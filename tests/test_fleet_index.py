"""OI-15: a trace queries a persisted index instead of loading the fleet.

`run_trace` read every repo record in the metabase to answer a question about one
repo. Deserialised JSON runs ~6.5x its size on disk, so a 34 GB fleet needs
~222 GB resident merely to be held. Past that the tool does not run slowly — it
is killed, with no partial result and nothing to bisect.

The issue suggests asserting peak RSS. These tests do not, because an RSS bound
is machine-dependent, flaky under a parallel CI runner, and — since
`ru_maxrss` is a high-water mark that never falls — cannot even observe the
thing it claims to. The structural assertion is stronger and exact: make loading
the fleet *raise*, and require the trace to succeed anyway. A trace that passes
that provably held no fleet-wide structure, on any machine.

The other risk a cache introduces is answering confidently from data that no
longer describes the metabase, so staleness gets as much attention here as
speed. A wrong answer served fast is worse than the problem being fixed.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src2sink import graph_common as gc
from src2sink import trace as trace_mod
from src2sink.aggregators.graphs import write_fleet_index
from src2sink.index_store import (
    INDEX_VERSION,
    fleet_signature,
    index_path,
    open_index,
)
from src2sink.trace import run_trace

_CONSUMER = {
    "schema_version": 2,
    "group": "fulfilment",
    "name": "fulfilment-commons",
    "nodes": [
        {"family": "http-out", "kind": "sink", "file": "Client.java", "line": 7,
         "detail": {"path": "/stock", "raw": 'rest.postForObject("http://warehouse-service/stock", body)'}},
    ],
    "edges": [],
    "dependencies_internal": [],
}

_SECOND_CONSUMER = {
    "schema_version": 2,
    "group": "billing",
    "name": "invoice-service",
    "nodes": [
        {"family": "http-out", "kind": "sink", "file": "Caller.java", "line": 12,
         "detail": {"path": "/stock", "raw": 'client.get("http://warehouse-service/stock/dispatch")'}},
    ],
    "edges": [],
    "dependencies_internal": [],
}

_TARGET = {
    "schema_version": 2,
    "group": "commerce",
    "name": "warehouse-service",
    "nodes": [
        {"family": "http-in", "kind": "source", "file": "Api.java", "line": 3,
         "detail": {"path": "/stock", "method": "POST"}},
        {"family": "sql", "kind": "sink", "file": "Dao.java", "line": 22,
         "detail": {"symbol": "execute", "raw": "SELECT ref FROM stock", "execution": True}},
    ],
    "edges": [],
    "dependencies_internal": [],
}

_ALL = (_CONSUMER, _SECOND_CONSUMER, _TARGET)
_TARGET_ID = "commerce/warehouse-service"


def _seed(tmp_path):
    """Write a three-repo metabase and return its record paths."""
    paths = []
    for record in _ALL:
        d = tmp_path / "repos" / record["group"]
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{record['name']}.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        paths.append(path)
    return sorted(paths)


def _forbid_fleet_load(monkeypatch):
    """Make loading the fleet an error, so only a query-based trace can pass.

    This is the whole point of `OI-15` expressed as an assertion: not "uses less
    memory", but "never has the opportunity to use it".
    """
    def explode(*_a, **_kw):
        raise AssertionError(
            "the fleet was loaded — this is exactly what OI-15 removes"
        )

    monkeypatch.setattr(gc, "load_v2_repo_records", explode)
    monkeypatch.setattr(trace_mod, "load_v2_repo_records", explode)


def test_an_indexed_trace_never_loads_the_fleet(tmp_path, monkeypatch):
    """The load-bearing assertion. Everything else is about not being wrong."""
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    _forbid_fleet_load(monkeypatch)
    report = run_trace(tmp_path, _TARGET_ID)

    assert report.target_repo == _TARGET_ID
    # Two callers, each found by two independent routes (a graph edge and a raw
    # literal), so four hits — asserted in full rather than deduplicated, since
    # losing an evidence kind is exactly the kind of silent regression the
    # indexed path could introduce.
    assert sorted((h.source_repo, h.kind) for h in report.upstream) == [
        ("billing/invoice-service", "http-out-graph"),
        ("billing/invoice-service", "http-out-raw"),
        ("fulfilment/fulfilment-commons", "http-out-graph"),
        ("fulfilment/fulfilment-commons", "http-out-raw"),
    ]


def test_the_index_and_a_fresh_computation_agree(tmp_path):
    """The artefact must not drift from the code that reads it.

    Both paths run over the same metabase and must produce the same report — if
    they can differ, the index is a second implementation rather than a cache,
    and every later change has to be made twice.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    indexed = run_trace(tmp_path, _TARGET_ID)
    computed = run_trace(tmp_path, _TARGET_ID, use_index=False)

    def shape(report):
        return (
            report.target_repo,
            [(h.source_repo, h.kind, h.confidence, h.evidence, h.ref)
             for h in report.upstream],
            report.inbound,
            report.sql_sinks,
        )

    assert shape(indexed) == shape(computed)


