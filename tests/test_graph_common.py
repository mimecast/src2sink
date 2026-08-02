"""Unit tests for the graph_common matching/normalisation helpers."""

from __future__ import annotations

import json

import pytest

from src2sink import graph_common as gc


def test_repo_id_and_iter_nodes():
    data = {"group": "g", "name": "r", "nodes": [{"family": "sql"}, {"family": "http-in"}]}
    assert gc.repo_id(data) == "g/r"
    nodes = list(gc.iter_nodes(data))
    assert len(nodes) == 2 and all(n["repo"] == "g/r" for n in nodes)


def test_normalize_path_template():
    assert gc.normalize_path_template("") == ""
    assert gc.normalize_path_template("?") == ""
    assert gc.normalize_path_template("queries/{id}") == "/queries/{}"
    assert gc.normalize_path_template("/queries/:id/") == "/queries/{}"
    assert gc.normalize_path_template("//a//b//") == "/a/b"


def test_path_templates_match_levels():
    assert gc.path_templates_match("/queries", "/queries") == "high"
    # OI-1: `/api` names no destination, so it must match nothing. It used to win
    # a `medium` prefix match against every `/api/...` path in the fleet.
    assert gc.path_templates_match("/api/queries", "/api") is None
    # OI-1: the version prefix carries no routing information, so this is the
    # same route — it used to rank `low` and lose to the bogus `/api` match.
    assert gc.path_templates_match("/api/v1/queries", "/queries") == "medium"
    assert gc.path_templates_match("/a", "/b") is None
    assert gc.path_templates_match("", "/x") is None


# (outbound, inbound, expected) — the OI-1 confidence table. Rows marked with a
# trailing comment are the ones whose behaviour the fix changes.
_MATCH_TABLE = [
    ("/v1/stock", "/stock", "medium"),                    # was low
    ("/v1/stock", "/v1", None),                           # was medium — the defect
    ("/v1/stock/dispatch", "/stock/dispatch", "medium"),  # was low
    ("/api/v2/pallets", "/pallets", "medium"),            # was low
    ("/api/queries", "/api", None),                       # was medium — the defect
    ("/queries/{handle}", "/queries", "medium"),          # unchanged: child route
    ("/stock/dispatch", "/stock", "medium"),              # unchanged: child route
    ("/v1/orders/{id}", "/orders", "medium"),             # was None — new recall
    ("/v1/reservations", "/reservations/{ref}", "medium"),  # was None — new recall
    ("/orders/{id}/lines", "/lines", "low"),              # unchanged: tail overlap
    ("/api", "/api/v1/queries", None),                    # was medium — the defect
    ("/stock", "/stock", "high"),
    ("/v1/stock", "/v1/reservations", None),
]


@pytest.mark.parametrize(("outbound", "inbound", "expected"), _MATCH_TABLE)
def test_path_templates_match_ignores_version_and_generic_segments(outbound, inbound, expected):
    """OI-1: confidence must reflect how much *meaning* matched, not which rule fired."""
    assert gc.path_templates_match(outbound, inbound) == expected


@pytest.mark.parametrize(("outbound", "inbound", "expected"), _MATCH_TABLE)
def test_path_templates_match_is_direction_free(outbound, inbound, expected):
    """The relation is symmetric: swapping the arguments cannot change the label.

    1.1.0 was asymmetric by accident — `o.startswith(i + "/")` and its mirror
    returned the same label, but the suffix rule only ran one way.
    """
    assert gc.path_templates_match(inbound, outbound) == expected


@pytest.mark.parametrize("bare", ["/v1", "/api", "/v2/", "/rest", "/internal", "/service"])
def test_a_side_with_no_significant_segment_matches_nothing(bare):
    """A path that reduces to a version or a generic word names no destination."""
    assert gc.path_templates_match("/v1/stock", bare) is None
    assert gc.path_templates_match(bare, "/v1/stock") is None


def test_path_filter_matches_keeps_prefix_semantics():
    """`trace --path` asks a different question and must keep the looser answer.

    "Show me everything under /v1" is a filter; "do these two routes denote the
    same endpoint" is routing. Sharing one predicate meant the OI-1 fix would
    silently empty `--path /v1` (finding F2).
    """
    assert gc.path_filter_matches("/v1/stock", "/v1") is True
    assert gc.path_templates_match("/v1/stock", "/v1") is None
    assert gc.path_filter_matches("/v1/stock", "/v1/stock") is True
    assert gc.path_filter_matches("/v1/stock", "/v1/reservations") is False
    assert gc.path_filter_matches("/v1/stock", "") is True


def test_extract_urls_and_paths():
    hosts, paths = gc.extract_urls_and_paths('call("http://sql-runner-api/queries?x=1")')
    assert "sql-runner-api" in hosts
    assert "/queries" in paths


