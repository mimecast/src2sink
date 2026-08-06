"""OI-28: the index lookup bypasses the significance filter, one layer up.

`OI-24` moved the equality shortcut in `path_templates_match` below the guard
that a path reducing to no significant segments names nothing. That fixed the
predicate — and the service-call edges never went through it.

`match_path_in_inbound_index` looks the normalised path up in a dict first, and a
hit returns `high` without consulting the predicate at all. So two repos each
exposing a bare `/v1` still produce a confident cross-repo edge, from a different
code path.

Reported from the field against 2.1.0, and the wording is the lesson: *same issue
in the caller instead of the callee*. `OI-24` was verified against the function
the issue named, and nothing checked whether the callers reached it.

The fix belongs at the top of the lookup rather than inside the fast path,
because the fuzzy pass below would reject the same path anyway — a path that
names nothing has no business being matched by any route.
"""

from __future__ import annotations

import pytest

from src2sink.graph_common import match_path_in_inbound_index, path_templates_match

_INBOUND = {
    "/v1": [("pricing/price-index", "/v1")],
    "/api": [("billing/tax-service", "/api")],
    "/{}": [("shipping/label-store", "/{id}")],
    "/stock": [("commerce/warehouse-service", "/stock")],
    "/stock/dispatch": [("commerce/warehouse-service", "/stock/dispatch")],
}


@pytest.mark.parametrize("path", ["/v1", "/api", "/{id}", "/service", "/internal"])
def test_a_path_that_names_nothing_matches_no_route(path):
    """The dict hit must not outrank the guard the predicate already applies."""
    assert path_templates_match(path, path) is None, "predicate must already reject it"
    rows, _conf = match_path_in_inbound_index(path, _INBOUND)
    assert rows == []


def test_a_real_route_still_matches_exactly():
    """The fast path exists for a reason and must keep working."""
    rows, conf = match_path_in_inbound_index("/stock", _INBOUND)
    assert rows == [("commerce/warehouse-service", "/stock")]
    assert conf == "high"


def test_a_version_prefixed_route_still_resolves():
    """`/v1/stock` names `stock`, so it is a route — unlike `/v1` alone."""
    rows, conf = match_path_in_inbound_index("/v1/stock", _INBOUND)
    assert rows == [("commerce/warehouse-service", "/stock")]
    assert conf == "medium"


def test_the_memo_does_not_cache_a_bypassed_answer():
    """A memo shared across call sites must not carry the defect forward.

    The lookup memoises by normalised path, so a wrong answer computed once is
    returned for every later call site with the same path.
    """
    memo: dict = {}
    first, _ = match_path_in_inbound_index("/v1", _INBOUND, memo=memo)
    second, _ = match_path_in_inbound_index("/v1", _INBOUND, memo=memo)
    assert first == [] and second == []


def test_the_edge_collector_produces_no_edge_for_a_bare_version_path():
    """End to end, because the predicate was fixed and the edges were not.

    This is the level the report was made at: the finding a reviewer sees is a
    cross-repo edge, not a call into the predicate.
    """
    from src2sink.aggregators.service_call_collect import collect_service_edges

    consumer = {
        "schema_version": 2, "group": "fulfilment", "name": "fulfilment-commons",
        "nodes": [{
            "family": "http-out", "kind": "sink", "file": "Client.java", "line": 7,
            "detail": {"path": "/v1", "raw": 'rest.postForObject("/v1", body)'},
        }],
        "edges": [], "dependencies_internal": [],
    }
    provider = {
        "schema_version": 2, "group": "pricing", "name": "price-index",
        "nodes": [{
            "family": "http-in", "kind": "source", "file": "Api.java", "line": 3,
            "detail": {"path": "/v1", "method": "GET"},
        }],
        "edges": [], "dependencies_internal": [],
    }
    edges, _unmatched = collect_service_edges([consumer, provider])
    assert [e for e in edges if e.target_repo == "pricing/price-index"] == []
