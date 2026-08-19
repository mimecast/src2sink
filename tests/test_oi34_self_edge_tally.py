"""`OI-34` sizing: the self-edges the service-call collector refuses are counted.

`OI-34` is justified on a hop that cannot currently be seen. A call **from a
client library to the service it fronts** is a call within one repo id, so
`_append_path_edge` drops it as a self-edge — the hop the client exists to make
is the hop that disappears. Those edges never reach a written record, so no
amount of reading the output can count them.

`collect_service_edges` therefore takes an optional tally. These tests hold the
two properties that make it a measurement: it records what was refused, and it
changes nothing about what is emitted.
"""

from __future__ import annotations

from typing import Any

from src2sink.aggregators.service_call_collect import collect_service_edges


def _record(group: str, name: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """One metabase record with the given nodes."""
    return {
        "schema_version": 2, "group": group, "name": name,
        "nodes": nodes, "edges": [],
    }


def _serves_and_calls_itself() -> dict[str, Any]:
    """A repo exposing `/items` and also calling `/items` — a self-edge."""
    return _record("grp", "svc", [
        {
            "family": "http-in", "kind": "source", "file": "ItemsController.java",
            "line": 12, "detail": {"method": "GET", "path": "/items"},
        },
        {
            # A quoted path literal is what the extractors actually record, and
            # what `extract_urls_and_paths` matches — a bare `/items` in the
            # blob is not recognised as a path at all.
            "family": "http-out", "kind": "sink", "file": "ItemsClient.java",
            "line": 30, "detail": {"raw": 'restTemplate.getForObject("/items")'},
        },
    ])


def _caller_in_another_repo() -> dict[str, Any]:
    """A different repo calling the same route — a genuine cross-repo edge."""
    return _record("grp", "client", [
        {
            "family": "http-out", "kind": "sink", "file": "Client.java",
            "line": 8, "detail": {"raw": 'restTemplate.getForObject("/items")'},
        },
    ])


def test_self_edge_is_tallied_rather_than_emitted() -> None:
    """A repo calling its own route yields no edge, and one tally entry."""
    dropped: set[tuple[str, str, str]] = set()
    edges, _broken = collect_service_edges([_serves_and_calls_itself()], dropped=dropped)

    assert not [e for e in edges if e.source_repo == e.target_repo], (
        "a self-edge must never be emitted; that is the behaviour being measured"
    )
    assert {repo for repo, _path, _conf in dropped} == {"grp/svc"}


def test_cross_repo_edge_is_emitted_and_not_tallied() -> None:
    """A real cross-repo hop is unaffected by the tally existing."""
    dropped: set[tuple[str, str, str]] = set()
    records = [_serves_and_calls_itself(), _caller_in_another_repo()]
    edges, _broken = collect_service_edges(records, dropped=dropped)

    assert any(e.source_repo == "grp/client" and e.target_repo == "grp/svc" for e in edges)
    assert "grp/client" not in {repo for repo, _path, _conf in dropped}


def test_tally_does_not_change_what_is_emitted() -> None:
    """The load-bearing property: a measurement must not change what it measures."""
    records = [_serves_and_calls_itself(), _caller_in_another_repo()]

    without, broken_without = collect_service_edges(records)
    with_tally, broken_with = collect_service_edges(records, dropped=set())

    def key(edges: list[Any]) -> list[tuple[str, str, str, str]]:
        return sorted(
            (e.source_repo, e.target_repo, e.target_path, e.confidence) for e in edges
        )

    assert key(without) == key(with_tally)
    assert broken_without == broken_with


def test_repeated_self_edges_are_deduplicated_like_real_edges() -> None:
    """Two call sites for one self-edge count once, so the tally sizes edges not sites."""
    record = _serves_and_calls_itself()
    record["nodes"].append({
        "family": "http-out", "kind": "sink", "file": "Other.java",
        "line": 40, "detail": {"raw": 'restTemplate.getForObject("/items")'},
    })
    dropped: set[tuple[str, str, str]] = set()
    collect_service_edges([record], dropped=dropped)

    assert len(dropped) == 1
