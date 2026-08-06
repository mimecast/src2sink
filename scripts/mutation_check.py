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
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Copied into each mutant's sandbox. Kept minimal: the suite must run, but a
# mutant should never be able to touch the real working tree.
# `scripts` is here because the gates are load-bearing code: a mutated gate that
# still passes means the gate was decorative. Tests reach them via sys.path, so
# they must be present in the sandbox for a mutant to have any effect.
_SANDBOX_CONTENTS = ("src2sink", "tests", "scripts", "pyproject.toml")

# A mutant may hang where the original terminated (that is itself a defect worth
# catching), so every run is bounded.
_MUTANT_TIMEOUT_S = 120

# Guardrails for the whole-catalogue runtime in `make ci`. Count alone turned out
# to be the wrong measure: most mutants cost ~1s, but one that breaks a *timeout*
# can only be killed by waiting for that timeout to expire, so the LIM-* entries
# cost 15-35s each. That is inherent to testing a bulkhead, not something to tune
# away — but it has to be visible rather than creeping, so slow mutants are named
# on every run.
_MAX_CATALOGUE_SIZE = 120
_SLOW_MUTANT_S = 5.0


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
        # Re-derived when classification moved out of _maybe_add_sql_sink and into
        # _sql_verdict over observations. Same defect, same guard, new home.
        id="OI7-M1",
        file="src2sink/derive.py",
        old="    if not _has_sql_evidence(detail, hint=hint):\n        return None\n",
        new="",
        selector="tests/test_sql_sink_evidence.py tests/test_sql_classifier.py",
        note="Evidence gate removed entirely — restores the 1.1.0 name-only match.",
    ),
    Mutant(
        id="OI18-M0",
        file="src2sink/maven.py",
        old='        return DET.fromstring(text)',
        new='        import re as _re\n'
            '        return DET.fromstring(\n'
            '            _re.sub(r\'xmlns(:\\w+)?="[^"]+"\', "", text, count=10)\n'
            '        )',
        selector="tests/test_maven_resolution.py",
        note=(
            "Namespaces stripped by regex again, leaving `xsi:schemaLocation` as "
            "an unbound prefix — so every POM an IDE or archetype emits fails to "
            "parse and returns no dependencies. Reported from the field against "
            "2.0.0; invisible to a suite whose fixtures were all bare <project>."
        ),
    ),
    Mutant(
        id="OI18-M1",
        file="src2sink/maven.py",
        old="        if id(dep) in managed_ids:\n            continue\n",
        new="",
        selector="tests/test_maven_resolution.py",
        note=(
            "dependencyManagement read as dependencies again, so the BOM is "
            "emitted as an edge to an artefact the code never calls."
        ),
    ),
    Mutant(
        id="OI18-M2",
        file="src2sink/maven.py",
        old="_MAX_PROPERTY_DEPTH = 8",
        new="_MAX_PROPERTY_DEPTH = 0",
        selector="tests/test_maven_resolution.py",
        note=(
            "Property expansion stops happening at all, so `${a}` never resolves "
            "— the bound is what makes a cycle terminate, and it must be used."
        ),
    ),
    Mutant(
        id="OI18-M3",
        file="src2sink/maven.py",
        old='    in_fleet = _find_parent_in_fleet(fleet_root, artifact)',
        new="    in_fleet = None",
        selector="tests/test_maven_resolution.py",
        note=(
            "Cross-repo parent resolution removed, so a POM inheriting from a "
            "parent in another scanned repo goes back to unresolved."
        ),
    ),
    Mutant(
        id="OI18-M4",
        file="src2sink/maven.py",
        old='            "version_kind": "resolved" if version else "unresolved",',
        new='            "version_kind": "resolved",',
        selector="tests/test_maven_resolution.py",
        note=(
            "An unresolvable version reported as resolved — the claim OI-18 "
            "exists to stop, in its purest form."
        ),
    ),
    Mutant(
        id="OI19-M1",
        file="src2sink/dependencies.py",
        old="    locked = _npm_lock_versions(repo_root)",
        new="    locked: dict[str, str] = {}",
        selector="tests/test_polyglot_dependencies.py",
        note=(
            "The npm lockfile stops overriding the manifest, so a resolved "
            "version is recorded as the range it came from — the assumption "
            "OI-19 exists to correct."
        ),
    ),
    Mutant(
        id="OI19-M2",
        file="src2sink/dependencies.py",
        old='            deps.append(_dep(name, spec, "range" if spec else "unresolved"))',
        new='            deps.append(_dep(name, spec, "resolved"))',
        selector="tests/test_polyglot_dependencies.py",
        note=(
            "A Python range recorded as though it were a resolved version, "
            "which is OI-18's defect reappearing in another ecosystem."
        ),
    ),
    Mutant(
        id="OI19-M3",
        file="src2sink/dependencies.py",
        old="    notes: list[str] = []\n    for manifest, ecosystem in sorted(UNPARSED_MANIFESTS.items()):",
        new="    notes: list[str] = []\n    for manifest, ecosystem in []:",
        selector="tests/test_polyglot_dependencies.py",
        note=(
            "An unparsed ecosystem stops saying so, making "
            "dependencies_internal: [] mean both 'none' and 'cannot read'."
        ),
    ),
    Mutant(
        id="OI17-M4",
        file="src2sink/extractors/ast_walk.py",
        old='            if name not in ("extends", "implements") and name not in out:',
        new="            if name not in out:",
        selector="tests/test_type_declarations.py",
        note=(
            "The Java keywords land in the supertype list, so a class reads as "
            "extending a type called `implements` — and interface resolution "
            "goes looking for it."
        ),
    ),
    Mutant(
        id="OI17-M5",
        file="src2sink/extractors/ast_walk.py",
        old='            _is_interface(node, language),',
        new="            False,",
        selector="tests/test_type_declarations.py tests/test_call_resolution.py",
        note=(
            "An interface reads as a class, so a call resolving to its bodiless "
            "method looks like a dead end rather than a prompt to expand to the "
            "implementations. Re-derived when `OI-17` step 3 moved the test into "
            "`_is_interface` to fix the Kotlin half of it; `OI17-M10` covers the "
            "Kotlin branch specifically."
        ),
    ),
    Mutant(
        id="OI17-M6",
        file="src2sink/extractors/ast_walk.py",
        old='        if child.type == "class_parameter":',
        new="        if False:",
        selector="tests/test_type_declarations.py",
        note=(
            "Kotlin constructor properties stop counting as fields, so the "
            "standard Spring shape loses every receiver type and Kotlin becomes "
            "resolvable only where a property sits in the body."
        ),
    ),
    Mutant(
        id="OI17-M1",
        file="src2sink/extractors/ts_extractors.py",
        old="            if start <= node.line <= end:",
        new="            if start <= node.line:",
        selector="tests/test_method_structure.py",
        note=(
            "Scope assignment stops checking the span end, so a class-level "
            "field is attributed to whichever method happens to follow it — a "
            "guessed scope, which is worse than an absent one."
        ),
    ),
    Mutant(
        id="OI17-M2",
        file="src2sink/extractors/ts_extractors.py",
        old="        key=lambda s: s[1] - s[0],",
        new="        key=lambda s: s[0] - s[1],",
        selector="tests/test_method_structure.py",
        note=(
            "Spans ordered widest-first, so a nested function's findings are "
            "attributed to its parent instead of to itself."
        ),
    ),
    Mutant(
        id="OI17-M3",
        file="src2sink/derive.py",
        old='    for key in ("enclosing_class", "enclosing_method"):',
        new="    for key in ():",
        selector="tests/test_method_structure.py",
        note=(
            "Derived nodes stop inheriting scope from the observation they came "
            "from, so a finding loses its method on the re-derive path where "
            "there is no extraction context to reassign it."
        ),
    ),
    Mutant(
        id="OI13-M1",
        file="src2sink/extractors/ast_walk.py",
        old='    if language == "kotlin":\n        return call_name_kotlin(source, node)\n',
        new="",
        selector="tests/test_ast_walk.py tests/test_oi13_kotlin_parity.py",
        note=(
            "Kotlin routed back to the Java walker, which requires a "
            "method_invocation node Kotlin never produces — so every Kotlin call "
            "site goes silently invisible again."
        ),
    ),
    Mutant(
        id="OI13-M3",
        file="src2sink/extractors/ast_walk.py",
        old='    if language == "kotlin":\n        # Kotlin has no `object` field: the receiver is the first child of the\n        # `navigation_expression` the call wraps (OI-13).\n        return call_receiver_kotlin(source, node)\n',
        new="",
        selector="tests/test_ast_walk.py tests/test_oi13_kotlin_parity.py",
        note=(
            "Kotlin calls lose their receiver, so classification falls back to "
            "file-scoped evidence and httpClient.execute becomes a SQL sink — "
            "OI-26 reappearing in the language that had no receiver."
        ),
    ),
    Mutant(
        id="OI21-M1",
        file="src2sink/derive.py",
        old='    if obs.family == "queue-sub" and obs.detail.get("direction") == "consume":',
        new='    if False:',
        selector="tests/test_entry_points.py",
        note=(
            "Queue consumers stop counting as entry points, so a whole class of "
            "service reports no way in — the case that would make OI-17 answer "
            "'no path' while looking certain."
        ),
    ),
    Mutant(
        id="OI21-M2",
        file="src2sink/derive.py",
        old='            "externally_triggered": obs.detail["externally_triggered"],',
        new='            "externally_triggered": True,',
        selector="tests/test_entry_points.py",
        note=(
            "A scheduled job reads as externally triggered, overstating what an "
            "attacker controls: the clock opens that door, not a caller."
        ),
    ),
    Mutant(
        id="OI21-M3",
        file="src2sink/derive.py",
        old="        if obs.family == \"entry-marker\":\n            key = (obs.file, detail[\"mechanism\"])\n            if key in seen_markers:\n                continue\n            seen_markers.add(key)\n",
        new="",
        selector="tests/test_entry_points.py",
        note=(
            "Marker dedup removed, so a gRPC service carrying both @GrpcService "
            "and extends ...ImplBase counts as two doors instead of one."
        ),
    ),
    Mutant(
        id="DRV-M1",
        file="src2sink/derive.py",
        old='    return (node.family, node.kind) in DERIVED_FAMILIES',
        new="    return node.family in {f for f, _ in DERIVED_FAMILIES}",
        selector="tests/test_derive_pass.py",
        note=(
            "Derived nodes keyed on family alone, so stripping for a re-derive "
            "also deletes the observed `sql`/`source` nodes that derivation does "
            "not rebuild."
        ),
    ),
    Mutant(
        id="OI26-M1",
        file="src2sink/derive.py",
        old='    if receiver_is_another_boundary(detail.get("receiver")):\n        return False\n',
        new="",
        selector="tests/test_oi26_receiver_scope.py",
        note=(
            "File-scoped evidence overrules the receiver again, so an HTTP client "
            "call in a file containing any SQL is a SQL execution sink — and can "
            "fabricate a raw-code-payload endpoint from it."
        ),
    ),
    Mutant(
        id="OI26-M2",
        file="src2sink/derive.py",
        old='    return bool(detail.get("file_sql_evidence", False))',
        new="    return False",
        selector="tests/test_oi26_receiver_scope.py tests/test_sql_sink_evidence.py",
        note=(
            "The unknown-receiver rescue is removed with the fix, so "
            "`runner.execute(STATEMENT)` in a SQL-bearing file is lost — "
            "over-tightening, the other way OI-26 can be got wrong."
        ),
    ),
    Mutant(
        id="OI26-M3",
        file="src2sink/extractors/patterns.py",
        old='    "ps",\n    "pstmt",\n',
        new="",
        selector="tests/test_oi26_receiver_scope.py",
        note=(
            "PreparedStatement abbreviations dropped from the receiver "
            "vocabulary, so tightening the file-scope rule silently withdraws "
            "real ps.execute() sinks."
        ),
    ),
    Mutant(
        id="OBS-M1",
        file="src2sink/extractors/unified.py",
        old="    derived_nodes, derived_edges = derive_from_observations(ctx.nodes)",
        new="    derived_nodes, derived_edges = [], []",
        selector="tests/test_sql_classifier.py tests/test_sql_sink_evidence.py",
        note=(
            "Classification never runs, so observations are recorded and no sql "
            "node is ever produced — the failure mode of splitting a pipeline."
        ),
    ),
    Mutant(
        id="OBS-M2",
        file="src2sink/derive.py",
        old='        if obs.family != "call-site":\n            continue\n',
        new="",
        selector="tests/test_sql_classifier.py",
        note=(
            "The classifier stops filtering to observations and reads every node, "
            "so any node carrying a `symbol` detail is re-classified as SQL."
        ),
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
        # Re-derived when posture moved onto the observation: it is now computed
        # once at observation time rather than inside the sink construction.
        old="            parameterised=sql_parameterisation(call_text, ctx.source, sql_symbols),",
        new="            parameterised=sql_parameterisation(call_text, ctx.source),",
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
    # --- OI-9: the outbound end of a SQL hop --------------------------------
    Mutant(
        id="OI9-M1",
        file="src2sink/extractors/ts_extractors.py",
        old="    if not ctx.http_out_sinks:\n        return\n",
        new="",
        selector="tests/test_sql_payload_out.py",
        note=(
            "The outbound-call precondition removed, so every data class "
            "declaring a `sql` field becomes a sink — the DTO-flooding mistake."
        ),
    ),
    Mutant(
        id="OI9-M2",
        file="src2sink/extractors/patterns.py",
        old='        rf"\\.\\s*set({alt})\\s*\\("        # body.setSql(x)\n',
        new="",
        selector="tests/test_sql_payload_out.py",
        note="Setter binding dropped — `body.setSql(x)` stops being recognised.",
    ),
    Mutant(
        id="OI9-M3",
        file="src2sink/extractors/patterns.py",
        old='    declared = frozenset(f for b in get_bindings() for f in b.payload_fields if f)',
        new="    declared = frozenset()",
        selector="tests/test_sql_payload_out.py",
        note=(
            "Binding-declared payload fields ignored, so a service declaring "
            "`payload_fields: [\"dql\"]` is neither recognised nor rated high."
        ),
    ),
    Mutant(
        id="OI9-M4",
        file="src2sink/extractors/ts_extractors.py",
        old='            confidence="high" if by_binding else "medium",',
        new='            confidence="high",',
        selector="tests/test_sql_payload_out.py",
        note=(
            "A generic vocabulary guess rated as highly as a binding "
            "declaration — claiming more than the evidence carries."
        ),
    ),
    Mutant(
        id="OI9-M5",
        file="src2sink/aggregators/taint_buckets.py",
        old='    "sql-payload-out": "sql_payload_out",\n',
        new="",
        selector="tests/test_sql_payload_out.py",
        note=(
            "Family not routed to a bucket: it exists in the per-repo JSON and "
            "nowhere a reviewer looks — the half-finished-plumbing case."
        ),
    ),
    # --- OI-2: a custom HTTP wrapper must not be invisible ------------------
    Mutant(
        id="OI2b-M1",
        file="src2sink/extractors/regex_extractors.py",
        old="            if has_route_constant is None:\n                has_route_constant = file_declares_a_route_constant(ctx.source)\n            if not has_route_constant:\n                continue\n",
        new="            continue\n",
        selector="tests/test_http_guard_evidence.py",
        note=(
            "Route-constant evidence removed, so a wrapper naming no HTTP "
            "library is invisible again — the whole of OI-2."
        ),
    ),
    Mutant(
        id="OI2b-M2",
        file="src2sink/extractors/regex_extractors.py",
        old="        if not file_guard.search(ctx.source):\n            if has_route_constant is None:",
        new="        if False:\n            if has_route_constant is None:",
        selector="tests/test_http_guard_evidence.py tests/test_cross_repo_caller_coverage.py",
        note=(
            "Guard bypassed entirely — `\\w*[Cc]lient.post(` then matches any "
            "Mapping-like helper in the fleet, which is what the guard is for."
        ),
    ),
    Mutant(
        id="OI2b-M3",
        file="src2sink/extractors/regex_extractors.py",
        old="            if _is_route_like_constant(m.group(1), m.group(2)):\n                return True",
        new="            return True",
        selector="tests/test_http_guard_evidence.py",
        note=(
            "Any string constant counts as a route, so `/config/app.yml` — a "
            "resource path — becomes HTTP evidence."
        ),
    ),
    Mutant(
        id="OI2a-M1",
        file="src2sink/extractors/http_out.py",
        old='    r"|MediaType|HttpStatus|Authorization|Bearer)\\b",',
        new='    r")\\b",',
        selector="tests/test_http_guard_evidence.py",
        note="Transport-agnostic Java tokens dropped from the file guard.",
    ),
    # --- OI-3: Gradle version catalogs -------------------------------------
    Mutant(
        id="OI3-M1",
        file="src2sink/build_metabase_v2.py",
        old='    return alias.replace("-", "").replace(".", "").replace("_", "").lower()',
        new="    return alias",
        selector="tests/test_gradle_version_catalogs.py",
        note=(
            "Alias normalisation dropped, so the catalog's "
            "`warehouse-service-client` never meets the script's "
            "`libs.warehouseServiceClient`."
        ),
    ),
    Mutant(
        id="OI3-M2",
        file="src2sink/build_metabase_v2.py",
        old='    return alias.replace("-", "").replace(".", "").replace("_", "").lower()',
        new='    return alias.replace("-", "").replace(".", "").replace("_", "")',
        selector="tests/test_gradle_version_catalogs.py",
        note="Case folding dropped — separators handled, capitalisation not.",
    ),
    Mutant(
        id="OI3-M3",
        file="src2sink/build_metabase_v2.py",
        old="        for alias, gid, aid in _CATALOG_DSL_RX.findall(text):\n            catalog.setdefault(_normalise_alias(alias), (gid, aid))\n",
        new="",
        selector="tests/test_gradle_version_catalogs.py",
        note="settings.gradle.kts `library(...)` catalogs no longer parsed.",
    ),
    Mutant(
        id="OI3-M4",
        file="src2sink/build_metabase_v2.py",
        old='                "kind": "internal" if is_internal_coordinate(gid, aid) else "external",',
        new='                "kind": "external",',
        selector="tests/test_gradle_version_catalogs.py",
        note=(
            "Resolved coordinates never classified internal, so they reach "
            "dependencies_internal — the discovery input — as nothing."
        ),
    ),
    Mutant(
        id="OI3-M5",
        file="src2sink/build_metabase_v2.py",
        old="        if unresolved:\n            notes.append(",
        new="        if False:\n            notes.append(",
        selector="tests/test_gradle_version_catalogs.py",
        note=(
            "Unresolved catalog references stop being reported — the dependency "
            "list degrades to empty with nothing saying so, which is the whole "
            "failure shape the 1.1.0 work set out to eliminate."
        ),
    ),
    # --- OI-4: discovery mines only one direction ---------------------------
    Mutant(
        id="OI4-M1",
        file="src2sink/aggregators/api_client_discovery.py",
        old="    _apply_demand_side(cands, records)\n",
        new="",
        selector="tests/test_demand_side_discovery.py",
        note=(
            "Demand-side pass removed — a hand-rolled caller is invisible again, "
            "and class_patterns goes back to being permanently empty."
        ),
    ),
    Mutant(
        id="OI4-M2",
        file="src2sink/aggregators/api_client_discovery.py",
        old="            if _is_binding_stamped(detail):\n                continue\n",
        new="",
        selector="tests/test_demand_side_discovery.py",
        note=(
            "Binding-stamped hops re-ingested as fresh evidence for the binding "
            "that created them — confidence inflates on every run."
        ),
    ),
    Mutant(
        id="OI4-M3",
        file="src2sink/aggregators/api_client_discovery.py",
        old="MAX_PATTERN_REPOS = 3",
        new="MAX_PATTERN_REPOS = 1000",
        selector="tests/test_demand_side_discovery.py",
        note=(
            "Distinctiveness check disabled, so a fleet-wide class name like "
            "`ApiClient` is proposed unflagged into an unguarded substring tier."
        ),
    ),
    Mutant(
        id="OI4-M4",
        file="src2sink/aggregators/api_client_discovery.py",
        old='            cand["discovery_method"] = "both"',
        new='            cand["discovery_method"] = "call-site"',
        selector="tests/test_demand_side_discovery.py",
        note=(
            "Agreement between the two directions no longer recorded, so the "
            "strongest candidates are indistinguishable from the weakest."
        ),
    ),
    Mutant(
        id="OI4-M5",
        file="src2sink/aggregators/api_client_discovery.py",
        old="                if target == consumer:\n                    continue\n",
        new="",
        selector="tests/test_demand_side_discovery.py",
        note="Self-edges proposed as client bindings — a repo calling itself.",
    ),
    # --- Tier A: security-critical modules (WI-10) --------------------------
    # Transcribed from a mutmut sweep. sanitize sat at 100% line coverage with 22
    # surviving mutants: the tests asked "is the dangerous thing gone" and never
    # "is the safe thing right", so any change to *what* a value is replaced with
    # went unnoticed.
    Mutant(
        id="SAN-M1",
        file="src2sink/sanitize.py",
        old='    text = _WHITESPACE_RX.sub(" ", text)\n    text = _CONTROL_RX.sub("", text)',
        new='    text = _WHITESPACE_RX.sub("  ", text)\n    text = _CONTROL_RX.sub("", text)',
        selector="tests/test_sanitize.py",
        note=(
            "Newlines collapse to something other than a single space. The cell "
            "stays structurally safe while the value a reader acts on is corrupted."
        ),
    ),
    Mutant(
        id="SAN-M2",
        file="src2sink/sanitize.py",
        old='    return text.replace("|", "\\\\|")',
        new='    return text.replace("|", "\\\\|\\\\|")',
        selector="tests/test_sanitize.py",
        note="Pipe escaped as something other than the Markdown escape sequence.",
    ),
    Mutant(
        id="SAN-M3",
        file="src2sink/sanitize.py",
        old="    text = _FENCE_RX.sub(lambda m: _INERT_GRAVE * len(m.group()), text)",
        new="    text = _FENCE_RX.sub(lambda m: _INERT_GRAVE, text)",
        selector="tests/test_sanitize.py",
        note=(
            "A fence is neutralised but its length is not preserved, so the "
            "snippet no longer reads as it did in the source."
        ),
    ),
    Mutant(
        id="SAN-M4",
        file="src2sink/sanitize.py",
        old='    text = _EMAIL_RX.sub("<redacted-email>", text)',
        new='    text = _EMAIL_RX.sub("<removed>", text)',
        selector="tests/test_sanitize.py",
        note=(
            "The redaction marker changes. Downstream readers grep for these "
            "exact strings, so the text is part of the contract, not a label."
        ),
    ),
    Mutant(
        id="SAN-M5",
        file="src2sink/sanitize.py",
        old='    return "<redacted-number>" if sum(c.isdigit() for c in token) >= 9 else token',
        new='    return "<redacted-number>" if sum(c.isdigit() for c in token) >= 8 else token',
        selector="tests/test_sanitize.py",
        note=(
            "The digit threshold moves, so ports, line numbers and dates start "
            "being redacted as identifiers — or PANs stop being."
        ),
    ),
    Mutant(
        id="SAN-M6",
        file="src2sink/sanitize.py",
        old="def for_mermaid_label(value: object, *, max_len: int = 40) -> str:",
        new="def for_mermaid_label(value: object, *, max_len: int = 41) -> str:",
        selector="tests/test_sanitize.py",
        note=(
            "Off-by-one in the documented default truncation length — invisible "
            "to an assertion that only checks `len(out) <= max_len`."
        ),
    ),
    # --- Tier A: the execution bulkhead (WI-10) -----------------------------
    Mutant(
        id="LIM-M1",
        file="src2sink/limits.py",
        old="    if proc.is_alive():\n        proc.kill()\n        proc.join()",
        new="",
        selector="tests/test_limits.py",
        note=(
            "Escalation to kill() removed. SIGTERM is a request a wedged or "
            "hostile worker can decline, and without the second step the scan "
            "hangs on that repo forever — the bulkhead's whole purpose."
        ),
    ),
    Mutant(
        id="LIM-M2",
        file="src2sink/limits.py",
        old="    if proc.is_alive():\n        proc.terminate()",
        new="    if True:\n        proc.terminate()",
        selector="tests/test_limits.py",
        note="An already-dead worker is signalled again, which can hit a recycled pid.",
    ),
    Mutant(
        id="LIM-M3",
        file="src2sink/limits.py",
        old="    workers = max(1, workers)",
        new="    workers = max(0, workers)",
        selector="tests/test_limits.py",
        note=(
            "`workers=0` reaches this from a CLI flag; without normalisation the "
            "dispatcher starts nothing and never terminates."
        ),
    ),
    Mutant(
        id="LIM-M4",
        file="src2sink/limits.py",
        old="        for r in running:\n            _kill(r[\"proc\"])\n            _close(r[\"conn\"])",
        new="        pass",
        selector="tests/test_limits.py",
        note=(
            "Cleanup on generator close removed, leaking a worker per abandoned "
            "iterator — the normal case when a consumer stops early."
        ),
    ),
    # --- Tier A: the malicious-content pre-screen (WI-10) --------------------
    Mutant(
        id="PRE-M1",
        file="src2sink/prescreen.py",
        old="    if head:\n        ratio = head.count(_REPLACEMENT_CHAR) / len(head)",
        new="    if True:\n        ratio = head.count(_REPLACEMENT_CHAR) / len(head)",
        selector="tests/test_prescreen.py",
        note=(
            "Empty-file guard removed: an empty file divides by zero and aborts "
            "the scan of a repo rather than skipping one file."
        ),
    ),
    Mutant(
        id="PRE-M2",
        file="src2sink/prescreen.py",
        old="_MAX_REPLACEMENT_RATIO = 0.10",
        new="_MAX_REPLACEMENT_RATIO = 0.50",
        selector="tests/test_prescreen.py",
        note=(
            "The binary-content threshold moves. A screen's thresholds are its "
            "whole behaviour, and the previous tests used ~98% replacement "
            "characters so anything from 1% to 97% passed them equally."
        ),
    ),
    Mutant(
        id="PRE-M3",
        file="src2sink/prescreen.py",
        old="            if len(line) > _MAX_LINE_BYTES:",
        new="            if len(line) >= _MAX_LINE_BYTES:",
        selector="tests/test_prescreen.py",
        note="Off-by-one on the minified-line cap: a line exactly at the limit is skipped.",
    ),
    Mutant(
        id="PRE-M4",
        file="src2sink/prescreen.py",
        old="_BINARY_SNIFF_CHARS = 8192",
        new="_BINARY_SNIFF_CHARS = 128",
        selector="tests/test_prescreen.py",
        note=(
            "The sniff window narrows, so binary files stop being recognised. "
            "The window is a deliberate cost trade-off, not an arbitrary number."
        ),
    ),
    Mutant(
        id="PRE-M5",
        file="src2sink/prescreen.py",
        old='                return f"matched configured indicator: {ind[:40]}"',
        new='                return f"matched configured indicator: {ind}"',
        selector="tests/test_prescreen.py",
        note="Operator-supplied text echoed into the reason unbounded.",
    ),
    # --- Tier B: config detection (WI-10) -----------------------------------
    Mutant(
        id="CFG-M1",
        file="src2sink/extractors/config.py",
        old='_CONFIG_SUFFIXES = frozenset({".properties", ".yml", ".yaml", ".env"})',
        new='_CONFIG_SUFFIXES = frozenset({".properties", ".yml", ".yaml"})',
        selector="tests/test_phase1_config.py",
        note=(
            "A format drops out of config detection. Everything downstream — "
            "data-store URLs, base URLs, credential-shaped keys — is only found "
            "in files this gate says yes to."
        ),
    ),
    Mutant(
        id="CFG-M2",
        file="src2sink/extractors/config.py",
        old="    return suffix in _CONFIG_SUFFIXES or path_name in CONFIG_FILE_NAMES",
        new="    return suffix in _CONFIG_SUFFIXES and path_name in CONFIG_FILE_NAMES",
        selector="tests/test_phase1_config.py",
        note=(
            "Only the six conventional Spring names are treated as config, so "
            "every other .yml/.properties in the fleet is skipped silently."
        ),
    ),
    # --- OI-1 / OI-1 companion: version prefixes are not route names --------
    Mutant(
        # Re-derived when OI-24 moved the equality shortcut below the filter and
        # OI-25 added the placeholder term; the defect is unchanged.
        id="OI1-M1",
        file="src2sink/graph_common.py",
        old="        and not _VERSION_SEGMENT_RX.match(s)\n"
            "        and s.lower() not in _GENERIC_SEGMENTS",
        new="",
        selector="tests/test_graph_common.py tests/test_cross_repo_caller_coverage.py"
                 " tests/test_path_match_significance.py",
        note="Significance filtering removed — `/v1` becomes a destination again.",
    ),
    Mutant(
        id="OI17-M7",
        file="src2sink/resolve.py",
        old="    if not table.is_interface.get(declared, False):",
        new="    if True:",
        selector="tests/test_call_resolution.py",
        note=(
            "Resolution stops at the declared type, so a call on an "
            "interface-typed field binds to the bodiless interface method and the "
            "chain ends there. The constructor-injected interface is the standard "
            "Spring shape, so this reports a confident dead end for most of the "
            "JVM fleet — and a confident dead end reads as a clean result."
        ),
    ),
    Mutant(
        id="OI17-M8",
        file="src2sink/resolve.py",
        old='        return "low" if self.ambiguous else _TIER_CONFIDENCE.get(self.tier, "low")',
        new='        return _TIER_CONFIDENCE.get(self.tier, "low")',
        selector="tests/test_call_resolution.py",
        note=(
            "An interface with several implementations reports `medium` for each, "
            "so a resolver that cannot say which one runs presents every guess as "
            "a moderately confident answer."
        ),
    ),
    Mutant(
        id="OI17-M9",
        file="src2sink/resolve.py",
        old="    if len(candidates) != 1:\n        return []",
        new="    if not candidates:\n        return []",
        selector="tests/test_call_resolution.py",
        note=(
            "T3 stops requiring the name to be unique, so a method declared on "
            "two unrelated classes resolves to whichever was indexed first — a "
            "guess between candidates presented as a resolution."
        ),
    ),
    Mutant(
        id="OI17-M10",
        file="src2sink/extractors/ast_walk.py",
        old='    return any(child.type == "interface" for child in node.children)',
        new="    return False",
        selector="tests/test_type_declarations.py tests/test_call_resolution.py",
        note=(
            "Kotlin interfaces stop being recognised, which is how this shipped in "
            "2.1.0: Kotlin has no `interface_declaration` node, so testing the node "
            "type marked every Kotlin interface as a class. Java kept passing "
            "throughout — the `OI-13` failure mode of a language being invisible."
        ),
    ),
    Mutant(
        id="OI17-M11",
        file="src2sink/build_metabase_v2.py",
        old='        or "library_hint" in n.detail            # sink-shaped: always kept',
        new="        or False",
        selector="tests/test_call_observations.py tests/test_characterization.py",
        note=(
            "The prune stops sparing sink-shaped calls, so `jdbcTemplate.query(...)` "
            "— which resolves to nothing declared in the repo, and is precisely the "
            "finding the tool exists to make — is dropped as unresolvable."
        ),
    ),
    Mutant(
        id="OI17-M12",
        file="src2sink/paths.py",
        old="    hit = _identifiers(text) & tainted\n    return sorted(hit)[0] if hit else None",
        new="    hit = _identifiers(text)\n    return sorted(hit)[0] if hit else None",
        selector="tests/test_tainted_paths.py",
        note=(
            "The taint set stops being consulted, so any argument mentioning any "
            "identifier counts as carrying a value — reachability without "
            "evidence, which reports every sink the service can touch rather than "
            "the ones a value reaches. Re-derived: the first form removed the "
            "`if not argument` prune, which SURVIVED, because propagating an empty "
            "taint set already matches nothing downstream. That prune bounds the "
            "work; this is the guard that decides the answer."
        ),
    ),
    Mutant(
        id="OI17-M13",
        file="src2sink/paths.py",
        old='        return min(self.hops, key=lambda h: confidence_rank(h.confidence)).confidence',
        new='        return max(self.hops, key=lambda h: confidence_rank(h.confidence)).confidence',
        selector="tests/test_tainted_paths.py",
        note=(
            "Path confidence becomes the *strongest* hop, so one high-confidence "
            "link makes a chain of guesses read as trustworthy. The minimum is the "
            "whole point: a chain is only as good as its weakest resolution."
        ),
    ),
    Mutant(
        id="OI17-M14",
        file="src2sink/paths.py",
        old="            if target in on_path:\n"
            "                # A cycle. The call graph is allowed to contain one; a path\n"
            "                # through it twice is the same path, so stop rather than loop.\n"
            "                continue\n",
        new="",
        selector="tests/test_tainted_paths.py",
        note=(
            "Cycle detection removed, so `a` calling `b` calling `a` recurses "
            "until the explored budget stops it — the search stops answering and "
            "starts burning, and reports truncation for a graph it could have "
            "walked."
        ),
    ),
    Mutant(
        id="OI17-M15",
        file="src2sink/paths.py",
        old="    return set(_IDENTIFIER_RX.findall(text or \"\"))",
        new="    return {text or \"\"}",
        selector="tests/test_tainted_paths.py",
        note=(
            "Identifier matching degrades to whole-text equality, so no argument "
            "ever matches a tainted name and every path disappears — the silent "
            "false-negative that an exclusion claim must never make."
        ),
    ),
    Mutant(
        id="OI17-M16",
        file="src2sink/extractors/ast_walk.py",
        old='    return next(\n        (c for c in node.children if c.type == "function_value_parameters"), None,\n    )',
        new="    return None",
        selector="tests/test_method_structure.py tests/test_tainted_paths.py",
        note=(
            "Kotlin parameters stop being read, which is how this shipped in "
            "2.1.0: Kotlin exposes no `parameters` field, so every Kotlin method "
            "recorded an empty parameter list. Nothing could be tainted, so no "
            "Kotlin path existed anywhere in the fleet — and the step 1 parity "
            "test compared method names, not their parameters."
        ),
    ),
    Mutant(
        id="OI17-M17",
        file="src2sink/extractors/ast_walk.py",
        old='    return next((c for c in node.children if c.type == "value_arguments"), None)',
        new="    return None",
        selector="tests/test_call_resolution.py tests/test_tainted_paths.py",
        note=(
            "Kotlin call arguments stop being read, so no Kotlin hop can carry "
            "taint. The same defect as OI17-M16 from the other end, and the same "
            "consequence: a clean-looking result across half the JVM fleet."
        ),
    ),
    Mutant(
        id="OI15-M1",
        file="src2sink/index_store.py",
        old='    if stored.get("fleet_signature") != fleet_signature(record_paths):\n'
            "        conn.close()\n"
            "        return None",
        new="",
        selector="tests/test_fleet_index.py",
        note=(
            "Staleness checking removed, so an index built from a metabase that "
            "has since changed is served anyway — a fast, confident answer about "
            "a fleet that no longer exists, which is worse than the slowness "
            "OI-15 set out to fix."
        ),
    ),
    Mutant(
        id="OI15-M2",
        file="src2sink/index_store.py",
        old='    digest.update(f"index={INDEX_VERSION} schema={SCHEMA_VERSION} "\n'
            '                  f"derivation={DERIVATION_VERSION}\\n".encode())',
        new='    digest.update(f"index={INDEX_VERSION}\\n".encode())',
        selector="tests/test_fleet_index.py",
        note=(
            "The signature stops folding in the versions that produced the "
            "records, so a DERIVATION_VERSION bump leaves the index looking "
            "fresh. A record's bytes can be unchanged while its meaning is not."
        ),
    ),
    Mutant(
        id="OI15-M3",
        file="src2sink/index_store.py",
        old='_OUTBOUND_FAMILIES = frozenset({"http-out", "api-client-consumer"})',
        new='_OUTBOUND_FAMILIES = frozenset(\n'
            '    {"http-out", "api-client-consumer", "http-in", "sql", "data-store"}\n'
            ")",
        selector="tests/test_fleet_index.py",
        note=(
            "The subset widens until `outbound_node` holds most of the fleet's "
            "nodes, so scanning it is a scan of the fleet again and the memory "
            "ceiling returns by the side door. Deliberately a *plausible* set "
            "rather than `None`: setting it to None kills by TypeError in every "
            "test, which proves the code runs, not that the invariant holds."
        ),
    ),
    Mutant(
        id="OI29-M1",
        file="src2sink/trace.py",
        old="        prev = upstream.get(key)\n"
            "        if prev is None or confidence_rank(hit.confidence) > confidence_rank(prev.confidence):\n"
            "            upstream[key] = hit",
        new="        upstream[key] = hit",
        selector="tests/test_fleet_index.py tests/test_characterization.py",
        note=(
            "Back to last-edge-wins, so a caller's confidence is whichever of its "
            "several route edges the collector yielded last — a `high` edge "
            "silently overwritten by a `low` one, understating a real finding."
        ),
    ),
    Mutant(
        id="OI28-M1",
        file="src2sink/graph_common.py",
        old="    return bool(norm) and bool(_significant_segments(norm))",
        new="    return bool(norm)",
        selector="tests/test_oi28_index_fast_path.py",
        note=(
            "The index fast path stops consulting the significance filter, so a "
            "dict hit on `/v1` returns a confident edge again — OI-24's defect "
            "reached through the caller instead of the callee."
        ),
    ),
    Mutant(
        id="OI24-M1",
        file="src2sink/graph_common.py",
        old="    label = _structural_match(o, i, op, ip)",
        new='    if o == i:\n        return "high"\n    label = _structural_match(o, i, op, ip)',
        selector="tests/test_path_match_significance.py",
        note=(
            "The equality shortcut restored above the significance guard, so two "
            "repos both exposing a bare `/v1` match at high again."
        ),
    ),
    Mutant(
        id="OI25-M1",
        file="src2sink/graph_common.py",
        old="        and s != _PLACEHOLDER_SEGMENT\n",
        new="",
        selector="tests/test_path_match_significance.py",
        note="`/{id}` counts as a destination again, so `/{id}` matches `/{name}`.",
    ),
    Mutant(
        id="OI25-M2",
        file="src2sink/graph_common.py",
        old="    if label is not None and _names_only_an_operation(op) and _names_only_an_operation(ip):",
        new="    if False:",
        selector="tests/test_path_match_significance.py",
        note=(
            "Verb-only matches stop being capped, so `/search` against `/search` "
            "is high-confidence evidence that one service calls the other."
        ),
    ),
    Mutant(
        # Re-derived alongside OI1-M1 when the filter became a multi-line
        # condition; the defect is unchanged.
        id="OI1-M2",
        file="src2sink/graph_common.py",
        old="        and s.lower() not in _GENERIC_SEGMENTS\n",
        new="",
        selector="tests/test_graph_common.py tests/test_path_match_significance.py",
        note="Generic segments (`/api`, `/service`) treated as route names again.",
    ),
    Mutant(
        id="OI1-M3",
        file="src2sink/graph_common.py",
        # Re-derived when OI-24 split the ladder into _structural_match: the
        # emptiness guard and the equality rung are no longer adjacent lines.
        old="    if not op or not ip:\n        return None",
        new="    if not op or not ip:\n        return \"low\"",
        selector="tests/test_graph_common.py tests/test_cross_repo_caller_coverage.py"
                 " tests/test_path_match_significance.py",
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
    Mutant(
        id="OI14-M1",
        file="src2sink/trace.py",
        old="    service_edges, indices = _resolve_fleet_derivations(\n"
            "        metabase_root, repos_root, records, service_edges, producer_indices,\n"
            "    )",
        new="    service_edges, indices = _resolve_fleet_derivations(\n"
            "        metabase_root, repos_root, records, None, producer_indices,\n"
            "    )",
        selector="tests/test_trace_fleet_scaling.py",
        note=(
            "Supplied fleet edges discarded, so every target rebuilds the "
            "fleet-wide graph again — the OI-14 defect, restored silently since "
            "the report is identical either way."
        ),
    ),
    Mutant(
        id="OI14-M2",
        file="src2sink/trace_batch.py",
        old="    service_edges, _unmatched = collect_service_edges(records)",
        new="    service_edges = None",
        selector="tests/test_trace_fleet_scaling.py",
        note=(
            "Batch stops hoisting the fleet graph out of its target loop, "
            "returning the cost to O(targets x fleet)."
        ),
    ),
    Mutant(
        id="OI14-M3",
        file="src2sink/graph_common.py",
        old="@lru_cache(maxsize=_PATH_CACHE_MAX)\ndef normalize_path_template(path: str) -> str:",
        new="def normalize_path_template(path: str) -> str:",
        selector="tests/test_trace_fleet_scaling.py",
        note=(
            "Route normalisation memoisation removed, restoring the quadratic "
            "graph build (4x per doubling of the fleet)."
        ),
    ),
    Mutant(
        id="OI14-M4",
        file="src2sink/graph_common.py",
        old="    return tuple(\n        s for s in path.split(\"/\")",
        new="    return list(\n        s for s in path.split(\"/\")",
        selector="tests/test_trace_fleet_scaling.py",
        note=(
            "Cached segment split hands back a mutable list, so one caller's "
            "edit would corrupt every later lookup of the same path."
        ),
    ),
    Mutant(
        id="OI14-M5",
        file="src2sink/graph_common.py",
        old="_PATH_CACHE_MAX = 65_536",
        new="_PATH_CACHE_MAX = None",
        selector="tests/test_trace_fleet_scaling.py",
        note=(
            "Path cache unbounded — keys are paths read from scanned repos, so "
            "an enormous or hostile fleet grows it without limit."
        ),
    ),
    Mutant(
        id="CYC-M1",
        file="src2sink/extractors/http_out.py",
        old="    if cached is not None and cached[0] is bindings:",
        new="    if cached is not None:",
        selector="tests/test_import_graph.py tests/test_cross_repo_caller_coverage.py",
        note=(
            "Derived call-site patterns never invalidate, so reconfiguring the "
            "binding registry leaves the extractor on the previous set — the "
            "stale-second-copy failure the push/pull inversion removed."
        ),
    ),
    Mutant(
        id="CYC-M2",
        file="src2sink/extractors/http_out.py",
        old="    bindings = get_bindings()",
        new="    bindings = ()",
        selector="tests/test_import_graph.py tests/test_cross_repo_caller_coverage.py",
        note=(
            "Patterns stop deriving from the registry, silently disabling every "
            "class_patterns binding — the original defect, in its new shape."
        ),
    ),
    Mutant(
        id="OI16-M1",
        file="src2sink/build_metabase_v2.py",
        old='        data.get("git_sha") == current_sha\n'
            '        and data.get("detection_version") == DETECTION_VERSION',
        new='        data.get("git_sha") == current_sha',
        selector="tests/test_detection_version.py",
        note=(
            "Skip keyed on the repo sha alone again, so a record built by an "
            "older detector is never rebuilt — the whole of OI-16."
        ),
    ),
    Mutant(
        id="OI16-M2",
        file="scripts/detection_version_check.py",
        old="    if recorded_version != version_now:",
        new="    if recorded_version != version_now or True:",
        selector="tests/test_detection_fingerprint_gate.py",
        note=(
            "The gate stops failing on a changed extractor with an unbumped "
            "version, which is the only case it exists to catch."
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
        # argv is this interpreter plus literals from CATALOGUE, which is source in
        # this file — nothing external reaches it, and there is no shell
        # (opengrep dangerous-subprocess-use-audit).
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell, sandboxed cwd.  # nosemgrep
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
        # git is an absolute path from shutil.which and every argument is a
        # literal; no shell (opengrep dangerous-subprocess-use-audit).
        proc = subprocess.run(  # nosec B603 - absolute path, fixed argv, no shell.  # nosemgrep
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
    slow: list[tuple[str, float]] = []
    print(f"Running {len(selected)} mutant(s)\n")
    for mutant in selected:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="src2sink-mutation-") as tmp:
            root = _sandbox(Path(tmp))
            _apply(root, mutant)
            killed, tail = _run_selector(root, mutant.selector)
        elapsed = time.monotonic() - started
        if elapsed >= _SLOW_MUTANT_S:
            slow.append((mutant.id, elapsed))
        mark = "killed " if killed else "SURVIVED"
        print(f"  [{mark}] {mutant.id}  {mutant.file}  ({tail})")
        if not killed:
            survivors.append(mutant)

    print()
    if slow:
        total = sum(s for _id, s in slow)
        print(
            f"{len(slow)} mutant(s) took over {_SLOW_MUTANT_S:.0f}s "
            f"({total:.0f}s of the run): " + ", ".join(f"{i} {s:.0f}s" for i, s in slow)
        )
        print("  Expected where the mutant breaks a timeout — killing it means "
              "waiting for that timeout. Watch this, not just the entry count.\n")

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
