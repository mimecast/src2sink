#!/usr/bin/env python3
"""Mutation gate — every catalogued defect must still break a test.

A passing test suite proves the code does what the tests say. It does not prove
the tests would *notice* if the code stopped doing it: an assertion on the wrong
field, an over-broad `assert nodes`, or a fixture that never exercises the branch
all pass just as green. This gate answers the other question by reintroducing
known defects one at a time and requiring the suite to fail.

Each catalogue entry is an exact source substitution plus the test selector that
must catch it. A mutant the selector does not kill is a missing assertion, not a
tolerable gap — the fix is to add the test, never to widen the catalogue.

The catalogue is *curated*, not generated: every entry is a defect someone chose
to care about, usually one that shipped. So the threshold is 100% — killing 21 of
22 means one real defect is undetectable, and a percentage would not say which.
Generated sweeps (`uvx mutmut@3.7.0`) are the discovery tool that feeds this file;
their survivors are triaged by hand and transcribed here. See
`docs/plans/open-issues-fix-plan.md` §5.

Usage:
    python scripts/mutation_check.py                    # run the whole catalogue
    python scripts/mutation_check.py --only OI7-M2      # iterate on one mutant
    python scripts/mutation_check.py --changed-only     # only mutants in the working diff
    python scripts/mutation_check.py --summary $GITHUB_STEP_SUMMARY
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # nosec B404 - runs pytest on a copy of this repo; no external input.
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Copied into each mutant's sandbox. Kept minimal: the suite must run, but a
# mutant should never be able to touch the real working tree.
_SANDBOX_CONTENTS = ("src2sink", "tests", "pyproject.toml")

# A mutant may hang where the original terminated (that is itself a defect worth
# catching), so every run is bounded.
_MUTANT_TIMEOUT_S = 120

# Guardrail for the whole-catalogue runtime in `make ci`. One selector costs
# ~0.8s, so this is roughly 100s. Raising it is a deliberate decision, not a
# side effect of appending entries — see the plan's budget discussion.
_MAX_CATALOGUE_SIZE = 120


@dataclass(frozen=True)
class Mutant:
    """One catalogued defect: a source substitution and the test that must catch it."""

    id: str
    file: str
    old: str
    new: str
    selector: str
    note: str


# ---------------------------------------------------------------------------
# Catalogue. Append; never trim. Group by the work item that introduced them.
# ---------------------------------------------------------------------------

CATALOGUE: tuple[Mutant, ...] = (
    # --- OI-7: the sql family must not match on method name alone -----------
    Mutant(
        id="OI7-M1",
        file="src2sink/extractors/ts_extractors.py",
        old="    if not (has_hint or receiver_is_database(receiver) or file_sql_evidence):\n        return\n",
        new="",
        selector="tests/test_sql_sink_evidence.py",
        note="Evidence gate removed entirely — restores the 1.1.0 name-only match.",
    ),
    Mutant(
        id="OI7-M2",
        file="src2sink/extractors/patterns.py",
        old="    return bool(SQL_LITERAL_RX.search(source) or SQL_DB_IMPORT_RX.search(source))",
        new='    return "sql" in source.lower()',
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "File evidence loosened to the bare token `sql` — the exact trap OI-7 "
            "describes, which re-admits an HTTP proxy with a `sql` field."
        ),
    ),
    Mutant(
        id="OI7-M3",
        file="src2sink/extractors/patterns.py",
        old="    if trailing.lower() in SQL_RECEIVER_NAMES:\n        return True",
        new="    if trailing.lower() in SQL_RECEIVER_NAMES:\n        return True\n    return True",
        selector="tests/test_sql_sink_evidence.py",
        note="Receiver check always passes — every receiver looks like a database.",
    ),
    Mutant(
        id="OI7-M4",
        file="src2sink/extractors/patterns.py",
        old="    return any(\n        a + b in SQL_RECEIVER_NAMES for a, b in zip(tokens, tokens[1:])\n    )",
        new="    return False",
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "Adjacent-token-pair matching dropped, so `jdbcTemplate` (tokens "
            "jdbc+template) stops resolving — the recall half of the gate."
        ),
    ),
    Mutant(
        # Re-derived when OI-10 rewrote sql_parameterisation. The original snippet
        # (`if not statements: return "unknown"`) is gone; the defect it guarded
        # against — a call with no attributable statement being given a definite
        # posture — now lives at the fallback's unknown return.
        id="OI7-M5",
        file="src2sink/extractors/patterns.py",
        old='        if len(candidates) != 1:\n            return "unknown"',
        new='        if len(candidates) != 1:\n            return "parameterised"',
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "A call with no attributable statement reported as safe rather than "
            "unknown — the sharpest form of claiming more than the evidence carries."
        ),
    ),
    Mutant(
        id="OI7-M6",
        file="src2sink/extractors/patterns.py",
        old=r'SQL_PLACEHOLDER_RX = re.compile(r"\?|:[A-Za-z_]\w{0,63}|%\(?[a-z_]*\)?s|\$\d{1,3}")',
        new=r'SQL_PLACEHOLDER_RX = re.compile(r":[A-Za-z_]\w{0,63}|%\(?[a-z_]*\)?s|\$\d{1,3}")',
        selector="tests/test_sql_sink_evidence.py",
        note="JDBC `?` placeholder dropped — a parameterised query reads as raw.",
    ),
    # --- OI-8: SQL assembled by formatting must be detected -----------------
    Mutant(
        id="OI8-M1",
        file="src2sink/extractors/patterns.py",
        old=r'    body = rf"[^{quote}\n]{{0,{_MAX_SQL_LITERAL}}}"',
        new='    body = rf"[^\\"\'\\n]{{0,{_MAX_SQL_LITERAL}}}"',
        selector="tests/test_sql_source_construction.py",
        note=(
            "Literal body excludes both quote characters again, so "
            "`\"… ref = '\" + ref` — the canonical injection shape — stops matching."
        ),
    ),
    Mutant(
        id="OI8-M2",
        file="src2sink/extractors/patterns.py",
        old='            (re.compile(rf"(?:String|MessageFormat)\\.format\\s*\\(\\s*{lit}"), "format-call"),\n',
        new="",
        selector="tests/test_sql_source_construction.py",
        note="`String.format`/`MessageFormat.format` coverage removed.",
    ),
    Mutant(
        id="OI8-M3",
        file="src2sink/extractors/patterns.py",
        old='            (re.compile(rf"{q}{body}?\\b{_SQL_KW}\\b{body}?{_INTERPOLATION}"), "template"),\n',
        new="",
        selector="tests/test_sql_source_construction.py",
        note=(
            "Keyword-then-interpolation dropped, reinstating 1.1.0's requirement "
            "that the interpolation precede the SQL keyword."
        ),
    ),
    Mutant(
        id="OI8-M4",
        file="src2sink/extractors/patterns.py",
        old='            (re.compile(rf"(?:String|MessageFormat)\\.format\\s*\\(\\s*{lit}"), "format-call"),',
        new='            (re.compile(rf"(?:String|MessageFormat)\\.format\\s*\\(\\s*"), "format-call"),',
        selector="tests/test_sql_source_construction.py",
        note="Any format call treated as SQL — the format string need not carry a keyword.",
    ),
    Mutant(
        id="OI8-M5",
        file="src2sink/extractors/regex_extractors.py",
        # Re-derived when OI-11 moved the de-duplication into the `emit` helper.
        old="        if line in seen_lines:\n            return\n        seen_lines.add(line)\n",
        new="",
        selector="tests/test_sql_source_construction.py",
        note=(
            "Per-line de-duplication removed — overlapping patterns inflate one "
            "constructed statement into a cluster of findings."
        ),
    ),
    # --- OI-11: a base-query constant hides the concatenation appended to it -
    Mutant(
        id="OI11-M1",
        file="src2sink/extractors/symbols.py",
        old="            symbols[name] = value\n",
        new="",
        selector="tests/test_sql_source_construction.py tests/test_sql_sink_evidence.py",
        note="Symbol table always empty — no constant can be resolved.",
    ),
    Mutant(
        id="OI11-M2",
        file="src2sink/extractors/patterns.py",
        old="        lambda value: bool(SQL_LITERAL_RX.search(f'\"{value}\"')),",
        new="        lambda value: True,",
        selector="tests/test_sql_source_construction.py",
        note=(
            "Any constant recorded, not only SQL-shaped ones, so resolving "
            "`GREETING + name` manufactures SQL out of an ordinary string."
        ),
    ),
    Mutant(
        id="OI11-M3",
        file="src2sink/extractors/ts_extractors.py",
        old='            "parameterised": sql_parameterisation(call_text, ctx.source, sql_symbols),',
        new='            "parameterised": sql_parameterisation(call_text, ctx.source),',
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "Resolution wired to the source pass but not the posture — the "
            "half-finished plumbing that would let the two drift apart again, "
            "reporting the construction while still calling the sink safe."
        ),
    ),
    Mutant(
        id="OI11-M4",
        file="src2sink/extractors/symbols.py",
        old=r'    r"\b([A-Za-z_][A-Za-z0-9_]{0,63})\s*\+|\+\s*([A-Za-z_][A-Za-z0-9_]{0,63})\b"',
        new=r'    r"\b([A-Za-z_][A-Za-z0-9_]{0,63})\b|\+\s*([A-Za-z_][A-Za-z0-9_]{0,63})\b"',
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "Any *reference* to a SQL constant treated as construction, not only "
            "one joined by `+`, so a base query used verbatim stops being "
            "parameterised."
        ),
    ),
    # --- OI-1 / OI-1 companion: version prefixes are not route names --------
    Mutant(
        id="OI1-M1",
        file="src2sink/graph_common.py",
        old="        if s and not _VERSION_SEGMENT_RX.match(s) and s.lower() not in _GENERIC_SEGMENTS",
        new="        if s",
        selector="tests/test_graph_common.py tests/test_cross_repo_caller_coverage.py",
        note="Significance filtering removed — `/v1` becomes a destination again.",
    ),
    Mutant(
        id="OI1-M2",
        file="src2sink/graph_common.py",
        old="        if s and not _VERSION_SEGMENT_RX.match(s) and s.lower() not in _GENERIC_SEGMENTS",
        new="        if s and not _VERSION_SEGMENT_RX.match(s)",
        selector="tests/test_graph_common.py",
        note="Generic segments (`/api`, `/service`) treated as route names again.",
    ),
    Mutant(
        id="OI1-M3",
        file="src2sink/graph_common.py",
        old="    if not op or not ip:\n        return None\n    if op == ip:\n        return \"medium\"",
        new="    if not op or not ip:\n        return \"low\"\n    if op == ip:\n        return \"medium\"",
        selector="tests/test_graph_common.py tests/test_cross_repo_caller_coverage.py",
        note="A side with no significant segment matches weakly instead of not at all.",
    ),
    Mutant(
        id="OI1-M4",
        file="src2sink/graph_common.py",
        old="    if op == ip:\n        return \"medium\"",
        new="    if op == ip:\n        return \"high\"",
        selector="tests/test_graph_common.py",
        note=(
            "Version-stripped equality promoted to `high` — two services may "
            "legitimately version differently and both expose `/stock`, and these "
            "edges carry no host to disambiguate."
        ),
    ),
    Mutant(
        id="OI1-M5",
        file="src2sink/graph_common.py",
        old="    if longer[: len(shorter)] == shorter:\n        # One is a child route of the other: /stock/dispatch against /stock.\n        return \"medium\"",
        new="    if longer[: len(shorter)] == shorter:\n        return None",
        selector="tests/test_graph_common.py",
        note=(
            "Child-route (prefix) matching deleted — the regression the issue "
            "document's own proposed implementation would have introduced."
        ),
    ),
    Mutant(
        id="OI1-M6",
        file="src2sink/graph_common.py",
        old="        # common segment may name a sub-resource that many services expose.\n        return \"low\"",
        new="        return \"medium\"",
        selector="tests/test_graph_common.py",
        note="Tail-only overlap promoted to `medium`, level with a real route match.",
    ),
    Mutant(
        id="OI1-M7",
        file="src2sink/graph_common.py",
        old="    if not path_filter:\n        return True\n    c = normalize_path_template(candidate)",
        new="    if not path_filter:\n        return True\n    return path_templates_match(candidate, path_filter) is not None\n    c = normalize_path_template(candidate)",
        selector="tests/test_graph_common.py tests/test_trace_render.py",
        note=(
            "`--path` filtering delegated back to the routing predicate, silently "
            "emptying `trace --path /v1` (finding F2)."
        ),
    ),
    Mutant(
        id="OI2-M2",
        file="src2sink/graph_common.py",
        old="                -abs(len(cand_sig) - len(query_sig)),",
        new="                0,",
        selector="tests/test_graph_common.py",
        note=(
            "Specificity dropped entirely — the query then reaches parent and "
            "child routes of itself as readily as the route it names."
        ),
    ),
    Mutant(
        id="OI2-M3",
        file="src2sink/graph_common.py",
        old="                sorted(row for _label, rows in winners for row in rows),",
        new="                [row for _label, rows in winners for row in rows],",
        selector="tests/test_graph_common.py",
        note="Deterministic ordering removed — output depends on index build order.",
    ),
    Mutant(
        id="OI7-M7",
        file="src2sink/aggregators/taint_writers.py",
        old='        if (posture := r.get("detail", {}).get("parameterised")) in _PARAM_POSTURES\n        else "unknown"',
        new='        if (posture := r.get("detail", {}).get("parameterised")) in _PARAM_POSTURES\n        else "parameterised"',
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "An unrecognised posture filed under the safe-looking bucket in the "
            "SQL catalogue — the downstream half of the claim-more-than-you-know "
            "defect (OI-7, OI-10)."
        ),
    ),
    # --- OI-10: `parameterised` is a posture, not a safety verdict ----------
    Mutant(
        id="OI10-M2",
        file="src2sink/extractors/patterns.py",
        old='        candidates = SQL_LITERAL_RX.findall(source)\n        if len(candidates) != 1:\n            return "unknown"\n',
        new="",
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "Ambiguous file-level attribution allowed — several candidate "
            "statements, any one of which may be the executed one."
        ),
    ),
    Mutant(
        id="OI10-M3",
        file="src2sink/extractors/patterns.py",
        # Re-derived when OI-11 threaded the symbol table into the check.
        old='    if _statement_is_constructed(region, symbols):\n        return "mixed" if placeholders else "raw"',
        new='    if _statement_is_constructed(region, symbols):\n        return "parameterised" if placeholders else "raw"',
        selector="tests/test_sql_sink_evidence.py",
        note=(
            "`mixed` collapsed into `parameterised` — a concatenated statement "
            "carrying a placeholder read as safe, which is the whole of OI-10."
        ),
    ),
    Mutant(
        id="OI10-M4",
        file="src2sink/extractors/patterns.py",
        old="        SQL_PLACEHOLDER_RX.search(lit) for lit in _STRING_LITERAL_RX.findall(region)",
        new="        SQL_PLACEHOLDER_RX.search(lit) for lit in [region]",
        selector="tests/test_sql_sink_evidence.py tests/test_sql_source_construction.py",
        note=(
            "Placeholder search widened from string literals to the whole call "
            "text, so a ternary `?` or a `$1` in code counts as a bind parameter."
        ),
    ),
)


def _sandbox(tmp: Path) -> Path:
    """Materialise a throwaway copy of the tree so mutants never touch the real one."""
    root = tmp / "repo"
    root.mkdir()
    for item in _SANDBOX_CONTENTS:
        src = REPO_ROOT / item
        dst = root / item
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    return root


def _apply(root: Path, mutant: Mutant) -> None:
    """Apply one substitution, or raise if the snippet no longer matches.

    A stale snippet is a hard error rather than a skip: when the target line is
    refactored, someone has to restate which defect the mutant represented and
    confirm a test still catches it. Silently passing would retire the check
    without anyone deciding to.
    """
    path = root / mutant.file
    text = path.read_text(encoding="utf-8")
    count = text.count(mutant.old)
    if count != 1:
        raise SystemExit(
            f"\nMutant {mutant.id} no longer applies to {mutant.file}: "
            f"its snippet matched {count} times, expected exactly 1.\n"
            f"  Defect it represents: {mutant.note}\n"
            f"  Snippet sought:\n{_indent(mutant.old)}\n"
            "Re-derive the mutant against the current source, or delete the entry "
            "with a note in the plan's catalogue if the defect is now unreachable."
        )
    path.write_text(text.replace(mutant.old, mutant.new), encoding="utf-8")


def _indent(text: str, prefix: str = "    | ") -> str:
    """Indent a snippet for readable error output."""
    return "\n".join(prefix + line for line in text.splitlines())


def _run_selector(root: Path, selector: str) -> tuple[bool, str]:
    """Run a mutant's tests in its sandbox; return (tests_failed, output tail).

    Coverage is disabled: the project's ``addopts`` carry ``--cov-fail-under``,
    which a scoped selection cannot meet, and a coverage failure would count as a
    kill for entirely the wrong reason.
    """
    cmd = [
        sys.executable, "-m", "pytest", *selector.split(),
        "-q", "--no-cov", "-p", "no:cacheprovider", "-x",
    ]
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, sandboxed cwd.
            cmd, cwd=root, capture_output=True, text=True, timeout=_MUTANT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return True, f"timed out after {_MUTANT_TIMEOUT_S}s (counted as killed)"
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    return proc.returncode != 0, tail[-1] if tail else ""


def _changed_files() -> set[str]:
    """Return repo-relative paths differing from origin/main, for --changed-only.

    ``git`` is resolved to an absolute path rather than found on PATH at call time,
    so the check cannot be redirected by a shadowing binary earlier in PATH.
    """
    git = shutil.which("git")
    if git is None:
        print("git not found; --changed-only cannot resolve the diff", file=sys.stderr)
        return set()
    changed: set[str] = set()
    for rev in (["origin/main...HEAD"], []):
        proc = subprocess.run(  # nosec B603 - absolute path, fixed argv, no shell.
            [git, "diff", "--name-only", *rev],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        changed |= {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return changed


def main() -> int:
    """Run the catalogue and fail on any survivor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="run a single mutant by id")
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="only mutants whose target file differs from origin/main",
    )
    parser.add_argument("--summary", help="append a markdown summary to this file")
    args = parser.parse_args()

    if len(CATALOGUE) > _MAX_CATALOGUE_SIZE:
        print(
            f"Catalogue has {len(CATALOGUE)} entries, over the {_MAX_CATALOGUE_SIZE} "
            "budget for the `make ci` loop. Raise _MAX_CATALOGUE_SIZE deliberately "
            "or move the surplus behind --changed-only.",
            file=sys.stderr,
        )
        return 1

    selected = list(CATALOGUE)
    if args.only:
        selected = [m for m in selected if m.id == args.only]
        if not selected:
            print(f"No mutant with id {args.only!r}", file=sys.stderr)
            return 1
    if args.changed_only:
        changed = _changed_files()
        selected = [m for m in selected if m.file in changed]

    survivors: list[Mutant] = []
    print(f"Running {len(selected)} mutant(s)\n")
    for mutant in selected:
        with tempfile.TemporaryDirectory(prefix="src2sink-mutation-") as tmp:
            root = _sandbox(Path(tmp))
            _apply(root, mutant)
            killed, tail = _run_selector(root, mutant.selector)
        mark = "killed " if killed else "SURVIVED"
        print(f"  [{mark}] {mutant.id}  {mutant.file}  ({tail})")
        if not killed:
            survivors.append(mutant)

    print()
    if survivors:
        print(f"{len(survivors)} mutant(s) survived — the tests do not constrain this code:\n")
        for m in survivors:
            print(f"  {m.id}  {m.file}")
            print(f"      defect:   {m.note}")
            print(f"      selector: {m.selector} passed with the defect reintroduced")
            print("      fix:      add the assertion that catches it (do not delete the mutant)\n")
    else:
        print(f"All {len(selected)} mutant(s) killed.")

    if args.summary:
        lines = [
            "## Mutation gate\n",
            f"- catalogue: {len(CATALOGUE)} mutants (budget {_MAX_CATALOGUE_SIZE})\n",
            f"- run: {len(selected)}\n",
            f"- survivors: {len(survivors)}\n",
        ]
        lines += [f"  - `{m.id}` {m.file} — {m.note}\n" for m in survivors]
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.writelines(lines)

    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
