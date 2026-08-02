"""Regression tests for OI-7 — the `sql` family must not match on method name alone.

`execute`, `query` and `update` are ordinary method names. Matching them without
looking at the receiver catalogued `httpClient.execute(request)` and
`messageDigest.update(data)` as unparameterised SQL execution sinks at `high`
confidence, and — because an execution sink is one of the three inputs to
`link_raw_code_payload_endpoints` — let a plain HTTP proxy with a field named
`sql` manufacture a `raw-code-payload` node. A fabricated injection endpoint sends
an analyst to audit code that was never vulnerable, so this is a correctness bug
with a security cost, not noise.

A `sql` node now requires positive evidence: a database-ish receiver, an explicit
library hint in the call text, or file-level SQL evidence (a SQL keyword inside a
string literal, or a database import). The last of these is deliberately *not*
satisfied by a field merely named `sql` — see
`test_sql_field_name_alone_is_not_sql_evidence`, which is the case the whole fix
exists to eliminate.

Fixture names follow the sanitised placeholder set used across the suite: no real
repo, service or class name appears.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file


def _nodes(source: str, *, language: str = "java", rel_path: str = "src/Sample.java"):
    """Run the full per-file extraction pipeline over one source string."""
    nodes, _edges = extract_from_file(
        repo_id="test/sample", rel_path=rel_path, language=language, source=source,
    )
    return nodes


def _sql_sinks(nodes):
    """Return only the `sql` sink nodes from an extraction result."""
    return [n for n in nodes if n.family == "sql" and n.kind == "sink"]


# --------------------------------------------------------------------------
# Precision — calls that merely share a verb with SQL execution
# --------------------------------------------------------------------------

JAVA_HTTP_EXECUTE = """
public class StockSender {
    public void send(HttpUriRequest request) throws Exception {
        httpClient.execute(request);
    }
}
"""

JAVA_DIGEST_UPDATE = """
public class Hasher {
    void hash(byte[] data) {
        messageDigest.update(data);
    }
}
"""

JAVA_OKHTTP_CALL = """
public class Caller {
    void go(Call call) throws Exception {
        Response response = call.execute();
    }
}
"""


@pytest.mark.parametrize(
    "name,source",
    [
        ("http-client-execute", JAVA_HTTP_EXECUTE),
        ("message-digest-update", JAVA_DIGEST_UPDATE),
        ("okhttp-call-execute", JAVA_OKHTTP_CALL),
    ],
)
def test_non_sql_receiver_is_not_a_sql_sink(name: str, source: str) -> None:
    """OI-7: a SQL verb on a non-database receiver, with no SQL in the file, is not SQL."""
    assert _sql_sinks(_nodes(source)) == [], f"{name} produced a sql sink"


# --------------------------------------------------------------------------
# Recall — real SQL execution must survive the gate
# --------------------------------------------------------------------------

JAVA_JDBC_TEMPLATE = """
public class StockDao {
    private static final String FIND = "SELECT ref FROM stock WHERE id = ?";
    List<Stock> find(long id) {
        return jdbcTemplate.query(FIND, mapper, id);
    }
}
"""

JAVA_FIELD_ACCESS_RECEIVER = """
public class StockDao {
    List<Stock> find(long id) {
        return this.stockRepository.query(id);
    }
}
"""

PYTHON_CURSOR_EXECUTE = """
def load(ref):
    cursor.execute("SELECT ref FROM stock WHERE ref = %s", (ref,))
"""

GO_DB_QUERY = """
package store

