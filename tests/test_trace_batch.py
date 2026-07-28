"""Tests for trace_batch and the traces index writer."""

from __future__ import annotations

import json

import pytest

from src2sink.aggregators.traces_index import write_traces_index
from src2sink.trace_batch import _safe_slug, batch_trace, load_catalogue_targets

_TARGET_JSON = {
    "schema_version": 2,
    "group": "g",
    "name": "r",
    "nodes": [
        {"family": "http-in", "kind": "source", "file": "C.java", "line": 1,
         "detail": {"path": "/run", "method": "POST"}},
        {"family": "raw-code-payload", "kind": "sink", "file": "C.java", "line": 2,
         "detail": {"endpoint_path": "/run", "sink_symbol": "exec"}},
    ],
}


def _seed_metabase(tmp_path):
    repos = tmp_path / "repos" / "g"
    repos.mkdir(parents=True)
    (repos / "r.json").write_text(json.dumps(_TARGET_JSON), encoding="utf-8")
    taint = tmp_path / "taint"
    taint.mkdir()
    (taint / "raw-code-payload-endpoints.jsonl").write_text(
        json.dumps({"repo": "g/r", "detail": {"endpoint_path": "/run"}}) + "\n",
        encoding="utf-8",
    )


def test_safe_slug():
    assert _safe_slug("g/r", "/a/b") == "g-r-a-b"
    assert _safe_slug("g/r", "/") == "g-r-root"


def test_load_catalogue_targets(tmp_path):
    _seed_metabase(tmp_path)
    assert load_catalogue_targets(tmp_path) == [("g/r", "/run")]


def test_load_catalogue_targets_missing(tmp_path):
    with pytest.raises(SystemExit):
        load_catalogue_targets(tmp_path)


def test_batch_trace_and_index(tmp_path):
    _seed_metabase(tmp_path)
    written, skipped, errors = batch_trace(tmp_path)
    assert written == 1 and errors == 0
    trace_md = tmp_path / "graphs" / "traces" / "g-r-run.md"
    assert trace_md.is_file()

    # skip-existing on a second run.
    w2, s2, _ = batch_trace(tmp_path, skip_existing=True)
    assert s2 == 1 and w2 == 0

    count = write_traces_index(tmp_path)
    assert count == 1
    index = (tmp_path / "graphs" / "traces" / "INDEX.md").read_text(encoding="utf-8")
    assert "g/r" in index and "Catalogue coverage" in index


def test_write_traces_index_no_dir(tmp_path):
    assert write_traces_index(tmp_path) == 0