@pytest.mark.parametrize("path_filter", [None, "/stock", "/stock/dispatch", "/absent"])
def test_the_two_paths_agree_under_every_filter(tmp_path, path_filter):
    """Filtering is applied in both paths separately, so it is checked in both.

    A filter handled in only one of them would give a correct unfiltered answer
    and a silently different filtered one — the kind of divergence that surfaces
    as a missing caller rather than as an error.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    indexed = run_trace(tmp_path, _TARGET_ID, path_filter=path_filter)
    computed = run_trace(tmp_path, _TARGET_ID, path_filter=path_filter, use_index=False)

    assert [(h.source_repo, h.kind) for h in indexed.upstream] == \
           [(h.source_repo, h.kind) for h in computed.upstream]


def test_a_stale_index_is_not_used(tmp_path, monkeypatch):
    """A metabase that changed after the index was built must invalidate it.

    The failure this prevents is the dangerous one: an answer that is confident,
    fast, and describes a fleet that no longer exists.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)
    assert open_index(tmp_path, paths) is not None, "index should start fresh"

    changed = dict(_SECOND_CONSUMER)
    changed["nodes"] = [
        {"family": "http-out", "kind": "sink", "file": "Caller.java", "line": 12,
         "detail": {"path": "/other", "raw": 'client.get("http://elsewhere/other")'}},
    ]
    (tmp_path / "repos" / "billing" / "invoice-service.json").write_text(
        json.dumps(changed), encoding="utf-8",
    )

    assert open_index(tmp_path, gc.v2_record_paths(tmp_path)) is None


def test_a_stale_index_falls_back_rather_than_failing(tmp_path):
    """Detecting staleness is only useful if the trace still answers.

    Rejecting the index must degrade to the slow path, not to an error — the
    tool has to keep working on a metabase someone has just rebuilt.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    (tmp_path / "repos" / "billing" / "invoice-service.json").write_text(
        json.dumps({**_SECOND_CONSUMER, "nodes": []}), encoding="utf-8",
    )

    report = run_trace(tmp_path, _TARGET_ID)
    # The edited repo no longer calls the target, so the fallback must report
    # the *current* fleet rather than the one the index was built from.
    assert {h.source_repo for h in report.upstream} == {"fulfilment/fulfilment-commons"}


def test_a_new_repo_invalidates_the_index(tmp_path):
    """Staleness is not only about edits. A repo appearing is a changed fleet."""
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    d = tmp_path / "repos" / "shipping"
    d.mkdir(parents=True)
    (d / "label-store.json").write_text(json.dumps({
        "schema_version": 2, "group": "shipping", "name": "label-store",
        "nodes": [], "edges": [], "dependencies_internal": [],
    }), encoding="utf-8")

    assert open_index(tmp_path, gc.v2_record_paths(tmp_path)) is None


def test_a_removed_repo_invalidates_the_index(tmp_path):
    """And a repo disappearing, which a count-based check would also catch but a
    "max mtime" one would not."""
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    (tmp_path / "repos" / "billing" / "invoice-service.json").unlink()
    assert open_index(tmp_path, gc.v2_record_paths(tmp_path)) is None


def test_an_index_from_a_different_version_is_rejected(tmp_path, monkeypatch):
    """The stored layout must not be read by code expecting a different one."""
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    with sqlite3.connect(index_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'index_version'",
            (str(INDEX_VERSION + 1),),
        )

    assert open_index(tmp_path, paths) is None


def test_a_missing_index_is_a_cache_miss_not_an_error(tmp_path):
    """A metabase built before this existed must still trace."""
    _seed(tmp_path)
    assert open_index(tmp_path, gc.v2_record_paths(tmp_path)) is None

    report = run_trace(tmp_path, _TARGET_ID)
    assert report.target_repo == _TARGET_ID
    assert report.upstream, "the fallback path must still find callers"


def test_a_corrupt_index_is_a_cache_miss_not_an_error(tmp_path):
    """A truncated or foreign file must not take the tool down.

    The index sits in a directory users copy, sync and archive, so arriving at a
    file that is not what it claims to be is a matter of time.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)
    index_path(tmp_path).write_bytes(b"this is not a database")

    assert open_index(tmp_path, paths) is None
    assert run_trace(tmp_path, _TARGET_ID).target_repo == _TARGET_ID


