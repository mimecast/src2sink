"""Regression tests for OI-9 — an outbound request carrying SQL has no home family.

`raw-code-payload` is structurally *inbound*: `link_raw_code_payload_endpoints`
requires `ctx.http_sources`, an `http-in` node, so it only ever fires on the
service that **receives** SQL. The dual — a repo that **sends** arbitrary SQL to
another service over the wire — had no representation at all. Such a call is
neither a local `sql` sink (nothing executes here) nor an ordinary `http-out`
(the payload is executable code at the far end), so it fell between the two and
appeared as a plain HTTP call.

The two families are the ends of one cross-repo hop:

    raw-code-payload   this service accepts SQL
    sql-payload-out    this service sends SQL

Joining them across repos is the obvious follow-on and is deliberately out of
scope here.

Fixture names follow the sanitised placeholder set used across the suite.
"""

from __future__ import annotations

import pytest

from src2sink import known_api_clients as kac
from src2sink.extractors.unified import extract_from_file
from src2sink.known_api_clients import ApiClientBinding


def _nodes(source: str, *, language: str = "java", rel_path: str = "src/Sample.java"):
    """Run the full per-file extraction pipeline over one source string."""
    nodes, _edges = extract_from_file(
        repo_id="fulfilment/commons", rel_path=rel_path, language=language, source=source,
    )
    return nodes


def _payload_out(source: str, **kw):
    """Return the `sql-payload-out` nodes for one source string."""
    return [n for n in _nodes(source, **kw) if n.family == "sql-payload-out"]


# --------------------------------------------------------------------------
# The shapes a SQL payload is bound in
# --------------------------------------------------------------------------

JAVA_SETTER = """
public class StockQueryForwarder {
    private static final String SUBMIT_URL = "/v1/query";

    public Result submit(String sqlText) {
        QueryRequest body = new QueryRequest();
        body.setSql(sqlText);
        return restTemplate.postForObject(SUBMIT_URL, body, Result.class);
    }
}
"""

JAVA_BUILDER = """
public class StockQueryForwarder {
    private static final String SUBMIT_URL = "/v1/query";

    public Result submit(String sqlText) {
        QueryRequest body = QueryRequest.builder().sql(sqlText).build();
        return restTemplate.postForObject(SUBMIT_URL, body, Result.class);
    }
}
"""

PYTHON_JSON_KEY = """
import requests

SUBMIT_URL = "/v1/query"


def submit(session: requests.Session, sql_text):
    return session.post(SUBMIT_URL, json={"sql": sql_text})
"""


@pytest.mark.parametrize(
    ("name", "source", "language", "rel_path"),
    [
        ("java-setter", JAVA_SETTER, "java", "src/StockQueryForwarder.java"),
        ("java-builder", JAVA_BUILDER, "java", "src/StockQueryForwarder.java"),
        ("python-json-key", PYTHON_JSON_KEY, "python", "forwarder.py"),
    ],
)
def test_sql_bound_into_an_outbound_request_is_a_payload_out(
    name: str, source: str, language: str, rel_path: str,
) -> None:
    """OI-9: a SQL field bound into an outbound request is a sink in its own right.

    The field-name passes recognised *declarations* only, so `body.setSql(...)`
    contributed nothing — the setter, builder and JSON-key forms are how a
    payload is actually populated at a call site.
    """
    nodes = _payload_out(source, language=language, rel_path=rel_path)
    assert nodes, f"{name} produced no sql-payload-out node"
    assert nodes[0].detail["field_name"] == "sql"
    assert nodes[0].data_class == "raw-sql-payload"


def test_payload_out_records_the_outbound_call_it_belongs_to() -> None:
    """The node must point at the request it rides on, not just the field."""
    node = _payload_out(JAVA_SETTER, rel_path="src/StockQueryForwarder.java")[0]
    assert node.detail["path"] == "/v1/query"
    assert isinstance(node.detail["http_out_line"], int)


# --------------------------------------------------------------------------
# Precision — a new sink family needs its negatives
# --------------------------------------------------------------------------

JAVA_ORDINARY_POST = """
public class StockNotifier {
    private static final String NOTIFY_URL = "/v1/notify";

    public void notifyDispatch(String reference) {
        NotifyRequest body = new NotifyRequest();
        body.setReference(reference);
        restTemplate.postForObject(NOTIFY_URL, body, Void.class);
    }
}
"""

JAVA_SQL_FIELD_NO_CALL = """
public class QueryRequest {
    private String sql;

    public void setSql(String sql) {
        this.sql = sql;
    }
}
"""


