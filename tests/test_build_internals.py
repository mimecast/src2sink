"""In-process tests for build_metabase_v2 worker-path functions.

process_one_v2 and _worker_init normally run in spawned workers, so their
coverage is invisible to the parent coverage run. Calling them directly here
exercises the per-repo extract → render → write path and the worker config.
"""

from __future__ import annotations

import json

from src2sink import build_metabase_v2 as b
from src2sink.build_metabase_v2 import (
    _existing_record_is_current,
    _worker_init,
    process_one_v2,
)
from src2sink.schema import DETECTION_VERSION

VALID_SHA = "a" * 40


def _make_repo(tmp_path):
    repo = tmp_path / "repos" / "g" / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "A.java").write_text(
        '@RestController class A { @GetMapping("/x") String x() { return "ok"; } }',
        encoding="utf-8",
    )
    return repo


def test_process_one_v2_extracts_and_writes(tmp_path):
    repo = _make_repo(tmp_path)
    metabase = tmp_path / "metabase"
    result = process_one_v2((repo, "g", "r", metabase, True))
    assert result["group"] == "g" and result["name"] == "r"
    assert "nodes" in result and "git_sha" in result
    assert (metabase / "repos" / "g" / "r.json").is_file()
    assert (metabase / "repos" / "g" / "r.md").is_file()


def test_process_one_v2_skips_unchanged(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text(VALID_SHA + "\n", encoding="utf-8")
    metabase = tmp_path / "metabase"
    out = metabase / "repos" / "g"
    out.mkdir(parents=True)
    (out / "r.json").write_text(
        json.dumps({"git_sha": VALID_SHA, "detection_version": DETECTION_VERSION}),
        encoding="utf-8",
    )

    result = process_one_v2((repo, "g", "r", metabase, False))  # force=False
    assert result == {"_skipped": True, "group": "g", "name": "r"}


def test_existing_record_is_current(tmp_path):
    """A record is reusable only if both the source and the detector match (OI-16)."""
    jp = tmp_path / "r.json"
    assert _existing_record_is_current(jp, VALID_SHA) is False  # no file

    jp.write_text(
        json.dumps({"git_sha": VALID_SHA, "detection_version": DETECTION_VERSION}),
        encoding="utf-8",
    )
    assert _existing_record_is_current(jp, VALID_SHA) is True
    assert _existing_record_is_current(jp, "b" * 40) is False  # source moved

    # Same source, older detector: the record predates a detection change and
    # must be rebuilt rather than trusted.
    jp.write_text(
        json.dumps({"git_sha": VALID_SHA, "detection_version": DETECTION_VERSION - 1}),
        encoding="utf-8",
    )
    assert _existing_record_is_current(jp, VALID_SHA) is False

    # Predates the field entirely, so what produced it is unknowable.
    jp.write_text(json.dumps({"git_sha": VALID_SHA}), encoding="utf-8")
    assert _existing_record_is_current(jp, VALID_SHA) is False

    jp.write_text("{bad", encoding="utf-8")
    assert _existing_record_is_current(jp, VALID_SHA) is False


def test_worker_init_configures_globals():
    import src2sink.prescreen as ps
    _worker_init([r"^com\.acme(\..+)?$"], "", 123, 4096, 999, ("bad-indicator",))
    assert b._MAX_FILES_PER_REPO == 123
    assert b._MAX_FILE_BYTES == 4096
    import src2sink.repo_utils as ru
    assert ru.MAX_FILE_BYTES == 4096  # propagated to manifest read paths too
    assert ps._MAX_LINE_BYTES == 999  # propagated to the pre-screen
    # Reset the caps so they don't leak into other tests.
    _worker_init(
        [r"^com\.acme(\..+)?$"], "", b.DEFAULT_MAX_FILES_PER_REPO, b.MAX_FILE_BYTES,
        ps.MAX_LINE_BYTES, (),
    )