def test_repo_name_aliases_and_host_match():
    aliases = gc.repo_name_aliases("sql-runner-api")
    assert "sql-runner-api" in aliases and "sql_runner_api" in aliases
    assert gc.host_matches_repo("sql-runner-api.internal", "acme/sql-runner-api")
    assert not gc.host_matches_repo("unrelated", "acme/sql-runner-api")


def test_alias_index_and_resolve():
    records = [{"group": "acme", "name": "sql-runner-api", "nodes": []}]
    idx = gc.build_repo_alias_index(records)
    assert gc.resolve_repo_for_host("sql-runner-api.svc", idx) == "acme/sql-runner-api"
    assert gc.resolve_repo_for_host("localhost", idx) is None


def test_match_path_in_inbound_index():
    inbound = {"/queries": [("acme/svc", "/queries")]}
    rows, conf = gc.match_path_in_inbound_index("/queries", inbound)
    assert rows and conf == "high"
    rows2, _ = gc.match_path_in_inbound_index("/api/v1/queries", inbound)
    assert rows2  # version/generic prefixes stripped, so this is the same route


def test_match_path_prefers_the_more_specific_route():
    """OI-1 companion: equal-confidence ties resolve by matched significant segments.

    Both routes match `/v1/stock/dispatch` at `medium`; returning them together
    would draw an edge to a service that only exposes `/stock`.
    """
    inbound = {
        "/stock": [("acme/stock", "/stock", "GET", "A.java:1")],
        "/stock/dispatch": [("acme/dispatch", "/stock/dispatch", "POST", "B.java:2")],
    }
    rows, conf = gc.match_path_in_inbound_index("/v1/stock/dispatch", inbound)
    assert [r[0] for r in rows] == ["acme/dispatch"]
    assert conf == "medium"


def test_match_path_does_not_reach_a_child_route_of_the_query():
    """A caller of `/v1/stock` must not also edge to the service exposing `/stock/dispatch`.

    Both candidates match at `medium` (one is the same route, the other a child),
    so only the equality term of the specificity key separates them. The mirror of
    `test_match_path_prefers_the_more_specific_route`, which the distance term
    alone would satisfy.
    """
    inbound = {
        "/stock": [("acme/stock", "/stock", "GET", "A.java:1")],
        "/stock/dispatch": [("acme/dispatch", "/stock/dispatch", "POST", "B.java:2")],
    }
    rows, _conf = gc.match_path_in_inbound_index("/v1/stock", inbound)
    assert [r[0] for r in rows] == ["acme/stock"]


def test_match_path_returns_equally_specific_candidates_together():
    """Two services exposing the same route are genuinely ambiguous — keep both."""
    inbound = {
        "/stock": [
            ("acme/stock-a", "/stock", "GET", "A.java:1"),
            ("acme/stock-b", "/stock", "GET", "B.java:1"),
        ],
    }
    rows, _conf = gc.match_path_in_inbound_index("/v1/stock", inbound)
    assert sorted(r[0] for r in rows) == ["acme/stock-a", "acme/stock-b"]


def test_match_path_is_independent_of_index_insertion_order():
    """The same index built in a different order must give the same answer.

    The two routes below are *different index keys* that reduce to the same
    significant segments, so they tie on both confidence and specificity and are
    returned together. Which one comes first is then pure dict-iteration order
    unless the result is sorted — a single-winner case would not detect that.
    """
    a = ("acme/stock-v1", "/v1/stock", "GET", "A.java:1")
    b = ("acme/stock-v2", "/v2/stock", "GET", "B.java:2")
    forward = {"/v1/stock": [a], "/v2/stock": [b]}
    reverse = {"/v2/stock": [b], "/v1/stock": [a]}
    rows, _conf = gc.match_path_in_inbound_index("/v3/stock", forward)
    assert len(rows) == 2, "fixture must produce a genuine tie across two index keys"
    assert gc.match_path_in_inbound_index("/v3/stock", forward) == \
        gc.match_path_in_inbound_index("/v3/stock", reverse)


def test_store_key_from_node_jdbc():
    node = {"detail": {"vendor": "jdbc", "url": "jdbc:postgresql://db-host:5432/mydb"}}
    key = gc.store_key_from_node(node)
    assert key and key.startswith("jdbc:postgresql://")


def test_load_v2_repo_records(tmp_path):
    root = tmp_path / "metabase"
    d = root / "repos" / "g"
    d.mkdir(parents=True)
    (d / "r.json").write_text(json.dumps({"schema_version": 2, "group": "g", "name": "r", "nodes": []}), encoding="utf-8")
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    records = gc.load_v2_repo_records(root)
    assert [rec["name"] for rec in records] == ["r"]
