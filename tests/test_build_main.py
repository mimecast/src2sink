"""End-to-end smoke test for build_metabase_v2.main.

`main` was refactored in Phase C into small helpers; this drives the whole CLI
path (arg parsing, discovery, the per-repo bulkhead dispatch, aggregation, source
map, and run manifest) on a tiny synthetic tree to prove it still works and to
give `main` real coverage. Runs single-process with one small repo.
"""

from __future__ import annotations

import json
import sys

import pytest

from src2sink.build_metabase_v2 import main


@pytest.mark.watchdog(60)
def test_main_end_to_end(tmp_path, monkeypatch):
    repo = tmp_path / "repos" / "grp" / "svc" / "src"
    repo.mkdir(parents=True)
    (repo / "QueryController.java").write_text(
        '@RestController class Q { @PostMapping("/run") void r(@RequestBody String sql)'
        " { jdbcTemplate.query(sql); } }",
        encoding="utf-8",
    )
    metabase = tmp_path / "metabase"

    argv = [
        "src2sink-build",
        "--repos-root", str(tmp_path / "repos"),
        "--metabase-root", str(metabase),
        "--workers", "1",
        "--repo-timeout", "60",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    assert main() == 0

    # Per-repo output and the run manifest were written.
    assert (metabase / "repos" / "grp" / "svc.json").is_file()
    manifest = json.loads((metabase / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["updated"] == 1
    assert manifest["updated_repos"][0]["repo"] == "grp/svc"
    # Aggregation ran (taint catalogues directory exists).
    assert (metabase / "taint").is_dir()


@pytest.mark.watchdog(60)
def test_main_missing_repos_root_returns_2(tmp_path, monkeypatch):
    argv = [
        "src2sink-build",
        "--repos-root", str(tmp_path / "does-not-exist"),
        "--metabase-root", str(tmp_path / "mb"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 2
