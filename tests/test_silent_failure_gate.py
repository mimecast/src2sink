"""OI-36: a detection path may not fail to empty without saying so.

§6 of the open issues has carried this principle since 1.1.0:

> a detection path that fails to empty without emitting a signal

It was then violated at least six more times, in releases that shipped after it
was written — `OI-18`, `OI-13`, `OI-31`, three separate Kotlin gaps inside
`OI-17`, and later `OI-33`, `OI-37`, `OI-38` and `OI-39`. Every one produced a
well-formed output and a successful exit. Six were found by building something
else on top and noticing the foundation was empty.

**A principle with nothing enforcing it is a statement of intent.** This is the
enforcement. It does not fix the handlers — that is behaviour change and follows
separately. It fixes the thing that let them accumulate: nothing required a
silent failure to be a decision rather than an accident.

The rule: an exception handler whose entire body discards the error must either
record something, or be listed below with a reason. Listing is not a defeat —
skipping one unreadable file among thousands is correct, and the per-repo
bulkhead (`TA-001`) deliberately isolates failures. The point is that silence
becomes a choice someone made and signed, rather than the default.

**Why the gate and not the sweep.** The handlers can be fixed one at a time and
each is cheap. What could not be fixed one at a time is the absence of a rule,
and every week without one adds more. `OI-39` is the argument: it was not a
handler at all — an over-broad regex silently excluded whole repositories — so
the real surface is wider than any list of `except` blocks, and the discipline
has to be a habit rather than a patch.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "src2sink"

# Calls that count as emitting a signal. A handler doing any of these has told
# somebody, which is all this gate asks.
_SIGNAL_NAMES = frozenset({
    "append",        # summary.notes / notes.append(...)
    "warning", "warn", "error", "exception", "critical", "info",
    "print",
    "add_note",
})

# Handlers where discarding the error is correct, with the reason. These are
# predicates and path arithmetic: the fallback value *is* the answer the caller
# acts on, so there is nothing to report. Plus three documented designs whose
# fallback is the published contract.
_SIGNAL_NOT_NEEDED: dict[str, str] = {
    "src2sink.safe_paths:resolve_within": (
        "refusing a path outside the root is the function's purpose; returning "
        "None is the signal and every caller acts on it"
    ),
    "src2sink.safe_paths:is_escaping_symlink": "as above; the predicate's answer is the signal",
    "src2sink.checkout_scan:_is_skipped": (
        "path arithmetic on a path outside the root; the fallback treats it as "
        "not skipped, which is the conservative direction"
    ),
    "src2sink.repo_utils:is_skipped_path": "as above, the original of that predicate",
    "src2sink.repo_utils:_rel_to_parent": "relative-path arithmetic; None means 'outside', which is the answer",
    "src2sink.repo_utils:_rel_to_root": (
        "relative-path arithmetic against the repos root; None means the path\n"
        "        lies outside it, which is the answer the caller wanted"
    ),
    "src2sink.aggregators.openapi_discovery:repo_from_under_repos": (
        "maps a spec path to its owning repo; a path outside the checkout has\n"
        "        no owner, and returning None is how the caller skips it"
    ),
    "src2sink.trace:_is_in_traces_dir": "as above, deciding whether to refresh the index",
    "src2sink.build_metabase_v2:repo_relpath": (
        "the same arithmetic when recording a node's file; a path that will\n"
        "        not relativise falls back to the raw path rather than vanishing"
    ),
    "src2sink.index_store:open_index": (
        "a missing, stale or corrupt index is a cache miss by design (OI-15). "
        "The caller falls back to computing from records, so the answer is "
        "unchanged and only the cost differs"
    ),
    "src2sink.index_store:fleet_signature": (
        "an unreadable record is folded into the signature as 'missing', which "
        "invalidates the cache — the conservative direction, and recorded in the "
        "signature itself"
    ),
    "src2sink.build_metabase_v2:_existing_record_is_current": (
        "an unreadable prior record means 'not current', so the repo is "
        "re-scanned. The fallback costs work rather than hiding a finding"
    ),
    "src2sink.limits:_close": "the per-repo bulkhead's teardown (TA-001); the parent reports the outcome",
    "src2sink.limits:_collect": "as above — a crashed worker is reported as an error row by the parent",
    "src2sink.limits:_entry": "as above, the worker side of the same bulkhead",
}

# The `OI-36` debt, frozen at the level found when the gate was written.
#
# **This list is a baseline, not an endorsement.** Each entry discards an error
# without recording anything, and several are known to be the exact defect the
# issue describes — the four dependency parsers are `OI-18` in four more places,
# and the three `ts_extractors` handlers are the `OI-17` foundation, where a
# parse failure means a file takes part in no path and the answer is "nothing
# reaches a sink here", stated with full confidence.
#
# They are listed rather than fixed because fixing them is behaviour change and
# was deliberately scoped out of 3.0.0. What the gate buys today is that the list
# **cannot grow**: a new silent handler fails the build until someone either
# emits a signal or argues for an addition. The ratchet below then requires it to
# shrink over time.
_KNOWN_SILENT: frozenset[str] = frozenset({
    "src2sink.aggregators.api_client_discovery:_load_bindings",
    "src2sink.aggregators.api_client_discovery:_load_discovered",
    "src2sink.aggregators.library_source_map:fix_flagged_mappings",
    "src2sink.aggregators.library_source_map:generate_library_source_map",
    "src2sink.aggregators.library_source_map:load_library_source_map",
    "src2sink.aggregators.openapi_discovery:discover_helm_hosts",
    "src2sink.aggregators.openapi_discovery:discover_openapi_specs",
    "src2sink.aggregators.payload_producers:_read_capped",
    "src2sink.aggregators.taint_buckets:collect_taint_buckets",
    "src2sink.aggregators.traces_index:_parse_trace_file",
    "src2sink.build_metabase_v2:_limits_hit_summary",
    "src2sink.build_metabase_v2:_load_v2_jsons",
    "src2sink.build_metabase_v2:_read_json",
    "src2sink.build_metabase_v2:_rederive_record",
    "src2sink.build_metabase_v2:_scan_repo_files",
    "src2sink.build_metabase_v2:_tool_version",
    "src2sink.build_metabase_v2:process_one_v2",
    "src2sink.build_metabase_v2:safe_read_text",
    "src2sink.dependencies:_npm_lock_versions",
    "src2sink.dependencies:_python_lock_versions",
    "src2sink.dependencies:parse_npm_dependencies",
    "src2sink.dependencies:parse_python_dependencies",
    "src2sink.extractors.ts_extractors:extract_method_declarations",
    "src2sink.extractors.ts_extractors:extract_tree_sitter_calls",
    "src2sink.extractors.ts_extractors:extract_type_declarations",
    "src2sink.graph_common:iter_v2_repo_records",
    "src2sink.graph_common:load_one_v2_repo_record",
    "src2sink.known_api_clients:load_api_client_bindings",
    "src2sink.library_taint_java:scan_java_public_api",
    "src2sink.maven:_parse",
    "src2sink.prescreen:load_indicators",
    "src2sink.record_fleet_baseline:count_fleet_families",
    "src2sink.repo_utils:_index_gradle",
    "src2sink.repo_utils:_index_npm",
    "src2sink.repo_utils:_iter_manifests",
    "src2sink.repo_utils:_read_cargo_identity",
    "src2sink.repo_utils:_read_composer_identity",
    "src2sink.repo_utils:_read_dotnet_project_identity",
    "src2sink.repo_utils:_read_pom_identity",
    "src2sink.repo_utils:_read_pyproject_identity",
    "src2sink.repo_utils:_read_setup_cfg_identity",
    "src2sink.repo_utils:parse_package_json_dependencies",
    "src2sink.repo_utils:safe_read_text",
    "src2sink.trace:_literal_hits_in_file",
})


def _module_name(path: Path) -> str:
    """Dotted module name for a file in the package."""
    rel = path.relative_to(_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _emits_signal(node: ast.AST) -> bool:
    """Whether a handler body does anything that tells somebody."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Raise):
            return True
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "attr", getattr(sub.func, "id", ""))
            if name in _SIGNAL_NAMES:
                return True
    return False


