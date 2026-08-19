#!/usr/bin/env python3
"""Count what tier each language's calls actually resolve at, fleet-wide (`OI-43`).

**A measurement instrument, not a gate.** Outside the `ci` chain: it reads a
built metabase, not the source tree, and produces a number to reason about.

`OI-43` step 3 moved TypeScript to T2, and Python and Go to T1, on a *canonical
shape* — an interface- or concrete-typed field, called, resolving to the
implementation. It was never checked against real code at fleet scale, and the
4.0 plan's own method section is a list of what happens to unmeasured
predictions: `+20%` that turned out to be 3.4x, threading assumed useless that
was 3.0x, a phase judged unnecessary that was 67% of the dominant one.

This answers the question that matters after a rebuild: **did the estate's calls
actually move tier, or only the fixtures?**

Tier lives in the edge's `evidence` as a `[T1]`/`[T2]`/`[T3]` prefix (see
`resolve.call_edges`), and the language belongs to the *calling* node, so edges
are joined back to `src_id`. A tier whose confidence is `low` where the tier
would normally earn `high`/`medium` is an **ambiguous** resolution — the resolver
found several candidates — and is counted separately, because an ambiguous T2
that reads as a confident answer is `OI-26` in a different place.

Usage:
  uv run python scripts/measure_resolution_tiers.py --metabase-root <mb> -o tiers.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src2sink.schema import SCHEMA_VERSION

# What each tier earns when the resolution is unambiguous, mirroring
# `resolve._TIER_CONFIDENCE`. Read here only to tell "this tier was degraded
# because the target was one of several" from "this tier is simply weak".
TIER_CONFIDENCE = {"T1": "high", "T2": "medium", "T3": "low"}
TIERS = ("T1", "T2", "T3")


def tier_of(evidence: str) -> str | None:
    """Return the `Tn` tier an edge's evidence names, or None if it names none."""
    if not evidence.startswith("[T"):
        return None
    close = evidence.find("]")
    if close == -1:
        return None
    tier = evidence[1:close]
    return tier if tier in TIER_CONFIDENCE else None


def read_record(path: Path) -> dict[str, Any] | None:
    """Load one repo record, or None if it is unreadable or a stale schema."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    return data


def scan(metabase_root: Path) -> dict[str, Any]:
    """Tally resolved-call tiers per language across every record in the metabase."""
    by_language: dict[str, Counter[str]] = defaultdict(Counter)
    ambiguous: Counter[str] = Counter()
    unreadable = 0
    records = 0
    repos_by_language: dict[str, set[str]] = defaultdict(set)

    for path in sorted(metabase_root.glob("repos/*/*.json")):
        data = read_record(path)
        if data is None:
            unreadable += 1
            continue
        records += 1
        language_of = {
            node.get("id"): node.get("language", "unknown")
            for node in data.get("nodes", [])
            if isinstance(node, dict)
        }
        repo_id = f"{data.get('group', '?')}/{data.get('name', '?')}"
        for edge in data.get("edges", []):
            if not isinstance(edge, dict):
                continue
            tier = tier_of(str(edge.get("evidence", "")))
            if tier is None:
                continue
            language = language_of.get(edge.get("src_id"), "unknown")
            by_language[language][tier] += 1
            repos_by_language[language].add(repo_id)
            # Degraded below what the tier normally earns: several candidates
            # matched, so the answer is weaker than the tier alone implies.
            if edge.get("confidence") != TIER_CONFIDENCE[tier]:
                ambiguous[f"{language}:{tier}"] += 1

    languages: dict[str, Any] = {}
    for language in sorted(by_language):
        counts = by_language[language]
        total = sum(counts.values())
        strong = counts["T1"] + counts["T2"]
        languages[language] = {
            "repos": len(repos_by_language[language]),
            "resolved_calls": total,
            **{tier: counts[tier] for tier in TIERS},
            "pct_strong": round(100 * strong / total, 2) if total else 0.0,
        }
    return {
        "records_read": records,
        "records_unreadable": unreadable,
        "languages": languages,
        "ambiguous": dict(ambiguous.most_common()),
    }


def report(summary: dict[str, Any]) -> None:
    """Print the per-language tier table."""
    print()
    print(f"records read {summary['records_read']}, unreadable {summary['records_unreadable']}")
    if summary["records_unreadable"]:
        print("  WARNING: unreadable records mean every count below is a lower bound")
    print()
    header = f"{'language':12} {'repos':>6} {'calls':>9} {'T1':>8} {'T2':>8} {'T3':>8} {'strong':>8}"
    print(header)
    print("-" * len(header))
    for language, row in summary["languages"].items():
        print(
            f"{language:12} {row['repos']:6} {row['resolved_calls']:9} "
            f"{row['T1']:8} {row['T2']:8} {row['T3']:8} {row['pct_strong']:7.2f}%"
        )
    print()
    print("What to check against `OI-43` step 3's claims:")
    print("  typescript / tsx  should now show T2 where it was all T3")
    print("  python            should now show T1")
    print("  go                should show T1 on concrete fields; T2 stays 0 by design")
    print("                    (Go interface satisfaction is structural - unreachable)")
    print("  javascript        T1 is intrinsically impossible; it declares no types")
    if summary["ambiguous"]:
        print()
        print("ambiguous resolutions (tier found several candidates, so confidence degraded):")
        for key, count in list(summary["ambiguous"].items())[:15]:
            print(f"  {key:28} {count}")


def main() -> int:
    """CLI entry point: tally resolution tiers per language and write the JSON."""
    ap = argparse.ArgumentParser(description="Resolution tiers per language (OI-43).")
    ap.add_argument("--metabase-root", type=Path, required=True, help="Metabase root.")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Where to write the JSON.")
    args = ap.parse_args()

    root = args.metabase_root.expanduser()
    if not (root / "repos").is_dir():
        raise SystemExit(f"no repos/ directory under {root}")

    summary = scan(root)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    report(summary)
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
