"""OI-26: file-scoped evidence must not overrule a receiver.

`OI-7` replaced "name alone" with "name plus one positive signal": a database
receiver, a library hint in the call text, or file-level SQL evidence. Three
terms OR'd together — so the weakest decides once satisfied, and the weakest is
file-scoped.

The result: one real SQL statement anywhere in a file admits *every* sink-named
call in that file, whatever its receiver. `httpClient.execute(r)` becomes a SQL
execution sink because a JDBC query sits in the same class. And because execution
sinks feed `link_raw_code_payload_endpoints`, it can still fabricate the
injection endpoint `OI-7` was raised to stop.

The fix has two sides and needs both. Tightening alone would drop real
`PreparedStatement` calls, because `ps` and `pstmt` are missing from the receiver
vocabulary while `stmt` and `conn` are present — so the guard would start
rejecting the very calls it exists to catch.

This is the first classification fix made *after* the observation layer: it
changes `_sql_verdict` over records already on disk, not the extractor.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.patterns import receiver_is_database
from src2sink.extractors.unified import extract_from_file

_SQL_BEARING_FILE = """
public class Mixed {{
    private final JdbcTemplate jdbcTemplate;
    private final {type} {receiver};
    void real() {{ jdbcTemplate.query("SELECT ref FROM stock", mapper); }}
    void other(Object x) {{ {receiver}.{call}(x); }}
}}
"""


def _sql_sinks(source: str):
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/Mixed.java", language="java", source=source
    )[0]
    return [n for n in nodes if n.family == "sql" and n.kind == "sink"]


@pytest.mark.parametrize(
    ("type_", "receiver", "call"),
    [
        ("HttpClient", "httpClient", "execute"),
        ("MessageDigest", "digest", "update"),
        ("ExecutorService", "pool", "execute"),
        ("Cache", "cache", "get"),
    ],
)
def test_a_non_database_receiver_is_not_rescued_by_the_file(type_, receiver, call):
    """The receiver is local evidence and beats a file-wide fact about other code."""
    source = _SQL_BEARING_FILE.format(type=type_, receiver=receiver, call=call)
    symbols = {(n.detail["receiver"], n.detail["symbol"]) for n in _sql_sinks(source)}
    assert (receiver, call) not in symbols
    # ...and the real one is untouched.
    assert ("jdbcTemplate", "query") in symbols


@pytest.mark.parametrize("receiver", ["ps", "pstmt", "stmt", "conn", "jdbcTemplate"])
def test_prepared_statement_receivers_are_recognised(receiver):
    """Tightening without widening would drop the calls the guard exists to catch.

    `ps` and `pstmt` are the ordinary abbreviations for a `PreparedStatement`, and
    were absent while `stmt` and `conn` were present.
    """
    assert receiver_is_database(receiver) is True


def test_a_call_with_no_receiver_is_still_rescued_by_file_evidence():
    """Nothing local to judge, so the file is the only evidence there is.

    This is the case file-scoped evidence was added for, and it must survive.
    """
    source = """
    public class Dao {
        void run(String sql) { execute("SELECT ref FROM stock WHERE x = " + sql); }
    }
    """
    assert [n.detail["symbol"] for n in _sql_sinks(source)] == ["execute"]


def test_a_library_hint_still_overrides_an_unrecognised_receiver():
    """A hint names the SQL API outright, so it is self-evidencing."""
    source = """
    public class Dao {
        private final NamedParameterJdbcTemplate tpl;
        void run() { tpl.query("SELECT ref FROM stock", params, mapper); }
    }
    """
    assert [n.detail["symbol"] for n in _sql_sinks(source)] == ["query"]


def test_no_fabricated_injection_endpoint():
    """The harm OI-7 named, still reachable through OI-26 until now.

    An HTTP proxy with a field named `sql`, in a file that happens to contain a
    real query, produced a `raw-code-payload` finding — an injection endpoint
    that never existed, sending a reviewer to audit safe code.
    """
    # File-level SQL evidence with no real execution sink: an audit statement
    # held as a constant. Before the fix that literal admitted `httpClient.execute`
    # as an execution sink, and the endpoint plus the `sql`-named body parameter
    # then fabricated a raw-code-payload finding from it.
    source = """
    @RestController
    public class Proxy {
        private static final String AUDIT_SQL = "SELECT ref FROM stock";
        private final HttpClient httpClient;
        @PostMapping("/forward")
        public String forward(@RequestBody String sql) { return httpClient.execute(sql); }
    }
    """
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/Proxy.java", language="java", source=source
    )[0]
    payloads = [n for n in nodes if n.family == "raw-code-payload"]
    assert payloads == [], "an HTTP forward was reported as a SQL injection endpoint"


def test_the_observation_is_unchanged_by_the_fix():
    """Only the classification moves. The record of what was seen is the same.

    This is what makes the fix re-runnable over stored data rather than a rescan:
    if the observation had to change, it would not be a classifier fix.
    """
    source = _SQL_BEARING_FILE.format(
        type="HttpClient", receiver="httpClient", call="execute"
    )
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/Mixed.java", language="java", source=source
    )[0]
    obs = {n.detail["symbol"]: n.detail for n in nodes if n.family == "call-site"}
    assert obs["execute"]["receiver"] == "httpClient"
    assert obs["execute"]["receiver_is_database"] is False
    assert obs["execute"]["file_sql_evidence"] is True
