#!/usr/bin/env python3
"""Record fleet-wide node-family counts for Phase 4 regression tests.

Example:
  uv run python metabase/scripts/record_fleet_baseline.py \
    --metabase-root metabase \
    --output metabase/tests/fixtures/fleet-family-baseline.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

from .schema import SCHEMA_VERSION


def count_fleet_families(metabase_root: Path) -> tuple[int, dict[str, int]]:
    """Count node-family occurrences across all current-schema repo JSONs.

    Args:
        metabase_root: Metabase root containing the ``repos/`` directory.

    Returns:
        A tuple of (repo count, mapping of family name to node count).
    """
    repos_dir = metabase_root / "repos"
    if not repos_dir.is_dir():
        raise SystemExit(f"No repos dir: {repos_dir}")

    families: Counter[str] = Counter()
    repo_count = 0
    for jp in sorted(repos_dir.glob("*/*.json")):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema_version") != SCHEMA_VERSION:
            continue
        repo_count += 1
        for node in data.get("nodes", []):
            families[node.get("family", "?")] += 1
    return repo_count, dict(families)


def main() -> int:
    """CLI entry point: write the fleet-family baseline JSON for regression tests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metabase-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--max-drop-fraction",
        type=float,
        default=0.05,
        help="Documented tolerance for regression test (default 5%%)",
    )
    args = parser.parse_args()

    metabase_root = Path(args.metabase_root).resolve()
    out = Path(args.output).resolve()

    repo_count, families = count_fleet_families(metabase_root)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "repo_count": repo_count,
        "max_family_drop_fraction": args.max_drop_fraction,
        "recorded_at": date.today().isoformat(),
        "note": "Regenerated via record_fleet_baseline.py",
        "families": dict(sorted(families.items(), key=lambda x: (-x[1], x[0]))),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({repo_count} repos, {len(families)} families)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
