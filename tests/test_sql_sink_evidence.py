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


JAVA_JDBC_TEMPLATE_NO_FILE_EVIDENCE = """
public class StockDao {
    List<Stock> findDynamic(String clause) {
        return jdbcTemplate.query(clause, mapper);
    }
}
"""


def test_jdbc_template_query_is_still_a_sql_sink() -> None:
    """The receiver vocabulary must keep real JDBC execution — the recall guard."""
    sinks = _sql_sinks(_nodes(JAVA_JDBC_TEMPLATE))
    assert len(sinks) == 1
    assert sinks[0].detail["execution"] is True
    assert sinks[0].confidence == "high"


def test_receiver_alone_admits_a_call_with_no_other_evidence() -> None:
    """A database receiver is sufficient on its own — no SQL literal, no import.

    Isolating the receiver signal matters: the fixture above also carries a SQL
    literal, so it stays green even if receiver matching breaks entirely. A
    mutation run caught exactly that (catalogue `OI7-M4`) — the recall guard was
    passing for the wrong reason.
    """
    source = JAVA_JDBC_TEMPLATE_NO_FILE_EVIDENCE
    assert "SELECT" not in source and "import" not in source, "fixture must isolate the receiver"
    sinks = _sql_sinks(_nodes(source))
    assert len(sinks) == 1
    assert sinks[0].detail["receiver"] == "jdbcTemplate"


JAVA_PREFIXED_RECEIVER = """
public class StockDao {
    Stock load(long id) {
        return readOnlyEntityManager.find(Stock.class, id);
    }
}
"""

JAVA_REST_TEMPLATE = """
public class StockClient {
    Stock fetch(String ref) {
        return restTemplate.get(ref, Stock.class);
    }
}
"""


def test_qualified_receiver_is_matched_on_its_trailing_identifier() -> None:
    """`this.stockRepository.query(...)` is a database receiver despite the qualifier."""
    assert _sql_sinks(_nodes(JAVA_FIELD_ACCESS_RECEIVER))


def test_prefixed_receiver_is_matched_on_its_token_pairs() -> None:
    """`readOnlyEntityManager` is an EntityManager — a read-replica handle is common.

    Whole-identifier and single-token matching both miss it (`entity` and `manager`
    are not vocabulary entries alone); only the adjacent-pair check resolves it.

    The fixture is chosen so that *no other signal* can carry the test: the call
    text matches no library hint, and the file has no SQL literal or import. An
    earlier version used `readOnlyJdbcTemplate`, which passes via the
    `JdbcTemplate` hint and so never exercised the pair branch at all — mutation
    catalogue `OI7-M4` is what exposed that.
    """
    from src2sink.extractors.patterns import SQL_EXECUTION_CALL_HINTS

    assert not any(hint in JAVA_PREFIXED_RECEIVER for hint in SQL_EXECUTION_CALL_HINTS), (
        "fixture must isolate the receiver signal — no call-text hint may match"
    )
    assert _sql_sinks(_nodes(JAVA_PREFIXED_RECEIVER))


def test_rest_template_is_not_a_database_receiver() -> None:
    """The negative that token-pair matching must preserve.

    `restTemplate` splits to rest+template exactly as `jdbcTemplate` splits to
    jdbc+template; the pair check must accept one and reject the other, which is
    why matching is on pairs rather than on the `template` token alone.
    """
    assert _sql_sinks(_nodes(JAVA_REST_TEMPLATE)) == []


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


def test_placeholder_query_is_parameterised() -> None:
    """A `?` placeholder in a statement with no construction is genuinely parameterised.

    OI-10 turned this field from a tri-state boolean into a posture, so the value
    is now the label `"parameterised"` rather than `True`. The statement here is a
    file-level constant, which is only trusted because the file builds no SQL
    dynamically and holds exactly one candidate.
    """
    sinks = _sql_sinks(_nodes(JAVA_JDBC_TEMPLATE))
    assert sinks[0].detail["parameterised"] == "parameterised"


def test_unknown_parameterisation_is_not_reported_as_parameterised(tmp_path) -> None:
    """The catalogue must not file `unknown` under the safe-looking posture.

    Downstream half of the same defect: a truthiness test on the tri-state counts
    "we could not tell" as "parameterised", which is the claim the tri-state exists
    to stop making. Caught by catalogue mutant `OI7-M7`.
    """
    from src2sink.aggregators.taint_buckets import TaintCatalogueBuckets
    from src2sink.aggregators.taint_writers import write_sql_catalogues

    buckets = TaintCatalogueBuckets(
        sql_sinks=[
            {
                "repo": "test/sample",
                "file": "src/Sample.java",
                "line": 4,
                "detail": {"symbol": "execute", "parameterised": "unknown"},
            },
        ],
    )
    write_sql_catalogues(tmp_path, buckets)
    md = (tmp_path / "sql-execution-sinks.md").read_text(encoding="utf-8")
    assert "unknown" in md
    assert "parameterised" not in md


# --------------------------------------------------------------------------
# OI-10: `parameterised` is a posture, not a safety verdict
# --------------------------------------------------------------------------

JAVA_SAFE_CONSTANT_PLUS_BUILT_QUERY = """
public class StockDao {
    private static final String SAFE = "SELECT ref FROM stock WHERE id = ?";

    List<Stock> search(String clause) {
        String sql = "SELECT * FROM stock WHERE " + clause;
        return jdbcTemplate.query(sql, mapper);
    }
}
"""

JAVA_MIXED_STATEMENT = """
public class StockDao {
    List<Stock> find(String ref, long id) {
        return jdbcTemplate.query(
            "SELECT * FROM stock WHERE ref = '" + ref + "' AND id = ?", mapper, id);
    }
}
"""

