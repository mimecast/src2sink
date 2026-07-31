"""Unit tests for the graph_common matching/normalisation helpers."""

from __future__ import annotations

import json

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
    assert gc.path_templates_match("/api/queries", "/api") == "medium"
    assert gc.path_templates_match("/api/v1/queries", "/queries") == "low"
    assert gc.path_templates_match("/a", "/b") is None
    assert gc.path_templates_match("", "/x") is None


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
    assert rows2  # low-confidence segment overlap still matches


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
