"""OI-13: Kotlin must find what Java finds.

The AST pass named Kotlin in `CALL_NODE_TYPES` and then routed it to the Java
walker, which requires a `method_invocation` node — a Java grammar type Kotlin
never produces. The two halves of the dispatch disagreed and neither said so, so
every Kotlin call site was invisible and Kotlin SQL sinks were found only when a
regex tier happened to match.

This is a prerequisite for `OI-17`, and for the same reason `OI-21` was:
reachability that silently covers one JVM language while missing the other
produces confident, incomplete answers. The absence looks like a clean result.

The measured symptom, from the issue:

    java    -> ['data-class-field/source', 'sql/sink']
    kotlin  -> ['data-class-field/source']
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file

_JAVA = """
class StockDao {
    private final JdbcTemplate jdbcTemplate;
    List<Stock> find(long id) {
        return jdbcTemplate.query("SELECT ref FROM stock WHERE id = ?", mapper, id);
    }
}
"""

_KOTLIN = """
class StockDao(private val jdbcTemplate: JdbcTemplate) {
    fun find(id: Long): List<Stock> {
        return jdbcTemplate.query("SELECT ref FROM stock WHERE id = ?", mapper, id)
    }
}
"""


def _families(source: str, language: str, rel_path: str):
    return sorted(
        f"{n.family}/{n.kind}"
        for n in extract_from_file(
            repo_id="g/r", rel_path=rel_path, language=language, source=source
        )[0]
    )


def test_kotlin_finds_what_java_finds():
    """The same query, in the two languages the JVM fleet is written in."""
    java = _families(_JAVA, "java", "src/StockDao.java")
    kotlin = _families(_KOTLIN, "kotlin", "src/StockDao.kt")
    assert "sql/sink" in kotlin, f"kotlin yielded {kotlin}"
    assert java == kotlin


def test_a_kotlin_sql_sink_carries_its_receiver():
    """Without the receiver, `OI-26`'s classification has only file evidence left."""
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/StockDao.kt", language="kotlin", source=_KOTLIN
    )[0]
    sinks = [n for n in nodes if n.family == "sql" and n.kind == "sink"]
    assert [n.detail["receiver"] for n in sinks] == ["jdbcTemplate"]


def test_a_kotlin_http_client_is_not_a_sql_sink():
    """The OI-26 guard must work in Kotlin too, or parity imports the old defect."""
    source = """
    class Proxy(private val httpClient: HttpClient) {
        val auditSql = "SELECT ref FROM stock"
        fun forward(body: String): String = httpClient.execute(body)
    }
    """
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/Proxy.kt", language="kotlin", source=source
    )[0]
    assert [n.family for n in nodes if n.family == "sql" and n.kind == "sink"] == []


def test_a_kotlin_endpoint_reaching_a_sink_is_linked():
    """`raw-code-payload` must fire in Kotlin, as it does in Java."""
    source = """
    @RestController
    class QueryApi(private val jdbcTemplate: JdbcTemplate) {
        @PostMapping("/run")
        fun run(@RequestBody sql: String): String =
            jdbcTemplate.query("SELECT ref FROM stock WHERE x = " + sql, mapper)
    }
    """
    families = {
        n.family for n in extract_from_file(
            repo_id="g/r", rel_path="src/QueryApi.kt", language="kotlin", source=source
        )[0]
    }
    assert "raw-code-payload" in families


@pytest.mark.parametrize("language,ext", [("java", "java"), ("kotlin", "kt")])
def test_script_exec_parity(language, ext):
    """The other AST-derived family must reach Kotlin too, not just SQL."""
    source = (
        'class A { void m() { eval("x"); } }' if language == "java"
        else 'class A { fun m() { eval("x") } }'
    )
    families = {
        n.family for n in extract_from_file(
            repo_id="g/r", rel_path=f"src/A.{ext}", language=language, source=source
        )[0]
    }
    assert "script-exec" in families
