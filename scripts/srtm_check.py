#!/usr/bin/env python3
"""SRTM traceability gate — every security requirement must have live evidence.

The Security Requirements Traceability Matrix (§8 of
``docs/security-privacy-gap-analysis.md``) maps each requirement to a control and
a ``TA-xxx`` test artifact. A matrix is only worth anything if it stays true, so
this check re-derives the mapping from the tree on every CI run and fails when
the two drift apart:

* every ``TA-xxx`` in the matrix must have evidence — an implementing test under
  ``tests/`` for automated artifacts, or a named section in
  ``docs/operations-security.md`` for the three audit-only ones (§9.4);
* every ``TA-xxx`` referenced by a test must exist in the matrix, so a renamed or
  deleted requirement cannot leave orphaned labels behind.

Evidence is a literal ``TA-xxx`` mention in the test module (docstring, comment,
or test name). That is deliberately cheap: it makes the link greppable in both
directions and costs a reviewer nothing to keep accurate.

Usage:
    python scripts/srtm_check.py            # exits 1 on any traceability gap
    python scripts/srtm_check.py --summary $GITHUB_STEP_SUMMARY
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRTM_DOC = REPO_ROOT / "docs" / "security-privacy-gap-analysis.md"
OPS_DOC = REPO_ROOT / "docs" / "operations-security.md"
TESTS_DIR = REPO_ROOT / "tests"

SRTM_HEADING = "## 8. Security Requirements Traceability Matrix"
TEST_ID_RX = re.compile(r"TA-\d{3}")

# Artifacts §9.4 scopes as manual review of the deployment, not code: there is no
# test that can prove a metabase store is access-controlled or that a retention
# schedule is honoured. Their evidence is the named section of the operations
# guide a reviewer works through, so the gate checks that section still exists.
AUDIT_ONLY: dict[str, str] = {
    "TA-010": "output/config sensitivity handling (restricted store, secret-file config)",
    "TA-012": "least-privilege CI identity + read-only repos mount",
    "TA-014": "retention schedule + erasure procedure",
}


@dataclass
class Requirement:
    """One row of the SRTM: a requirement and the test artifacts that prove it."""

    req_id: str
    summary: str
    priority: str
    test_ids: list[str]


@dataclass
class Result:
    """Outcome of the traceability check."""

    requirements: list[Requirement] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def parse_srtm(doc: Path) -> list[Requirement]:
    """Extract the requirement rows of the SRTM table from the gap analysis."""
    text = doc.read_text(encoding="utf-8")
    if SRTM_HEADING not in text:
        raise SystemExit(f"{doc}: '{SRTM_HEADING}' section not found")
    section = text.split(SRTM_HEADING, 1)[1].split("\n---", 1)[0]

    requirements: list[Requirement] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # Columns: Req ID | Requirement | Type | Use Case | Threat | Control |
        #          Test ID | Test Description | Priority
        if len(cells) < 9 or cells[0] in ("Req ID", "") or set(cells[0]) == {"-"}:
            continue
        test_ids = TEST_ID_RX.findall(cells[6])
        if not test_ids:
            raise SystemExit(f"{doc}: SRTM row '{cells[0]}' has no TA-xxx test ID")
        requirements.append(
            Requirement(
                req_id=cells[0], summary=cells[1], priority=cells[8], test_ids=test_ids
            )
        )
    if not requirements:
        raise SystemExit(f"{doc}: SRTM table parsed to zero rows — has its format changed?")
    return requirements


def collect_test_evidence(tests_dir: Path) -> dict[str, list[str]]:
    """Map each TA id to the test modules that reference it."""
    evidence: dict[str, list[str]] = {}
    for path in sorted(tests_dir.rglob("test_*.py")):
        for test_id in sorted(set(TEST_ID_RX.findall(path.read_text(encoding="utf-8")))):
            evidence.setdefault(test_id, []).append(
                str(path.relative_to(REPO_ROOT))
            )
    return evidence


def collect_audit_evidence(ops_doc: Path) -> dict[str, list[str]]:
    """Map each audit-only TA id to the operations-guide section documenting it."""
    evidence: dict[str, list[str]] = {}
    current = ""
    for line in ops_doc.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line.lstrip("# ").strip()
        for test_id in TEST_ID_RX.findall(line):
            if test_id in AUDIT_ONLY:
                where = f"{ops_doc.relative_to(REPO_ROOT)} § {current or 'preamble'}"
                evidence.setdefault(test_id, [])
                if where not in evidence[test_id]:
                    evidence[test_id].append(where)
    return evidence


def check() -> Result:
    """Run the traceability check and return the collected result."""
    result = Result(requirements=parse_srtm(SRTM_DOC))
    test_evidence = collect_test_evidence(TESTS_DIR)
    audit_evidence = collect_audit_evidence(OPS_DOC)
    result.evidence = {**test_evidence, **audit_evidence}

    matrix_ids = {tid for req in result.requirements for tid in req.test_ids}

    for req in result.requirements:
        for test_id in req.test_ids:
            if test_id in test_evidence:
                continue
            if test_id in AUDIT_ONLY:
                if test_id not in audit_evidence:
                    result.failures.append(
                        f"{req.req_id} / {test_id}: audit artifact "
                        f"({AUDIT_ONLY[test_id]}) is not documented in "
                        f"{OPS_DOC.relative_to(REPO_ROOT)}"
                    )
                continue
            result.failures.append(
                f"{req.req_id} / {test_id}: no test under tests/ references it "
                f"({req.summary})"
            )

    for test_id, files in sorted(test_evidence.items()):
        if test_id not in matrix_ids:
            result.failures.append(
                f"{test_id}: referenced by {', '.join(files)} but absent from the "
                "SRTM — stale label or a requirement was dropped"
            )
    return result


def render_report(result: Result) -> str:
    """Render the traceability matrix as markdown."""
    lines = [
        "# SRTM traceability",
        "",
        f"{len(result.requirements)} requirements · "
        f"{len({t for r in result.requirements for t in r.test_ids})} test artifacts · "
        f"{len(result.failures)} gaps",
        "",
        "| Req ID | Requirement | Priority | Test ID | Evidence |",
        "|---|---|---|---|---|",
    ]
    for req in result.requirements:
        for test_id in req.test_ids:
            where = result.evidence.get(test_id, [])
            kind = "audit" if test_id in AUDIT_ONLY else "test"
            cell = f"{kind}: {', '.join(where)}" if where else "**MISSING**"
            lines.append(
                f"| {req.req_id} | {req.summary} | {req.priority} | {test_id} | {cell} |"
            )
    if result.failures:
        lines += ["", "## Gaps", ""] + [f"- {f}" for f in result.failures]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Check SRTM traceability, print the report, and signal gaps via exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="also append the markdown report to this file (e.g. $GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args(argv)

    result = check()
    report = render_report(result)
    print(report)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(report)

    if result.failures:
        print(f"SRTM traceability FAILED — {len(result.failures)} gap(s)", file=sys.stderr)
        return 1
    print(f"SRTM traceability OK — {len(result.requirements)} requirements traced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
