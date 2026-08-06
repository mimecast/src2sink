"""TA-005 — bounded-regex tests (threat-model D-2).

Every regex the extractors run against untrusted scanned-repo content must
resolve in time roughly linear in input size — a pattern with nested/overlapping
quantifiers (e.g. ``(a+)+``) can blow up exponentially on a crafted input and
hang a worker. ``limits.map_with_timeout`` (TA-001) is the last-resort backstop
for that, but a bounded-regex test catches the root cause directly and fails
fast with a clear "which pattern" signal instead of a bulkhead kill.

Strategy: collect every ``re.Pattern`` reachable from the extractor pattern
tables (module-level lists/dicts/tuples of compiled regexes), then run each
one against several adversarial payloads sized to make super-linear behaviour
obvious (a quadratic or worse pattern over ~100k chars would blow the budget
by orders of magnitude; every pattern here is expected to finish in
milliseconds). A second test drives the same payloads through the real
``extract_from_file`` pipeline for an end-to-end check.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from src2sink import (
    dependencies,
    maven,
    constants,
    graph_common,
    internal_groups as internal_groups_mod,
    library_taint_java,
    repo_utils,
    sanitize,
    trace_batch,
    vocabulary,
)
from src2sink.aggregators import (
    openapi_discovery,
    payload_producers,
    pii_touchpoint_utils,
    traces_index,
)
from src2sink.extractors import config as config_extractor
from src2sink.extractors import ast_walk, http_out, patterns, regex_extractors, symbols
from src2sink.extractors.file_context import FileExtractionContext
from src2sink.internal_groups import DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS
from src2sink import build_metabase_v2

# Per-pattern wall-clock budget. Every pattern here is a simple/linear regex;
# a nested-quantifier regression would take vastly longer than this on the
# payload sizes below, while legitimate linear patterns finish in ms.
BUDGET_SECONDS = 2.0

_PAYLOAD_SIZE = 100_000


def _adversarial_payloads() -> list[str]:
    """Payload shapes that defeat a `prefix .* required-literal` style pattern.

    Each omits the literal the pattern is looking for, forcing the greedy/lazy
    quantifier to scan to the end of the string before failing — the classic
    trigger for quadratic-or-worse regex blowups.
    """
    return [
        # Long run of quote characters — defeats `[^"']*["']` style patterns.
        '"' * _PAYLOAD_SIZE,
        # Unterminated string literal — defeats `["\'][^"\']*...["\']` patterns.
        '"' + "a" * _PAYLOAD_SIZE,
        # Deeply nested/unclosed parens and brackets.
        "(" * _PAYLOAD_SIZE,
        "[" * _PAYLOAD_SIZE,
        # Realistic-shaped near-miss: many short "calls" that almost match
        # common call-site patterns but never complete one.
        "foo(" * (_PAYLOAD_SIZE // 4),
        # Long line with no newline at all (stresses line-oriented scanning).
        "x" * _PAYLOAD_SIZE,
    ]


def _iter_regexes(*modules) -> list[re.Pattern[str]]:
    """Collect every distinct compiled regex reachable from these modules' globals."""
    seen: dict[int, re.Pattern[str]] = {}

    def walk(value: object) -> None:
        if isinstance(value, re.Pattern):
            seen[id(value)] = value
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    for module in modules:
        for value in vars(module).values():
            walk(value)
    return list(seen.values())


# Named so the completeness test below can check nothing has been left out.
_HARVESTED_MODULES = (
    # constants is harvested at its definition site so TEST_PATH_RX stays ReDoS-tested
    # without relying on a re-export elsewhere (which ruff would flag as unused).
    patterns, http_out, regex_extractors, graph_common, vocabulary, library_taint_java,
    constants,
    # symbols holds the identifier/assignment patterns that used to live in
    # http_out; they run against untrusted source just the same after the move.
    symbols,
    # Harvested as a whole module rather than by naming _GRADLE_DEP_RX: every
    # manifest pattern here reads untrusted repo content, and importing one by
    # name meant the version-catalog patterns added later were silently outside
    # this gate. Whole-module harvesting fails safe as patterns are added.
    build_metabase_v2,
    # Manifest and lockfile patterns, run against untrusted repo content exactly
    # as the Gradle ones above are. Harvested as a module for the same reason.
    dependencies, maven,
    # Type-name matching over scanned source, same as every pattern above.
    ast_walk,
    # Added by the completeness check below, which found nine modules defining
    # module-level regexes that had never been inside this gate at all.
    config_extractor,
    internal_groups_mod,
    openapi_discovery,
    payload_producers,
    pii_touchpoint_utils,
    repo_utils,
    sanitize,
    trace_batch,
    traces_index,
)
ALL_REGEXES = _iter_regexes(*_HARVESTED_MODULES)


