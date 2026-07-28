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
            "java-query-api",
            "dataplatform/query-api-service",
            "src/QueryController.java",
            "java",
            SYN / "dataplatform/query-api-service/src/QueryController.java",
        ),
        (
            "java-http-consumer",
            "dataplatform/api-consumer",
            "src/QueryClient.java",
            "java",
            SYN / "dataplatform/api-consumer/src/QueryClient.java",
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
        (
            "go-http-caller",
            "go/http-caller",
            "main.go",
            "go",
            SYN / "go/http-caller/main.go",
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
