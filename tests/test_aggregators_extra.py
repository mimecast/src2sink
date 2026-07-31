"""Coverage for payload_producers and CLI helper modules."""

from __future__ import annotations

import json
import sys

from src2sink import known_api_clients as kac
from src2sink.aggregators.payload_producers import build_producer_indices, write_payload_producer_index
from src2sink.known_api_clients import ApiClientBinding


def _binding():
    return ApiClientBinding(
        target_repo="acme/sql-runner-api",
        maven_artifact="sql-runner-api-client",
        import_prefix="com.example.acme.sqlrunner.client",
        paths=("/queries",),
        payload_fields=("sql",),
        service_aliases=("sql-runner-api",),
        class_patterns=("SqlRunnerApiClient",),
    )


def _write_records(root, records):
    jsons = []
    for rec in records:
        d = root / "repos" / rec["group"]
        d.mkdir(parents=True, exist_ok=True)
        jp = d / f"{rec['name']}.json"
        jp.write_text(json.dumps(rec), encoding="utf-8")
        jsons.append(jp)
    return jsons


def test_build_and_write_producer_index(tmp_path):
    kac.configure_api_client_bindings((_binding(),))
    try:
        consumer = {
            "group": "apps", "name": "consumer",
            "nodes": [
                {"family": "api-client-consumer", "kind": "propagator",
                 "file": "C.java", "line": 3, "confidence": "high",
                 "detail": {"client": "sql-runner-api-client", "target_repo": "acme/sql-runner-api",
                            "import": "import com.example.acme.sqlrunner.client.SqlRunnerApiClient",
                            "paths": ["/queries"]}},
            ],
        }
        jsons = _write_records(tmp_path, [consumer])
        indices = build_producer_indices(tmp_path, json_paths=jsons)
        assert any(idx.binding.target_repo == "acme/sql-runner-api" for idx in indices)

        write_payload_producer_index(tmp_path, jsons)
        out = tmp_path / "graphs" / "payload-endpoint-producers.jsonl"
        assert out.is_file()
    finally:
        kac.configure_api_client_bindings(())


def test_build_producer_indices_scans_repos(tmp_path):
    kac.configure_api_client_bindings((_binding(),))
    try:
        # A consumer repo that imports the client and declares the pom dependency.
        consumer = tmp_path / "apps" / "consumer"
        consumer.mkdir(parents=True)
        (consumer / "Client.java").write_text(
            "import com.example.acme.sqlrunner.client.SqlRunnerApiClient;\n"
            "class Client { SqlRunnerApiClient c; }\n",
            encoding="utf-8",
        )
        (consumer / "pom.xml").write_text(
            "<project><dependencies><dependency>"
            "<artifactId>sql-runner-api-client</artifactId></dependency></dependencies></project>",
            encoding="utf-8",
        )
        indices = build_producer_indices(tmp_path, repos_root=tmp_path, json_paths=[])
        hits = [h for idx in indices for h in idx.hits]
        assert any(h.source_repo == "apps/consumer" for h in hits)
        assert any(h.kind in ("import-scan", "build-dep-scan") for h in hits)
    finally:
        kac.configure_api_client_bindings(())


def test_library_taint_java(tmp_path):
    from src2sink.library_taint_java import render_taint_table, scan_java_public_api

    src = tmp_path / "src" / "main" / "java"
    src.mkdir(parents=True)
    (src / "Api.java").write_text(
        "public class Api {\n"
        "  public String runQuery(String sql) { return db.exec(sql); }\n"
        "  public void save(Object o) {}\n"
        "}\n",
        encoding="utf-8",
    )
    rows = scan_java_public_api(tmp_path)
    table = render_taint_table(rows)
    assert isinstance(rows, list)
    assert isinstance(table, str) and "|" in table


def test_record_fleet_baseline(tmp_path):
    from src2sink.record_fleet_baseline import count_fleet_families

    d = tmp_path / "repos" / "g"
    d.mkdir(parents=True)
    (d / "r.json").write_text(json.dumps({
        "schema_version": 2, "group": "g", "name": "r",
        "nodes": [{"family": "sql"}, {"family": "sql"}, {"family": "http-in"}],
    }), encoding="utf-8")
    repo_count, families = count_fleet_families(tmp_path)
    assert repo_count == 1
    assert families.get("sql") == 2


def test_record_fleet_baseline_main(tmp_path, monkeypatch, capsys):
    from src2sink.record_fleet_baseline import main

    d = tmp_path / "repos" / "g"
    d.mkdir(parents=True)
    (d / "r.json").write_text(json.dumps({
        "schema_version": 2, "group": "g", "name": "r", "nodes": [{"family": "sql"}],
    }), encoding="utf-8")
    out = tmp_path / "baseline.json"
    monkeypatch.setattr(
        sys, "argv",
        ["src2sink-baseline", "--metabase-root", str(tmp_path), "--output", str(out)],
    )
    assert main() == 0
    assert out.is_file()
