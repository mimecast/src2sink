"""Regression tests for OI-16: a detection fix must reach unchanged repos.

The incremental scan skipped a repo whose git sha matched the sha in the existing
record. A record's contents are a function of *two* things — what was scanned and
what scanned it — so keying the cache on only the first meant every detection fix
stopped at the repos that happened to have committed since. Measured before the
fix: the false `sql` sink `OI-7` removed survived a build containing the `OI-7`
fix, indefinitely.

The cache key now carries the detector identity too, and a gate keeps that
identity honest: changing an extractor without bumping the version fails the
build, because "remember to bump it" is exactly the discipline that fails
silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src2sink.build_metabase_v2 import process_one_v2
from src2sink.schema import DETECTION_VERSION, SCHEMA_VERSION

_SHA = "a" * 40


def _repo(tmp_path: Path, source: str = "class A { void f(){ httpClient.execute(req); } }") -> Path:
    """Create a scannable repo directory with a fixed git sha."""
    root = tmp_path / "src" / "grp" / "svc"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(_SHA + "\n", encoding="utf-8")
    (root / "Api.java").write_text(source, encoding="utf-8")
    return root


def _seed_record(tmp_path: Path, **overrides: object) -> Path:
    """Write a prior-run record for grp/svc, with fields overridable."""
    d = tmp_path / "mb" / "repos" / "grp"
    d.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "group": "grp",
        "name": "svc",
        "git_sha": _SHA,
        "analysed_at": "2025-01-01T00:00:00+00:00",
        # The defect OI-7 removed: an HTTP client call catalogued as a SQL sink.
        "nodes": [{
            "family": "sql", "kind": "sink", "file": "Api.java", "line": 1,
            "detail": {"symbol": "execute", "raw": "httpClient.execute(req)"},
        }],
        "edges": [],
        "dependencies_internal": [],
    }
    record.update(overrides)
    (d / "svc.json").write_text(json.dumps(record), encoding="utf-8")
    return d / "svc.json"


def _scan(tmp_path: Path, repo: Path) -> dict[str, object]:
    """Run one repo through the incremental scanner."""
    return process_one_v2((repo, "grp", "svc", tmp_path / "mb", False)) or {}


def test_a_fresh_record_names_the_detector_that_made_it(tmp_path):
    """Without this the staleness is undetectable after the fact."""
    repo = _repo(tmp_path)
    (tmp_path / "mb").mkdir()
    _scan(tmp_path, repo)

    written = json.loads((tmp_path / "mb" / "repos" / "grp" / "svc.json").read_text())
    assert written["detection_version"] == DETECTION_VERSION


def test_an_unchanged_repo_is_still_skipped(tmp_path):
    """The incremental scan is what makes a fleet run affordable; it must survive."""
    repo = _repo(tmp_path)
    _seed_record(tmp_path, detection_version=DETECTION_VERSION)
    assert _scan(tmp_path, repo).get("_skipped") is True


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("older detector", {"detection_version": DETECTION_VERSION - 1}),
        ("no detector recorded", {}),
    ],
)
def test_a_stale_detector_forces_a_rescan(tmp_path, label, overrides):
    """Same source, different detector, so the record is stale and must be rebuilt.

    A record with no ``detection_version`` at all counts as stale: we genuinely
    cannot know what produced it, and assuming the current version is how the
    defect went unnoticed through six releases.
    """
    repo = _repo(tmp_path)
    _seed_record(tmp_path, **overrides)
    assert _scan(tmp_path, repo).get("_skipped") is not True, label


def test_the_false_sql_sink_does_not_survive_the_fix_that_removed_it(tmp_path):
    """The exact scenario measured in OI-16, end to end.

    `httpClient.execute(req)` is an HTTP call. A pre-OI-7 record catalogues it as
    an unparameterised SQL execution sink. Rescanning with a build that contains
    the OI-7 fix must replace that record, not preserve it.
    """
    repo = _repo(tmp_path)
    path = _seed_record(tmp_path)
    _scan(tmp_path, repo)

    after = json.loads(path.read_text())
    sql_sinks = [n for n in after["nodes"] if n["family"] == "sql" and n["kind"] == "sink"]
    assert sql_sinks == [], "the false sink outlived the fix that removed it"
    assert after["detection_version"] == DETECTION_VERSION