def _enclosing_function(tree: ast.AST, handler: ast.ExceptHandler) -> str:
    """The function a handler sits in, for the exemption key."""
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and handler in list(
            ast.walk(fn)
        ):
            return fn.name
    return "<module>"


def _silent_handlers() -> dict[str, list[int]]:
    """Every handler that discards its error, keyed `module:function`."""
    found: dict[str, list[int]] = {}
    for path in sorted(_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _module_name(path)
        for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
            if _emits_signal(handler):
                continue
            key = f"{module}:{_enclosing_function(tree, handler)}"
            found.setdefault(key, []).append(handler.lineno)
    return found


def test_every_silent_handler_is_a_decision() -> None:
    """A handler that discards an error must record something or be listed.

    This is the whole of `OI-36`. The failure mode it guards is not a crash — it
    is a well-formed output, a successful exit, and nothing in it that says a
    detection path found nothing because it broke.
    """
    unlisted = sorted(set(_silent_handlers()) - set(_SIGNAL_NOT_NEEDED) - _KNOWN_SILENT)
    assert not unlisted, (
        "these handlers discard an exception without recording anything:\n  "
        + "\n  ".join(
            f"{k} (line{'s' if len(_silent_handlers()[k]) > 1 else ''} "
            f"{', '.join(str(n) for n in _silent_handlers()[k])})"
            for k in unlisted
        )
        + "\n\nEmit a signal — a note on the summary, a warning, a counter — "
        "so a caller can tell 'found nothing' from 'broke'. If silence is "
        "genuinely right, add it to _SIGNAL_NOT_NEEDED with the reason.\n\n"
        "Adding to _KNOWN_SILENT is not the answer: that list is frozen debt "
        "and must only shrink.\n"
        "See docs/issues/src2sink-open-issues.md §6 and `OI-36`."
    )


def test_no_exemption_is_stale() -> None:
    """An exemption for a handler that has moved hides the next gap.

    The same failure as a hand-maintained watch list: it drifts, and the drift is
    invisible because the gate stays green.
    """
    known = set(_SIGNAL_NOT_NEEDED) | _KNOWN_SILENT
    stale = sorted(known - set(_silent_handlers()))
    assert not stale, (
        "these exemptions no longer match a silent handler — the code changed "
        f"and the reason was not revisited: {stale}"
    )


def test_every_exemption_states_a_reason() -> None:
    """A bare entry is an undocumented hole, which is what this gate is against."""
    thin = sorted(k for k, v in _SIGNAL_NOT_NEEDED.items() if len(v.strip()) < 30)
    assert not thin, f"exemptions need a reason, not a placeholder: {thin}"


def test_the_gate_can_actually_fail() -> None:
    """A gate that cannot fail is decoration.

    `OI-36` exists because a *principle* with no enforcement was violated for
    four releases. An enforcement that cannot fire would be the same mistake one
    level up, so the detector is exercised against a handler it must catch.
    """
    silent = ast.parse("try:\n    x()\nexcept OSError:\n    return []\n")
    handler = next(n for n in ast.walk(silent) if isinstance(n, ast.ExceptHandler))
    assert not _emits_signal(handler)

    for loud in (
        "try:\n    x()\nexcept OSError:\n    notes.append('could not read')\n",
        "try:\n    x()\nexcept OSError:\n    raise\n",
        "try:\n    x()\nexcept OSError:\n    log.warning('nope')\n",
        "try:\n    x()\nexcept OSError:\n    print('nope')\n",
    ):
        h = next(n for n in ast.walk(ast.parse(loud)) if isinstance(n, ast.ExceptHandler))
        assert _emits_signal(h), f"should count as a signal:\n{loud}"


def test_the_debt_only_shrinks() -> None:
    """`_KNOWN_SILENT` is a ratchet, not a parking space.

    The gate's value today is that the list cannot grow. Its value over time is
    that it must come down — otherwise `OI-36` becomes a documented problem
    rather than a fixed one, which is precisely the state §6 was already in when
    it got violated four more times.
    """
    still_silent = set(_silent_handlers())
    assert _KNOWN_SILENT <= still_silent | set(_SIGNAL_NOT_NEEDED), (
        "an entry left _KNOWN_SILENT without the list being updated — good news, "
        "but shrink the frozen set so the ratchet holds the gain"
    )
    assert len(_KNOWN_SILENT) <= 44, (
        f"the OI-36 debt grew to {len(_KNOWN_SILENT)}; it is a ratchet and only "
        "goes down"
    )