def test_an_ordinary_post_yields_no_payload_out() -> None:
    """A request with no SQL-ish field is an ordinary outbound call."""
    assert _payload_out(JAVA_ORDINARY_POST, rel_path="src/StockNotifier.java") == []


def test_a_sql_field_without_an_outbound_call_yields_no_payload_out() -> None:
    """A data class declaring a `sql` field sends nothing; it is not a sink.

    Without this the family would fire on every DTO in the fleet — the same
    mistake that let a field named `sql` fabricate a raw-code-payload finding.
    """
    assert _payload_out(JAVA_SQL_FIELD_NO_CALL, rel_path="src/QueryRequest.java") == []


# --------------------------------------------------------------------------
# Binding payload_fields raise confidence and extend the vocabulary
# --------------------------------------------------------------------------

BINDING = ApiClientBinding(
    target_repo="commerce/warehouse-service",
    maven_artifact="warehouse-service-client",
    import_prefix="com.example.commerce.warehouse.client",
    paths=("/v1/query",),
    payload_fields=("dql",),
    service_aliases=("warehouse-service",),
    class_patterns=(),
)

JAVA_BINDING_FIELD = """
public class StockQueryForwarder {
    private static final String SUBMIT_URL = "/v1/query";

    public Result submit(String expression) {
        QueryRequest body = new QueryRequest();
        body.setDql(expression);
        return restTemplate.postForObject(SUBMIT_URL, body, Result.class);
    }
}
"""


@pytest.fixture
def bindings():
    """Configure one api-client binding for the duration of a test."""
    kac.configure_api_client_bindings((BINDING,))
    yield
    kac.configure_api_client_bindings(())


def test_binding_payload_fields_extend_the_vocabulary(bindings) -> None:
    """`dql` is not in the strict vocabulary; the binding says it matters here.

    A binding declaring `payload_fields` is a statement that this service takes
    that field as executable input, so it is stronger evidence than the generic
    vocabulary and rates `high`.
    """
    nodes = _payload_out(JAVA_BINDING_FIELD, rel_path="src/StockQueryForwarder.java")
    assert nodes, "binding-declared payload field not recognised"
    assert nodes[0].detail["field_name"] == "dql"
    assert nodes[0].confidence == "high"


def test_vocabulary_only_match_is_medium_confidence() -> None:
    """Without a binding to vouch for it, a vocabulary hit is a guess worth less."""
    node = _payload_out(JAVA_SETTER, rel_path="src/StockQueryForwarder.java")[0]
    assert node.confidence == "medium"


# --------------------------------------------------------------------------
# The family must reach the aggregated output, not just ctx.nodes
# --------------------------------------------------------------------------

def test_payload_out_reaches_the_taint_catalogue(tmp_path) -> None:
    """A new family is not done when the extractor emits it.

    It has to be routed by taint_buckets and written by taint_writers, or it
    exists in the per-repo JSON and nowhere a reviewer looks. This is the test
    that catches half-finished plumbing.
    """
    import json

    from src2sink.aggregators.taint_catalogs import aggregate_taint_catalogs_v2

    repo_json = tmp_path / "repos" / "fulfilment" / "commons.json"
    repo_json.parent.mkdir(parents=True)
    nodes = _payload_out(JAVA_SETTER, rel_path="src/StockQueryForwarder.java")
    repo_json.write_text(json.dumps({
        "schema_version": 2,
        "group": "fulfilment",
        "name": "commons",
        "nodes": [{
            "family": n.family, "kind": n.kind, "file": n.file, "line": n.line,
            "detail": n.detail, "confidence": n.confidence, "data_class": n.data_class,
        } for n in nodes],
        "edges": [],
    }), encoding="utf-8")

    aggregate_taint_catalogs_v2(tmp_path, [repo_json])
    written = (tmp_path / "taint" / "sql-payload-out.jsonl")
    assert written.is_file(), sorted(p.name for p in (tmp_path / "taint").iterdir())
    assert "sql" in written.read_text(encoding="utf-8")


def test_payload_out_is_counted_in_the_repo_index() -> None:
    """The family needs a count in the index, or a fleet scan cannot see it at all."""
    from src2sink.aggregators.index_v2 import _row_from_record

    row = _row_from_record({
        "group": "fulfilment",
        "name": "commons",
        "nodes": [
            {"family": "sql-payload-out", "kind": "sink", "detail": {"field_name": "sql"}},
            {"family": "http-out", "kind": "sink", "detail": {}},
        ],
        "edges": [],
    })
    assert row.get("sql_payload_out") == 1
