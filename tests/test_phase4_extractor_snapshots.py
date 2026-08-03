"""Phase 4: per-language extractor snapshots against committed JSON."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src2sink.extractors.config import extract_from_config
from src2sink.extractors.unified import extract_from_file
from snapshot_utils import load_snapshot, normalize_extraction, write_snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYN = FIXTURES / "synthetic-repos"
SNAPSHOTS = FIXTURES / "extractor-snapshots"
UPDATE = os.environ.get("UPDATE_METABASE_SNAPSHOTS", "").lower() in {"1", "true", "yes"}


def _assert_snapshot(name: str, actual: dict) -> None:
    path = SNAPSHOTS / f"{name}.json"
    if UPDATE:
        write_snapshot(path, actual)
        pytest.skip(f"Updated snapshot {name}")
    expected = load_snapshot(path)
    assert actual == expected, (
        f"Snapshot mismatch for {name}. "
        "Set UPDATE_METABASE_SNAPSHOTS=1 to refresh."
    )


@pytest.mark.parametrize(
    ("name", "repo_id", "rel_path", "language", "source_path"),
    [
        (
            "java-sql-runner-api",
            "acme/sql-runner-api",
            "src/QueryController.java",
            "java",
            SYN / "acme/sql-runner-api/src/QueryController.java",
        ),
        (
            "java-http-consumer",
            "acme/api-consumer",
            "src/RunnerClient.java",
            "java",
            SYN / "acme/api-consumer/src/RunnerClient.java",
        ),
        (
            "java-safe-crud",
            "negative/safe-crud",
            "src/ItemsController.java",
            "java",
            SYN / "negative/safe-crud/src/ItemsController.java",
        ),
        (
            "java-phone-publisher",
            "notifications/sms-gateway",
            "src/EventPublisher.java",
            "java",
            SYN / "notifications/sms-gateway/src/EventPublisher.java",
        ),
        (
            "java-phone-listener",
            "notifications/sms-consumer",
            "src/PhoneListener.java",
            "java",
            SYN / "notifications/sms-consumer/src/PhoneListener.java",
        ),
        (
            "python-pii-log",
            "python/sms-sender",
            "src/sender.py",
            "python",
            SYN / "python/sms-sender/src/sender.py",
        ),
        # OI-9: a repo that sends SQL to another service. Before the family
        # existed this file was an ordinary http-out and nothing more.
        (
            "java-query-forwarder",
            "fulfilment/query-forwarder",
            "src/StockQueryForwarder.java",
            "java",
            SYN / "fulfilment/query-forwarder/src/StockQueryForwarder.java",
        ),
        (
            "go-http-caller",
            "go/http-caller",
            "main.go",
            "go",
            SYN / "go/http-caller/main.go",
        ),
        # OI-7: an HTTP proxy with a `sql` field and SQL-verb calls on non-database
        # receivers. Snapshotted as a whole node list precisely because the
        # assertion is an *absence* — `sql` and `raw-code-payload` must not appear,
        # and a lookup-based test would not notice them coming back under a
        # different line or symbol.
        (
            "java-stock-proxy",
            "fulfilment/stock-proxy",
            "src/StockForwarder.java",
            "java",
            SYN / "fulfilment/stock-proxy/src/StockForwarder.java",
        ),
        # OI-8: SQL built by String.format and by concatenation containing an
        # embedded quote. Both produced no node at all before the rewrite.
        (
            "java-stock-dao",
            "fulfilment/stock-dao",
            "src/StockQueryBuilder.java",
            "java",
            SYN / "fulfilment/stock-dao/src/StockQueryBuilder.java",
        ),
    ],
)
def test_extractor_file_snapshot(
    name: str,
    repo_id: str,
    rel_path: str,
    language: str,
    source_path: Path,
) -> None:
    source = source_path.read_text(encoding="utf-8")
    nodes, edges = extract_from_file(
        repo_id=repo_id,
        rel_path=rel_path,
        language=language,
        source=source,
    )
    _assert_snapshot(name, normalize_extraction(nodes, edges))


def test_extractor_java_sql_inline_snapshot() -> None:
    source = """
@RestController
class SqlRunner {
  static class Req { String sql; }
  @PostMapping("/execute")
  void run(@RequestBody Req req) {
    jdbcTemplate.query(req.getSql());
  }
}
"""
    nodes, edges = extract_from_file(
        repo_id="test/sql",
        rel_path="src/SqlRunner.java",
        language="java",
        source=source.strip(),
    )
    _assert_snapshot("java-sql-runner", normalize_extraction(nodes, edges))


def test_extractor_config_yaml_snapshot() -> None:
    source = (SYN / "config/store-svc/application.yml").read_text(encoding="utf-8")
    nodes, edges = extract_from_config(
        repo_id="config/store-svc",
        rel_path="application.yml",
        source=source,
    )
    _assert_snapshot("yaml-config-store", normalize_extraction(nodes, edges))
