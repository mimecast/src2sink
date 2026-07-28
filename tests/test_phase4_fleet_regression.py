"""Phase 4: fleet-wide node-family regression (requires local v2 JSONs)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from src2sink.schema import SCHEMA_VERSION

FIXTURES = Path(__file__).resolve().parent / "fixtures"
METABASE_ROOT = Path(__file__).resolve().parents[1]
REPOS_DIR = METABASE_ROOT / "repos"


def _count_fleet() -> tuple[int, Counter[str]]:
    families: Counter[str] = Counter()
    repo_count = 0
    for jp in sorted(REPOS_DIR.glob("*/*.json")):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema_version") != SCHEMA_VERSION:
            continue
        repo_count += 1
        for node in data.get("nodes", []):
            families[node.get("family", "?")] += 1
    return repo_count, families


@pytest.mark.fleet
def test_fleet_family_counts_within_baseline() -> None:
    if not REPOS_DIR.is_dir():
        pytest.skip("metabase/repos not present — clone fleet or skip fleet tests")

    baseline = json.loads(
        (FIXTURES / "fleet-family-baseline.json").read_text(encoding="utf-8"),
    )
    max_drop = float(baseline.get("max_family_drop_fraction", 0.05))
    expected_repos = int(baseline["repo_count"])
    expected_families: dict[str, int] = baseline["families"]

    repo_count, current = _count_fleet()
    if repo_count == 0:
        pytest.skip("no schema_version=2 JSONs under metabase/repos")

    min_repos = int(expected_repos * (1.0 - max_drop))
    assert repo_count >= min_repos, (
        f"repo count dropped: {repo_count} < {min_repos} "
        f"(baseline {expected_repos})"
    )

    failures: list[str] = []
    for family, base_count in sorted(expected_families.items()):
        cur = current.get(family, 0)
        floor = int(base_count * (1.0 - max_drop))
        if cur < floor:
            failures.append(
                f"{family}: {cur} < {floor} (baseline {base_count}, "
                f"max drop {max_drop:.0%})",
            )

    assert not failures, "Fleet regression failed:\n" + "\n".join(failures)
