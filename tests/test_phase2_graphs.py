"""Phase 2 graph matching and trace tests."""

from __future__ import annotations

from src2sink.graph_common import (
    normalize_path_template,
    path_templates_match,
    store_key_from_node,
)
from src2sink.aggregators.service_calls import collect_service_edges
from src2sink.trace import run_trace


def test_path_template_matching() -> None:
    assert path_templates_match("/queries/{handle}", "/queries") == "medium"
    assert path_templates_match("/queries", "/queries") == "high"
    assert normalize_path_template("/queries/{id}") == "/queries/{}"


def test_service_edge_from_fixture() -> None:
    consumer = {
        "schema_version": 2,
        "group": "test",
        "name": "consumer",
        "nodes": [
            {
                "family": "http-out",
                "kind": "sink",
                "file": "Client.java",
                "line": 10,
                "detail": {"raw": 'rest.post("https://query-api-service.dev/v1/queries")'},
            },
        ],
        "edges": [],
    }
    provider = {
        "schema_version": 2,
        "group": "dataplatform",
        "name": "query-api-service",
        "nodes": [
            {
                "family": "http-in",
                "kind": "source",
                "file": "Controller.java",
                "line": 5,
                "detail": {"path": "/queries", "method": "POST"},
            },
        ],
        "edges": [],
    }
    edges, _broken = collect_service_edges([consumer, provider])
    assert any(
        e.source_repo == "test/consumer"
        and e.target_repo == "dataplatform/query-api-service"
        for e in edges
    )


def test_store_key_jdbc() -> None:
    node = {
        "detail": {
            "vendor": "jdbc",
            "url": "jdbc:postgresql://db.example.com:5432/analytics",
        },
    }
    key = store_key_from_node(node)
    assert key is not None
    assert "postgresql" in key


JAVA_HTTP_URL = '''
import java.net.URI;
import java.net.http.HttpRequest;
class C {
  void post() {
    HttpRequest.newBuilder(URI.create("https://query-api-service.dev/v1/queries")).POST(null).build();
  }
}
'''


def test_http_out_url_enrichment() -> None:
    from src2sink.extractors.unified import extract_from_file

    nodes, _ = extract_from_file(
        repo_id="test/client",
        rel_path="src/C.java",
        language="java",
        source=JAVA_HTTP_URL,
    )
    out = [n for n in nodes if n.family == "http-out"]
    assert out
    assert any(n.detail.get("url") or n.detail.get("path") for n in out)


def test_payload_producer_index_dep(tmp_path) -> None:
    from src2sink.aggregators.payload_producers import build_producer_indices
    from src2sink.known_api_clients import ApiClientBinding, configure_api_client_bindings

    configure_api_client_bindings((
        ApiClientBinding(
            target_repo="dataplatform/query-api-service",
            maven_artifact="query-api-service-client",
            import_prefix="com.example.dataplatform.queryapi.client",
            paths=("/queries", "/queries/sync", "/queries/{handle}", "/results/{handle}"),
            payload_fields=("sql",),
            service_aliases=("query-api-service",),
        ),
    ))

    metabase = tmp_path / "metabase"
    repo_dir = metabase / "repos" / "dataplatform"
    repo_dir.mkdir(parents=True)
    consumer = {
        "schema_version": 2,
        "group": "dataplatform",
        "name": "dp-ato-detections",
        "dependencies_internal": [
            {
                "groupId": "com.example.dataplatform",
                "artifactId": "query-api-service-client",
                "version": "3.6.0",
                "kind": "internal",
            },
        ],
        "nodes": [],
        "edges": [],
    }
    (repo_dir / "dp-ato-detections.json").write_text(
        __import__("json").dumps(consumer),
        encoding="utf-8",
    )
    (repo_dir / "query-api-service.json").write_text(
        __import__("json").dumps({
            "schema_version": 2,
            "group": "dataplatform",
            "name": "query-api-service",
            "nodes": [],
            "edges": [],
        }),
        encoding="utf-8",
    )
    indices = build_producer_indices(metabase)
    qas = next(i for i in indices if i.binding.target_repo == "dataplatform/query-api-service")
    assert any(h.source_repo == "dataplatform/dp-ato-detections" for h in qas.hits)


def test_trace_target_inbound(tmp_path) -> None:
    metabase = tmp_path / "metabase"
    repo_dir = metabase / "repos" / "dataplatform"
    repo_dir.mkdir(parents=True)
    data = {
        "schema_version": 2,
        "group": "dataplatform",
        "name": "query-api-service",
        "nodes": [
            {
                "family": "http-in",
                "kind": "source",
                "file": "C.java",
                "line": 1,
                "detail": {"path": "/queries", "method": "POST"},
            },
            {
                "family": "raw-code-payload",
                "kind": "source",
                "file": "C.java",
                "line": 2,
                "detail": {
                    "endpoint_path": "/queries",
                    "field_line": 2,
                    "sink_symbol": "executeQuery",
                },
            },
        ],
        "edges": [],
    }
    (repo_dir / "query-api-service.json").write_text(
        __import__("json").dumps(data),
        encoding="utf-8",
    )
    report = run_trace(metabase, "dataplatform/query-api-service", path_filter="/queries")
    assert report.inbound
    assert report.raw_payloads
