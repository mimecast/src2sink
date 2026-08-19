#!/usr/bin/env python3
"""Measure where the metabase's bytes actually go (4.0 plan §3.5).

**A measurement instrument, not a gate.** Outside the `ci` chain: it reads a
built metabase and produces numbers to decide from.

§3.5 records that the metabase is *roughly twice the source it describes* — 2.2 GB
of JSON from 1.07 GiB of source — and that shrinking the record shrinks the
dominant phase, because aggregation's remaining cost is three passes over it. It
names three candidates and states that each is **to be measured before building**,
since that discipline caught every wrong prediction in the 3.0 cycle:

1. **Drop what is derivable** — `repo` is on every node and is also the record's
   own identity; `id` is a deterministic function of the other fields.
2. **Hoist per-file constants** — `file`, `language` and `framework` repeat for
   every node in a file.
3. **Omit defaults** — `pii_classification` and `data_class` are null on the
   large majority of nodes.

This script measures all three, in bytes, against a real metabase. It reports
what each would save rather than whether it is a good idea; that judgement is
§3.5's, and it is a `SCHEMA_VERSION` change either way.

**The byte figures are exact, not estimated.** Records are written with
`indent=2, sort_keys=True`, and a node sits at depth 3, so one scalar field
occupies `6 spaces + "key" + ": " + value + ",\\n"` on disk. Only scalar fields
are costed — `detail` is nested and multi-line, so it is reported as a count and
a share of total bytes instead of being folded into the candidates.

Usage:
  uv run python scripts/measure_record_size.py --metabase-root <mb> -o size.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src2sink.schema import SCHEMA_VERSION

# A node is the third level of nesting in the written record (record -> "nodes"
# list -> node object -> field), so each of its fields is indented six spaces.
NODE_FIELD_INDENT = 6

# Candidate 1: present on every node and recoverable without being stored.
DERIVABLE_FIELDS = ("repo", "id")
# Candidate 2: constant for every node that shares a file.
HOISTABLE_FIELDS = ("file", "language", "framework")
# Candidate 3: null on the majority of nodes.
DEFAULT_FIELDS = ("pii_classification", "data_class")


def field_bytes(key: str, value: Any, *, last: bool = False) -> int:
    """Return the exact on-disk cost of one scalar field line inside a node.

    `      "key": value,\\n` — the indent, the quoted key, the separator, the
    serialised value, the comma and the newline.

    ``last`` drops the comma. Records are written with ``sort_keys=True``, so the
    alphabetically last key of a node has none — which for a `FlowNode` is
    ``repo``, one of the fields this script costs. Ignoring that over-counts by
    exactly one byte per node, which a cross-check against the written files
    caught; it is small, but a script whose whole claim is exact byte accounting
    does not get to be approximately right.
    """
    comma = 0 if last else 1
    return NODE_FIELD_INDENT + len(json.dumps(key)) + 2 + len(json.dumps(value)) + comma + 1


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
    """Census every record: bytes on disk, node mix, and what each §3.5 candidate saves."""
    total_bytes = 0
    unreadable = 0
    records = 0
    nodes_total = 0
    edges_total = 0
    by_kind: Counter[str] = Counter()
    by_family: Counter[str] = Counter()
    by_language: Counter[str] = Counter()
    detail_bytes = 0
    record_sizes: list[int] = []

    derivable = dict.fromkeys(DERIVABLE_FIELDS, 0)
    hoistable = dict.fromkeys(HOISTABLE_FIELDS, 0)
    defaults = dict.fromkeys(DEFAULT_FIELDS, 0)
    default_null_counts = dict.fromkeys(DEFAULT_FIELDS, 0)

    for path in sorted(metabase_root.glob("repos/*/*.json")):
        data = read_record(path)
        if data is None:
            unreadable += 1
            continue
        records += 1
        size = path.stat().st_size
        total_bytes += size
        record_sizes.append(size)

        nodes = [n for n in data.get("nodes", []) if isinstance(n, dict)]
        nodes_total += len(nodes)
        edges_total += len(data.get("edges", []))

        # Candidate 2 needs the per-file grouping: hoisting stores the value once
        # per file, so the saving is every repeat after the first.
        per_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            by_kind[str(node.get("kind", "?"))] += 1
            by_family[str(node.get("family", "?"))] += 1
            by_language[str(node.get("language", "?"))] += 1
            detail_bytes += len(json.dumps(node.get("detail", {})))
            last_key = max(node) if node else ""
            for field in DERIVABLE_FIELDS:
                if field in node:
                    derivable[field] += field_bytes(field, node[field], last=field == last_key)
            for field in DEFAULT_FIELDS:
                if node.get(field) is None:
                    default_null_counts[field] += 1
                    defaults[field] += field_bytes(field, None, last=field == last_key)
            per_file[str(node.get("file", ""))].append(node)

        for file_nodes in per_file.values():
            repeats = len(file_nodes) - 1
            if repeats <= 0:
                continue
            first = file_nodes[0]
            last_key = max(first) if first else ""
            for field in HOISTABLE_FIELDS:
                # Every node in a file carries the same value, so all but one
                # copy is redundant. Costed on the first node's value.
                hoistable[field] += repeats * field_bytes(
                    field, first.get(field), last=field == last_key
                )

    # Byte totals are kept in their own typed mapping; folding them into the
    # reporting dict alongside the per-field breakdowns widens the value type to
    # `object` and every later arithmetic use needs a cast.
    candidate_bytes: dict[str, int] = {
        "1_drop_derivable": sum(derivable.values()),
        "2_hoist_per_file": sum(hoistable.values()),
        "3_omit_null_defaults": sum(defaults.values()),
    }
    combined = sum(candidate_bytes.values())

    def pct(value: int) -> float:
        """Share of the whole metabase, as a percentage."""
        return round(100 * value / total_bytes, 2) if total_bytes else 0.0

    candidates: dict[str, Any] = {
        "1_drop_derivable": {
            "fields": derivable,
            "bytes": candidate_bytes["1_drop_derivable"],
            "pct_of_metabase": pct(candidate_bytes["1_drop_derivable"]),
        },
        "2_hoist_per_file": {
            "fields": hoistable,
            "bytes": candidate_bytes["2_hoist_per_file"],
            "pct_of_metabase": pct(candidate_bytes["2_hoist_per_file"]),
        },
        "3_omit_null_defaults": {
            "fields": defaults,
            "null_counts": default_null_counts,
            "bytes": candidate_bytes["3_omit_null_defaults"],
            "pct_of_metabase": pct(candidate_bytes["3_omit_null_defaults"]),
        },
    }

    record_sizes.sort()
    return {
        "records_read": records,
        "records_unreadable": unreadable,
        "total_bytes": total_bytes,
        "nodes": nodes_total,
        "edges": edges_total,
        "bytes_per_node": round(total_bytes / nodes_total, 1) if nodes_total else 0.0,
        "detail_bytes": detail_bytes,
        "detail_pct_of_metabase": (
            round(100 * detail_bytes / total_bytes, 2) if total_bytes else 0.0
        ),
        "record_size": {
            "min": record_sizes[0] if record_sizes else 0,
            "median": record_sizes[len(record_sizes) // 2] if record_sizes else 0,
            "p90": record_sizes[int(len(record_sizes) * 0.9)] if record_sizes else 0,
            "max": record_sizes[-1] if record_sizes else 0,
        },
        "nodes_by_kind": dict(by_kind.most_common()),
        "nodes_by_family": dict(by_family.most_common()),
        "nodes_by_language": dict(by_language.most_common()),
        "candidates": candidates,
        "combined_bytes": combined,
        "combined_pct_of_metabase": (
            round(100 * combined / total_bytes, 2) if total_bytes else 0.0
        ),
    }


def human(count: int) -> str:
    """Format a byte count for reading."""
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def report(summary: dict[str, Any]) -> None:
    """Print the census and what each §3.5 candidate would save."""
    print()
    print(f"records read {summary['records_read']}, unreadable {summary['records_unreadable']}")
    if summary["records_unreadable"]:
        print("  WARNING: unreadable records mean every number below is a lower bound")
    print(f"metabase          {human(summary['total_bytes'])}")
    print(f"nodes             {summary['nodes']} ({summary['bytes_per_node']} bytes/node)")
    print(f"edges             {summary['edges']}")
    sizes = summary["record_size"]
    print(
        f"record size       min {human(sizes['min'])}, median {human(sizes['median'])}, "
        f"p90 {human(sizes['p90'])}, max {human(sizes['max'])}"
    )
    print(
        f"detail payloads   {human(summary['detail_bytes'])} "
        f"({summary['detail_pct_of_metabase']}% of the metabase)"
    )
    print()
    print("nodes by kind:")
    for kind, count in summary["nodes_by_kind"].items():
        share = 100 * count / summary["nodes"] if summary["nodes"] else 0
        print(f"  {kind:14} {count:9}  {share:5.1f}%")
    print()
    print("§3.5 candidates, measured:")
    for name, candidate in summary["candidates"].items():
        print(
            f"  {name:22} {human(int(candidate['bytes'])):>10}  "
            f"{candidate['pct_of_metabase']:5.2f}%"
        )
        for field, count in candidate["fields"].items():
            print(f"      {field:22} {human(int(count))}")
    print(
        f"  {'ALL THREE':22} {human(summary['combined_bytes']):>10}  "
        f"{summary['combined_pct_of_metabase']:5.2f}%"
    )
    print()
    print("Note: these are the *stored* costs, not the parse-time saving. Aggregation")
    print("decodes and walks this JSON three times, so a byte removed is removed from")
    print("the dominant phase three times over - but that ratio is not measured here.")


def main() -> int:
    """CLI entry point: census the metabase and write the JSON."""
    ap = argparse.ArgumentParser(description="Where the metabase's bytes go (4.0 plan §3.5).")
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
