"""Tests for trace rendering + upstream resolution (covers worker/CLI-only paths)."""

from __future__ import annotations

from src2sink.sanitize import UNTRUSTED_CONTENT_NOTICE
from src2sink.trace import TraceReport, UpstreamHit, render_trace_markdown, run_trace

TARGET = {
    "group": "dp",
    "name": "query-api-service",
    "nodes": [
        {"family": "http-in", "kind": "source", "file": "C.java", "line": 1,
         "framework": "spring", "detail": {"path": "/queries", "method": "POST"}},
        {"family": "sql", "kind": "sink", "file": "S.java", "line": 9,
         "detail": {"execution": True, "symbol": "execute", "raw": "SELECT 1"}},
        {"family": "raw-code-payload", "kind": "sink", "file": "C.java", "line": 2,
         "detail": {"endpoint_path": "/queries", "sink_symbol": "execute", "field_line": 3}},
        {"family": "data-store", "kind": "store", "file": "app.yml", "line": 4,
         "detail": {"vendor": "jdbc", "url": "jdbc:postgresql://h:5432/db"}},
    ],
}
CONSUMER = {
    "group": "apps",
    "name": "consumer",
    "nodes": [
        {"family": "http-out", "kind": "sink", "file": "Cl.java", "line": 5,
         "detail": {"raw": 'client.post("http://query-api-service/queries", body)'}},
    ],
}


def test_render_trace_markdown_all_sections():
    report = TraceReport(
        target_repo="dp/query-api-service",
        path_filter="/queries",
        inbound=[{"path": "/queries", "method": "POST", "file": "C.java", "line": 1}],
        raw_payloads=[{"endpoint": "/queries", "field_line": 3, "sink_symbol": "x", "file": "C.java"}],
        sql_sinks=[{"symbol": "execute", "file": "S.java", "line": 9, "raw": "SELECT 1"}],
        stores=[{"store_key": "jdbc:postgresql://h/db", "file": "app.yml", "line": 4}],
        upstream=[UpstreamHit("apps/consumer", "http-out-raw", "medium", "evidence", "Cl.java:5")],
    )
    md = render_trace_markdown(report)
    assert UNTRUSTED_CONTENT_NOTICE.strip() in md
    for section in ("Inbound endpoints", "Raw SQL", "SQL execution sinks",
                    "Config data stores", "Upstream callers"):
        assert section in md
    assert "apps/consumer" in md


def test_render_trace_markdown_empty_sections():
    md = render_trace_markdown(TraceReport(target_repo="g/r", path_filter=None))
    assert "_None matched._" in md or "_None._" in md


def test_run_trace_collects_facts_and_upstream(tmp_path):
    report = run_trace(
        tmp_path,
        "dp/query-api-service",
        records=[TARGET, CONSUMER],
        producer_indices=[],
    )
    assert report.target_repo == "dp/query-api-service"
    assert any(h["path"] == "/queries" for h in report.inbound)
    assert report.sql_sinks and report.stores
    # The consumer's http-out raw references the target path → upstream hit.
    assert any(u.source_repo == "apps/consumer" for u in report.upstream)


def test_run_trace_scan_repos_source_literal(tmp_path):
    # Target must exist as a directory; a sibling consumer references it by name.
    (tmp_path / "dp" / "query-api-service").mkdir(parents=True)
    consumer_src = tmp_path / "apps" / "consumer"
    consumer_src.mkdir(parents=True)
    (consumer_src / "Client.java").write_text(
        'String url = "http://query-api-service/queries";', encoding="utf-8"
    )
    report = run_trace(
        tmp_path,  # metabase_root (unused since records passed)
        "dp/query-api-service",
        records=[TARGET],
        producer_indices=[],
        scan_repos=True,
        repos_root=tmp_path,
    )
    assert any(u.kind == "source-literal" and u.source_repo == "apps/consumer"
               for u in report.upstream)


def test_source_literal_evidence_is_pii_redacted(tmp_path):
    """SAST finding 2: on-disk source-literal evidence must be PII-redacted.

    The matched quoted string is read straight off an untrusted file and never
    passes through the build-time redaction, so redact_literals must run here.
    """
    (tmp_path / "dp" / "query-api-service").mkdir(parents=True)
    consumer_src = tmp_path / "apps" / "consumer"
    consumer_src.mkdir(parents=True)
    # A quoted literal that references the target AND embeds a sample email + SSN.
    (consumer_src / "Client.java").write_text(
        'String u = "query-api-service admin@example.com ssn 123-45-6789";',
        encoding="utf-8",
    )
    report = run_trace(
        tmp_path, "dp/query-api-service", records=[TARGET], producer_indices=[],
        scan_repos=True, repos_root=tmp_path,
    )
    lit = next(u for u in report.upstream if u.kind == "source-literal")
    assert "admin@example.com" not in lit.evidence
    assert "123-45-6789" not in lit.evidence
    assert "<redacted-email>" in lit.evidence and "<redacted-number>" in lit.evidence
