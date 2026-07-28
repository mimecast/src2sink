"""Phase 1 extractor unit tests (inline fixtures)."""

from __future__ import annotations

from src2sink.extractors.unified import extract_from_file

JAVA_SQL = """
@RestController
class SqlRunner {
  static class Req { String sql; }
  @PostMapping("/execute")
  void run(@RequestBody Req req) {
    jdbcTemplate.query(req.getSql());
  }
}
"""

PYTHON_PII_LOG = """
import logging
logger = logging.getLogger(__name__)
def send(phoneNumber: str):
    logger.info("SMS to %s", phoneNumber)
"""

JAVA_FILE_SINK = """
import java.nio.file.Files;
void w(String p) { Files.writeString(Path.of(p), "x"); }
"""


def test_java_sql_sink_and_raw_payload() -> None:
    nodes, edges = extract_from_file(
        repo_id="test/sql",
        rel_path="src/SqlRunner.java",
        language="java",
        source=JAVA_SQL,
    )
    families = {n.family for n in nodes}
    assert "sql" in families
    assert "raw-code-payload" in families
  # at least one execution sink
    sinks = [n for n in nodes if n.family == "sql" and n.kind == "sink"]
    assert sinks
    assert edges or any(n.family == "raw-code-payload" for n in nodes)


def test_python_pii_log_sink() -> None:
    nodes, _ = extract_from_file(
        repo_id="test/sms",
        rel_path="src/sender.py",
        language="python",
        source=PYTHON_PII_LOG,
    )
    log_sinks = [n for n in nodes if n.family == "pii-log"]
    assert log_sinks
    assert log_sinks[0].pii_classification == "direct-pii"


def test_java_file_sink() -> None:
    nodes, _ = extract_from_file(
        repo_id="test/files",
        rel_path="src/Writer.java",
        language="java",
        source=JAVA_FILE_SINK,
    )
    assert any(n.family == "file" and n.kind == "sink" for n in nodes)


JAVA_FALSE_POSITIVE = """
@RestController
class Items {
  @GetMapping("/items")
  List<Item> list() { return repo.findAll(); }
}
"""


def test_no_raw_payload_on_orm_find_only() -> None:
    nodes, _ = extract_from_file(
        repo_id="test/items",
        rel_path="src/Items.java",
        language="java",
        source=JAVA_FALSE_POSITIVE,
    )
    assert "raw-code-payload" not in {n.family for n in nodes}


def test_test_path_skipped() -> None:
    nodes, edges = extract_from_file(
        repo_id="test/sql",
        rel_path="src/test/SqlRunner.java",
        language="java",
        source=JAVA_SQL,
    )
    assert nodes == []
    assert edges == []