func Load(ref string) {
	db.Query("SELECT ref FROM stock WHERE ref = $1", ref)
}
"""


def test_jdbc_template_query_is_still_a_sql_sink() -> None:
    """The receiver vocabulary must keep real JDBC execution — the recall guard."""
    sinks = _sql_sinks(_nodes(JAVA_JDBC_TEMPLATE))
    assert len(sinks) == 1
    assert sinks[0].detail["execution"] is True
    assert sinks[0].confidence == "high"


def test_qualified_receiver_is_matched_on_its_trailing_identifier() -> None:
    """`this.stockRepository.query(...)` is a database receiver despite the qualifier."""
    assert _sql_sinks(_nodes(JAVA_FIELD_ACCESS_RECEIVER))


def test_python_cursor_execute_is_a_sql_sink() -> None:
    """The gate must not be Java-only: Python receivers resolve too."""
    assert _sql_sinks(_nodes(PYTHON_CURSOR_EXECUTE, language="python", rel_path="store.py"))


def test_go_db_query_is_a_sql_sink() -> None:
    """The gate must not be Java-only: Go selector receivers resolve too."""
    assert _sql_sinks(_nodes(GO_DB_QUERY, language="go", rel_path="store.go"))


# --------------------------------------------------------------------------
# File-level SQL evidence (signal c)
# --------------------------------------------------------------------------

JAVA_BARE_EXECUTE_WITH_SQL_LITERAL = """
public class StockJob {
    private static final String STATEMENT = "DELETE FROM stock WHERE expired = true";
    void run(Runner runner) {
        runner.execute(STATEMENT);
    }
}
"""

JAVA_BARE_EXECUTE_WITH_JDBC_IMPORT = """
import java.sql.PreparedStatement;

public class StockJob {
    void run(Runner runner, PreparedStatement statement) {
        runner.execute(statement);
    }
}
"""


def test_sql_literal_in_file_admits_an_unknown_receiver() -> None:
    """A SQL keyword in a string literal is file-level evidence (signal c)."""
    assert _sql_sinks(_nodes(JAVA_BARE_EXECUTE_WITH_SQL_LITERAL))


def test_database_import_in_file_admits_an_unknown_receiver() -> None:
    """A JDBC import is file-level evidence (signal c)."""
    assert _sql_sinks(_nodes(JAVA_BARE_EXECUTE_WITH_JDBC_IMPORT))


# --------------------------------------------------------------------------
# The defect itself: a field named `sql` is not evidence of SQL
# --------------------------------------------------------------------------

JAVA_HTTP_PROXY_WITH_SQL_FIELD = """
@RestController
public class StockForwarder {
    private String sql;

    @PostMapping("/v1/forward")
    public Response forward(@RequestBody StockRequest req) throws Exception {
        return httpClient.execute(req.toHttpRequest());
    }
}
"""


def test_sql_field_name_alone_is_not_sql_evidence() -> None:
    """OI-7 in full: a proxy with a `sql` field must fabricate no injection finding.

    This is the case the whole work item exists to eliminate. Signal (c) is
    deliberately "SQL keyword in a literal, or a DB import" and never the bare
    token `sql`; a looser reading re-admits exactly this file.
    """
    nodes = _nodes(JAVA_HTTP_PROXY_WITH_SQL_FIELD, rel_path="src/StockForwarder.java")
    families = {n.family for n in nodes}
    assert "sql" not in families
    assert "raw-code-payload" not in families


# --------------------------------------------------------------------------
# `parameterised` must not claim knowledge it does not have
# --------------------------------------------------------------------------

def test_parameterised_is_unknown_without_a_sql_literal() -> None:
    """Without SQL text in scope there is nothing to call parameterised or not.

    The 1.1.0 test was `"?" in call_text or ":" in call_text`, which reported a
    call containing no SQL at all as *unparameterised* — half of what made the
    OI-7 false positives read like findings.
    """
    sinks = _sql_sinks(_nodes(JAVA_BARE_EXECUTE_WITH_JDBC_IMPORT))
    assert sinks
    assert sinks[0].detail["parameterised"] == "unknown"


def test_parameterised_is_true_for_a_placeholder_query() -> None:
    """A `?` placeholder in a SQL literal in scope is a genuine parameterised call."""
    sinks = _sql_sinks(_nodes(JAVA_JDBC_TEMPLATE))
    assert sinks[0].detail["parameterised"] is True
