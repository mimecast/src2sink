"""TA-015 / B8 — run provenance manifest (threat-model R-1 / GDPR Art. 30)."""

from __future__ import annotations

import argparse
import json

from src2sink.build_metabase_v2 import _tool_version, _write_run_manifest


def _args(**over):
    base = dict(
        repos_root="/abs/secret-location/repos",
        metabase_root="/abs/out/metabase",
        workers=4,
        repo_timeout=300,
        max_files_per_repo=50_000,
        max_file_bytes=1_500_000,
        max_line_bytes=50_000,
        force=False,
        repo=None,
        limit=0,
        api_clients="/etc/ci-secrets/api-clients.json",
        prescreen_indicators=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_manifest_records_provenance_without_secrets(tmp_path):
    rows = [
        {"group": "b", "name": "two", "git_sha": "b" * 40},
        {"group": "a", "name": "one", "git_sha": "a" * 40},
    ]
    _write_run_manifest(
        tmp_path, _args(), rows, skipped=3, timed_out=1,
        started_at="2026-07-01T00:00:00+00:00", finished_at="2026-07-01T00:05:00+00:00", total_seconds=1.0,
    )
    m = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))

    assert m["tool"] == "src2sink"
    assert m["tool_version"]
    assert m["counts"] == {
        "updated": 2, "skipped": 3, "timed_out": 1,
        # `OI-36`, 4.0 phase 1: what the fleet could not read, counted at the
        # run level so a note buried in one repo record among 746 is not the
        # only place it appears.
        "unparsed": {
            "source_files": 0, "manifests": 0,
            "repos_affected": 0, "records_unreadable": 0,
        },
        # `OI-43` step 4: repos per language whose resolution is limited, so a
        # coverage gap is a number on the run rather than prose in one record.
        "resolution_gaps": {},
    }
    # Per-repo SHAs recorded, sorted by repo id.
    assert m["updated_repos"][0] == {"repo": "a/one", "git_sha": "a" * 40}
    assert m["invocation"]["api_clients_configured"] is True
    assert m["invocation"]["repos_root"] == "repos"  # basename only

    # No absolute paths or the sensitive config filename leak into the manifest.
    blob = json.dumps(m)
    assert "/abs/secret-location" not in blob
    assert "/etc/ci-secrets" not in blob
    assert "api-clients.json" not in blob


def test_manifest_flags_disabled_config(tmp_path):
    _write_run_manifest(
        tmp_path, _args(api_clients=None, prescreen_indicators=None), [],
        skipped=0, timed_out=0, started_at="T0", finished_at="T1", total_seconds=1.0,
    )
    m = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert m["invocation"]["api_clients_configured"] is False
    assert m["invocation"]["prescreen_indicators_configured"] is False
    assert m["updated_repos"] == []


def test_tool_version_is_a_string():
    assert isinstance(_tool_version(), str) and _tool_version()