def test_an_unknown_target_still_fails_cleanly_through_the_index(tmp_path):
    """A missing repo must be reported, not surfaced as an index miss."""
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    with pytest.raises(SystemExit, match="not found"):
        run_trace(tmp_path, "nope/does-not-exist")


def test_supplied_fleet_data_bypasses_the_index(tmp_path):
    """A batch caller that already holds the fleet must not be second-guessed.

    `OI-14` made batch tracing build the fleet derivations once and pass them
    down. Silently preferring the index would discard that and re-query per
    target, so the explicit argument wins.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)
    records = gc.load_v2_repo_records(tmp_path)

    report = run_trace(tmp_path, _TARGET_ID, records=records, service_edges=[])
    assert report.target_repo == _TARGET_ID


def test_the_signature_folds_in_the_versions_that_produced_the_records(tmp_path):
    """A record's meaning can change without its bytes changing.

    `DERIVATION_VERSION` governs what is derived from a record; if it moves, an
    index built under the old one describes findings the tool no longer agrees
    with. Size and mtime cannot see that, so the versions are hashed in.
    """
    import src2sink.index_store as store

    paths = _seed(tmp_path)
    before = fleet_signature(paths)

    monkey = store.DERIVATION_VERSION + 1
    original, store.DERIVATION_VERSION = store.DERIVATION_VERSION, monkey
    try:
        assert fleet_signature(paths) != before
    finally:
        store.DERIVATION_VERSION = original


def test_the_index_covers_every_repo_in_the_metabase(tmp_path):
    """A repo missing from the locator would be untraceable through the index."""
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    with open_index(tmp_path, paths) as index:
        assert index.repo_ids() == [
            "billing/invoice-service",
            "commerce/warehouse-service",
            "fulfilment/fulfilment-commons",
        ]


def test_a_vanished_record_invalidates_rather_than_crashing(tmp_path):
    """A record named in the index but gone from disk is a changed fleet.

    Distinct from the glob-based case above: here the caller still holds the old
    path list, so the check has to survive statting a file that is not there and
    treat its absence as a difference rather than raising.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)
    before = fleet_signature(paths)

    (tmp_path / "repos" / "billing" / "invoice-service.json").unlink()

    assert fleet_signature(paths) != before
    assert open_index(tmp_path, paths) is None


def test_producer_hits_are_recorded_against_their_target(tmp_path):
    """The producer table is keyed by target, and an empty read must be empty.

    A query that silently returned every hit regardless of target would look
    correct on a fleet with one binding and wrong on a real one.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    with open_index(tmp_path, paths) as index:
        assert list(index.producer_hits_for("nobody/at-all")) == []
        for row in index.producer_hits_for(_TARGET_ID):
            assert row.target_repo == _TARGET_ID


def test_only_outbound_families_are_stored(tmp_path):
    """The table stays small because it holds a subset, and that must stay true.

    If every family leaked in, the scan in `outbound_nodes` would be over the
    whole fleet again and the memory ceiling would come back by the side door.
    """
    paths = _seed(tmp_path)
    write_fleet_index(tmp_path, paths)

    with open_index(tmp_path, paths) as index:
        families = {n.family for n in index.outbound_nodes()}
    assert families == {"http-out"}, "sql/http-in nodes must not be stored"
