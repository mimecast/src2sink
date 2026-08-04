"""Regression tests for OI-14: trace cost over a whole fleet.

Two independent defects, both measured on a synthetic fleet before the fix:

* ``run_trace`` rebuilt the *entire* fleet service-call graph for every target,
  even though that graph is target-independent — a batch of N targets paid the
  cost N times.
* the graph build itself was O(nodes x routes), because each path lookup
  re-normalised every candidate route string from scratch.

Neither is observable from output, so both are guarded structurally: by counting
how often the fleet-wide collector runs, and by asserting the pure path helpers
memoise. A timing assertion would be flaky in CI and is deliberately avoided.
"""

from __future__ import annotations

import json

import pytest

import src2sink.graph_common as gc
from src2sink.trace import run_trace
from src2sink.trace_batch import batch_trace

_CONSUMER = {
    "schema_version": 2,
    "group": "fulfilment",
    "name": "fulfilment-commons",
    "nodes": [
        {"family": "http-out", "kind": "sink", "file": "Client.java", "line": 7,
         "detail": {"path": "/stock", "raw": 'rest.postForObject("/stock", body)'}},
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
        {"family": "raw-code-payload", "kind": "sink", "file": "Api.java", "line": 9,
         "detail": {"endpoint_path": "/stock", "sink_symbol": "exec"}},
    ],
    "edges": [],
    "dependencies_internal": [],
}


def _seed(tmp_path, targets=(("commerce/warehouse-service", "/stock"),)):
    """Write a two-repo metabase plus a catalogue naming ``targets``."""
    for record in (_CONSUMER, _TARGET):
        d = tmp_path / "repos" / record["group"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{record['name']}.json").write_text(json.dumps(record), encoding="utf-8")
    taint = tmp_path / "taint"
    taint.mkdir(exist_ok=True)
    (taint / "raw-code-payload-endpoints.jsonl").write_text(
        "".join(
            json.dumps({"repo": repo, "detail": {"endpoint_path": path}}) + "\n"
            for repo, path in targets
        ),
        encoding="utf-8",
    )


def _count_collector_calls(monkeypatch, module):
    """Patch ``module.collect_service_edges`` with a counting passthrough."""
    from src2sink.aggregators.service_calls import collect_service_edges as real

    calls: list[int] = []

    def counted(records):
        calls.append(1)
        return real(records)

    monkeypatch.setattr(module, "collect_service_edges", counted)
    return calls


def test_run_trace_accepts_precomputed_service_edges(tmp_path):
    """Supplying the fleet edge list must not change what the trace reports."""
    _seed(tmp_path)
    from src2sink.aggregators.service_calls import collect_service_edges
    from src2sink.graph_common import load_v2_repo_records

    records = load_v2_repo_records(tmp_path)
    edges, _ = collect_service_edges(records)

    computed = run_trace(tmp_path, "commerce/warehouse-service")
    supplied = run_trace(
        tmp_path, "commerce/warehouse-service", records=records, service_edges=edges,
    )
    assert [(h.source_repo, h.kind, h.confidence) for h in supplied.upstream] == [
        (h.source_repo, h.kind, h.confidence) for h in computed.upstream
    ]
    assert supplied.upstream, "fixture should produce at least one upstream hit"


def test_run_trace_does_not_rebuild_supplied_edges(tmp_path, monkeypatch):
    """Given edges, the trace must not recompute the fleet-wide graph."""
    _seed(tmp_path)
    import src2sink.trace as trace_mod
    from src2sink.aggregators.service_calls import collect_service_edges
    from src2sink.graph_common import load_v2_repo_records

    records = load_v2_repo_records(tmp_path)
    edges, _ = collect_service_edges(records)
    calls = _count_collector_calls(monkeypatch, trace_mod)

    run_trace(
        tmp_path, "commerce/warehouse-service", records=records, service_edges=edges,
    )
    assert calls == [], "supplied edges were ignored and the fleet graph rebuilt"


def test_batch_trace_builds_the_fleet_graph_once(tmp_path, monkeypatch):
    """A batch over N targets must pay the fleet-graph cost once, not N times."""
    _seed(tmp_path, targets=(
        ("commerce/warehouse-service", "/stock"),
        ("commerce/warehouse-service", "/stock/dispatch"),
        ("commerce/warehouse-service", "/stock/reserve"),
    ))
    import src2sink.trace as trace_mod
    import src2sink.trace_batch as batch_mod

    # Count builds wherever they happen: the batch hoist and the per-target path
    # both draw on the same budget of one.
    calls = _count_collector_calls(monkeypatch, batch_mod)
    monkeypatch.setattr(
        trace_mod, "collect_service_edges", getattr(batch_mod, "collect_service_edges"),
    )

    written, _skipped, errors = batch_trace(tmp_path)
    assert (written, errors) == (3, 0)
    assert len(calls) == 1, f"fleet graph rebuilt {len(calls)}x for 3 targets"


def test_significant_segments_cannot_be_mutated_by_a_caller():
    """The memoised segment split must not hand out a mutable shared object.

    Returning a cached ``list`` would let one caller's edit corrupt every later
    lookup of the same path, which is why this returns a tuple.
    """
    first = gc._significant_segments("/v1/stock/dispatch")
    assert first == ("stock", "dispatch")
    with pytest.raises((AttributeError, TypeError)):
        first.append("injected")  # type: ignore[attr-defined]
    assert gc._significant_segments("/v1/stock/dispatch") == ("stock", "dispatch")


def test_path_helpers_are_memoised():
    """Repeated normalisation of the same route must hit a cache, not recompute.

    The fleet graph build normalises the same handful of routes millions of
    times; without memoisation the build is quadratic in fleet size (OI-14).
    """
    for fn in (gc.normalize_path_template, gc._significant_segments):
        assert hasattr(fn, "cache_info"), f"{fn.__name__} is not memoised"
        fn.cache_clear()

    for _ in range(5):
        gc.normalize_path_template("/v1/stock/{id}")
        gc._significant_segments("/v1/stock/{}")

    assert gc.normalize_path_template.cache_info().hits >= 4
    assert gc._significant_segments.cache_info().hits >= 4


def test_path_cache_is_bounded():
    """Path strings come from scanned repos, so the cache must not grow without limit."""
    for fn in (gc.normalize_path_template, gc._significant_segments):
        assert fn.cache_info().maxsize is not None, (
            f"{fn.__name__} has an unbounded cache; untrusted paths would grow it forever"
        )