@pytest.mark.watchdog(60)
def _param_id(index: int, pattern: str) -> str:
    """A pytest node id that can be selected on the command line.

    Using the raw regex source as the id put `[`, `|`, `\\` and truncated groups
    into node ids, which then could not be passed back to pytest — anything that
    selects tests by node id (mutation runs, CI shard splitters, `--last-failed`)
    fails to resolve them. The pattern itself is still in the assertion message,
    so a failure names it.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "", pattern)[:24]
    return f"rx{index:03d}-{slug}" if slug else f"rx{index:03d}"


@pytest.mark.parametrize(
    "rx",
    ALL_REGEXES,
    ids=[_param_id(i, p.pattern) for i, p in enumerate(ALL_REGEXES)],
)
def test_pattern_is_bounded_on_adversarial_input(rx: re.Pattern[str]) -> None:
    for payload in _adversarial_payloads():
        start = time.monotonic()
        list(rx.finditer(payload))
        elapsed = time.monotonic() - start
        assert elapsed < BUDGET_SECONDS, (
            f"{rx.pattern!r} took {elapsed:.2f}s on a {len(payload)}-char "
            "adversarial payload — possible catastrophic backtracking"
        )


@pytest.mark.watchdog(60)
def test_internal_group_defaults_are_bounded() -> None:
    """The (user-configurable) internal-group regexes also run against untrusted coords."""
    for pattern_str in DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS:
        rx = re.compile(pattern_str)
        for payload in _adversarial_payloads():
            start = time.monotonic()
            rx.match(payload)
            elapsed = time.monotonic() - start
            assert elapsed < BUDGET_SECONDS, (
                f"{pattern_str!r} took {elapsed:.2f}s — possible catastrophic backtracking"
            )


# The regex-only extraction passes from extract_from_file (D-2's scope). The
# tree-sitter pass is excluded on purpose: a slow/hostile *parse* is a
# separate, already-mitigated concern (D-1's bulkhead / TA-001), not a
# regex-boundedness one, and forcing a tight bound on it here would just
# fight that existing architecture.
_REGEX_EXTRACTION_PASSES = (
    regex_extractors.extract_http_inbound,
    regex_extractors.extract_sql_string_sources,
    regex_extractors.extract_file_sinks,
    regex_extractors.extract_api_client_imports,
    regex_extractors.extract_http_outbound,
    regex_extractors.extract_path_constants,
    regex_extractors.extract_queue_io,
    regex_extractors.extract_crypto_and_auth,
    regex_extractors.extract_pii_field_declarations,
    regex_extractors.extract_data_class_field_declarations,
    regex_extractors.extract_raw_sql_field_markers,
    regex_extractors.extract_pii_sinks,
)


@pytest.mark.watchdog(60)
@pytest.mark.parametrize("language", ["java", "python", "javascript", "go"])
def test_regex_extraction_pipeline_is_bounded_on_adversarial_source(language: str) -> None:
    """End-to-end: every regex extraction pass over one hostile file, per language."""
    source = "\n".join(_adversarial_payloads())
    ctx = FileExtractionContext(repo_id="acme/hostile", rel_path="Hostile.txt", language=language, source=source)
    start = time.monotonic()
    for pass_fn in _REGEX_EXTRACTION_PASSES:
        pass_fn(ctx)
    elapsed = time.monotonic() - start
    assert elapsed < BUDGET_SECONDS * 4, (
        f"regex extraction passes ({language}) took {elapsed:.2f}s on adversarial source"
    )


# ---------------------------------------------------------------------------
# The gate must not silently stop covering things
# ---------------------------------------------------------------------------

def _modules_defining_patterns() -> set[str]:
    """Return src2sink modules that define a module-level compiled regex."""
    import ast

    found: set[str] = set()
    root = Path(__file__).resolve().parent.parent / "src2sink"
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            if not targets:
                continue
            src = ast.unparse(node.value) if node.value is not None else ""
            if "re.compile" in src:
                found.add(str(path.relative_to(root.parent)))
                break
    return found


def test_every_module_defining_patterns_is_harvested() -> None:
    """A module outside the harvest list is silently outside this gate.

    `_iter_regexes` walks the globals of the modules it is *given*, so a pattern
    living anywhere else is unguarded and nothing says so. This has already
    happened twice: once when the symbol-table patterns moved to
    `extractors/symbols.py`, and once when version-catalog patterns were added to
    `build_metabase_v2` — which was harvested by *name* (`_GRADLE_DEP_RX`) rather
    than as a module, so later additions fell outside.

    Neither was caught by a failing test. Coverage was unchanged, the suite was
    green, and the gate simply stopped covering three patterns each time.
    """
    harvested = {
        f"src2sink/{m.__name__.split('src2sink.')[-1].replace('.', '/')}.py"
        for m in _HARVESTED_MODULES
    }
    unharvested = _modules_defining_patterns() - harvested
    assert not unharvested, (
        "these modules define module-level regexes but are not harvested by "
        f"_iter_regexes, so their patterns are outside TA-005: {sorted(unharvested)}. "
        "Add the module to _HARVESTED_MODULES."
    )