JAVA_MIXED_STATEMENT_REVERSED = """
public class StockDao {
    List<Stock> find(String ref, long id) {
        return jdbcTemplate.query(
            "SELECT * FROM stock WHERE id = ? AND ref = '" + ref + "'", mapper, id);
    }
}
"""

JAVA_UNRESOLVABLE_STATEMENT = """
import java.sql.PreparedStatement;

public class StockJob {
    void run(Runner runner, PreparedStatement statement) {
        runner.execute(statement);
    }
}
"""


def test_unrelated_safe_constant_does_not_certify_a_built_statement() -> None:
    """OI-10: a placeholder elsewhere in the file is not evidence about this call.

    The call executes a concatenated string. The scan already reports that as a
    `sql` source with `pattern=concatenated`; labelling the sink `parameterised`
    on the strength of an unrelated constant makes the output contradict itself,
    and a reviewer filtering for raw statements never sees the call site.
    """
    nodes = _nodes(JAVA_SAFE_CONSTANT_PLUS_BUILT_QUERY)
    assert any(n.family == "sql" and n.kind == "source" for n in nodes), (
        "fixture must contain a detectably constructed statement"
    )
    sinks = _sql_sinks(nodes)
    assert sinks
    assert sinks[0].detail["parameterised"] != "parameterised"
    assert sinks[0].detail["parameterised"] is not True


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("concat-then-placeholder", JAVA_MIXED_STATEMENT),
        ("placeholder-then-concat", JAVA_MIXED_STATEMENT_REVERSED),
    ],
)
def test_concatenated_and_parameterised_is_mixed(name: str, source: str) -> None:
    """A placeholder does not undo a concatenation in the same statement.

    Both operand orders must agree. Before the posture existed the answer
    depended on which fragment the literal pattern happened to match, so
    `"… ref = '" + ref + "' AND id = ?"` read as raw and its mirror as
    parameterised — right once, by luck.
    """
    sinks = _sql_sinks(_nodes(source))
    assert sinks, f"{name} produced no sql sink"
    assert sinks[0].detail["parameterised"] == "mixed"


def test_unresolvable_statement_is_unknown_not_parameterised() -> None:
    """No statement in scope means no posture — never the safe one."""
    sinks = _sql_sinks(_nodes(JAVA_UNRESOLVABLE_STATEMENT))
    assert sinks
    assert sinks[0].detail["parameterised"] == "unknown"


def test_mixed_is_not_counted_as_parameterised_in_the_catalogue(tmp_path) -> None:
    """The writer must keep `mixed` distinct from the safe-looking posture."""
    from src2sink.aggregators.taint_buckets import TaintCatalogueBuckets
    from src2sink.aggregators.taint_writers import write_sql_catalogues

    buckets = TaintCatalogueBuckets(
        sql_sinks=[{
            "repo": "test/sample", "file": "src/Sample.java", "line": 4,
            "detail": {"symbol": "query", "parameterised": "mixed"},
        }],
    )
    write_sql_catalogues(tmp_path, buckets)
    md = (tmp_path / "sql-execution-sinks.md").read_text(encoding="utf-8")
    assert "mixed" in md
    assert "parameterised" not in md


JAVA_SINGLE_CONSTRUCTED_STATEMENT = """
public class StockDao {
    List<Stock> search(String clause) {
        String sql = "SELECT * FROM stock WHERE " + clause;
        return jdbcTemplate.query(sql, mapper);
    }
}
"""

JAVA_TERNARY_IN_CALL = """
public class StockDao {
    List<Stock> find(String ref) {
        return jdbcTemplate.query(
            "SELECT * FROM stock WHERE ref = '" + (ref != null ? ref : "") + "'", mapper);
    }
}
"""


def test_single_constructed_statement_is_attributable_as_raw() -> None:
    """One unambiguous candidate is attributable even when it is constructed.

    `unknown` would be safe but needlessly vague: there is exactly one statement
    in the file, it is concatenated, and it is what this call runs.
    """
    sinks = _sql_sinks(_nodes(JAVA_SINGLE_CONSTRUCTED_STATEMENT))
    assert sinks
    assert sinks[0].detail["parameterised"] == "raw"


def test_a_question_mark_outside_a_literal_is_not_a_placeholder() -> None:
    """A ternary in the call is not a bind parameter.

    Placeholders are only counted inside string literals; searching the whole
    call text would read `ref != null ? ref : ""` as parameterisation and
    upgrade an injectable statement from `raw` to `mixed`.
    """
    sinks = _sql_sinks(_nodes(JAVA_TERNARY_IN_CALL))
    assert sinks
    assert "?" in sinks[0].detail["raw"], "fixture must contain a non-placeholder ?"
    assert sinks[0].detail["parameterised"] == "raw"


def test_legacy_boolean_posture_is_reported_as_unknown(tmp_path) -> None:
    """A metabase written before OI-10 stored True/False; neither is a posture.

    Re-reading old output must not translate `True` into `parameterised` — the
    old boolean was computed by the very heuristic OI-10 removed, so carrying it
    forward would re-import the claim rather than the data.
    """
    from src2sink.aggregators.taint_buckets import TaintCatalogueBuckets
    from src2sink.aggregators.taint_writers import write_sql_catalogues

    buckets = TaintCatalogueBuckets(
        sql_sinks=[{
            "repo": "test/sample", "file": "src/Legacy.java", "line": 1,
            "detail": {"symbol": "query", "parameterised": True},
        }],
    )
    write_sql_catalogues(tmp_path, buckets)
    md = (tmp_path / "sql-execution-sinks.md").read_text(encoding="utf-8")
    assert "unknown" in md
    assert "parameterised" not in md
