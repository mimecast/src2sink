"""Regression tests for OI-8 — SQL assembled by formatting must be detected.

`SQL_SOURCE_RX` covered concatenation and Python f-strings. A confirmed injection
built with `String.format` produced no `sql` node at all, and two adjacent holes
turned out to be worse:

* the concatenation patterns excluded *both* quote characters from the literal
  body, so `"… WHERE ref = '" + ref + "'"` — the canonical injection shape —
  could not be spanned;
* the template pattern required the interpolation to appear *before* the SQL
  keyword, while real templates interpolate after it.

For a tool whose whole purpose is finding injections, a missed dynamic query is
the most expensive kind of miss: nothing downstream says "we looked here and
found nothing", so the call site is simply absent from the catalogue.

Fixture names follow the sanitised placeholder set used across the suite.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file


def _sql_sources(source: str, *, language: str = "java", rel_path: str = "src/StockDao.java"):
    """Return the `sql` source nodes extracted from one source string."""
    nodes, _edges = extract_from_file(
        repo_id="test/sample", rel_path=rel_path, language=language, source=source,
    )
    return [n for n in nodes if n.family == "sql" and n.kind == "source"]


# --------------------------------------------------------------------------
# Formatting functions
# --------------------------------------------------------------------------

JAVA_STRING_FORMAT = """
public class StockDao {
    String query(String ref) {
        return String.format("SELECT * FROM stock WHERE ref = '%s'", ref);
    }
}
"""

JAVA_FORMATTED = """
public class StockDao {
    String query(String ref) {
        return "SELECT * FROM stock WHERE ref = '%s'".formatted(ref);
    }
}
"""

JAVA_MESSAGE_FORMAT = """
public class StockDao {
    String query(String ref) {
        return MessageFormat.format("SELECT * FROM stock WHERE ref = ''{0}''", ref);
    }
}
"""

PYTHON_PERCENT = """
def query(ref):
    return "SELECT * FROM stock WHERE ref = '%s'" % ref
"""

PYTHON_DOT_FORMAT = """
def query(ref):
    return "SELECT * FROM stock WHERE ref = '{}'".format(ref)
"""

KOTLIN_TEMPLATE = """
fun query(ref: String): String {
    return "SELECT * FROM stock WHERE ref = '$ref'"
}
"""

KOTLIN_BRACED_TEMPLATE = """
fun query(ref: String): String {
    return "SELECT * FROM stock WHERE ref = '${ref.trim()}'"
}
"""


@pytest.mark.parametrize(
    ("name", "source", "language", "rel_path", "expected_pattern"),
    [
        ("java-string-format", JAVA_STRING_FORMAT, "java", "src/StockDao.java", "format-call"),
        ("java-formatted", JAVA_FORMATTED, "java", "src/StockDao.java", "format-call"),
        ("java-message-format", JAVA_MESSAGE_FORMAT, "java", "src/StockDao.java", "format-call"),
        ("python-percent", PYTHON_PERCENT, "python", "dao.py", "format-percent"),
        ("python-dot-format", PYTHON_DOT_FORMAT, "python", "dao.py", "format-call"),
        ("kotlin-template", KOTLIN_TEMPLATE, "kotlin", "Dao.kt", "template"),
        ("kotlin-braced-template", KOTLIN_BRACED_TEMPLATE, "kotlin", "Dao.kt", "template"),
    ],
)
def test_formatted_sql_is_a_source(
    name: str, source: str, language: str, rel_path: str, expected_pattern: str,
) -> None:
    """OI-8: SQL assembled by a format call or template must produce a node.

    The `pattern` label is asserted, not just the node's existence. Several
    patterns match the same construction — a `String.format` whose format string
    contains `%s` matches the template pattern too — so an existence-only
    assertion stays green even if the format-call patterns are deleted, and the
    label is what tells a reviewer how the statement was built.
    """
    nodes = _sql_sources(source, language=language, rel_path=rel_path)
    assert nodes, f"{name} produced no sql source"
    assert [n.detail["pattern"] for n in nodes] == [expected_pattern]


# --------------------------------------------------------------------------
# Concatenation — including the shape 1.1.0 could not span
# --------------------------------------------------------------------------

JAVA_CONCAT_PLAIN = """
public class StockDao {
    String query(String ref) {
        return "SELECT * FROM stock WHERE ref = " + ref;
    }
}
"""

JAVA_CONCAT_EMBEDDED_QUOTE = """
public class StockDao {
    String query(String ref) {
        return "SELECT * FROM stock WHERE ref = '" + ref + "'";
    }
}
"""

JAVA_CONCAT_TRAILING_KEYWORD = """
public class StockDao {
    String query(String clause) {
        return "SELECT * FROM stock" + " WHERE " + clause;
    }
}
"""


def test_plain_concatenation_is_still_a_source() -> None:
    """The shape that already worked — the recall guard for the rewritten patterns."""
    assert _sql_sources(JAVA_CONCAT_PLAIN)


def test_concatenation_with_an_embedded_quote_is_a_source() -> None:
    """OI-8: the canonical injection shape, missed by 1.1.0.

    The literal body excluded both `"` and `'`, so a double-quoted string
    containing `'` could not be spanned — and `WHERE ref = '" + ref + "'` is
    exactly how a string-built query with a quoted parameter looks.
    """
    assert _sql_sources(JAVA_CONCAT_EMBEDDED_QUOTE)


def test_concatenation_onto_a_sql_literal_is_a_source() -> None:
    """A keyword-bearing literal followed by concatenation, in either direction."""
    assert _sql_sources(JAVA_CONCAT_TRAILING_KEYWORD)


# --------------------------------------------------------------------------
# Precision — widening a source pattern needs its negatives
# --------------------------------------------------------------------------

JAVA_NON_SQL_FORMAT = """
public class Greeter {
    String greet(String name) {
        return String.format("Hello %s, welcome back", name);
    }
}
"""

JAVA_SQL_IN_A_COMMENT = """
public class StockDao {
    // SELECT is faster than a full scan here, so we use the index instead.
    List<Stock> all() {
        return repository.findAll();
    }
}
"""

JAVA_STATIC_SQL_CONSTANT = """
public class StockDao {
    private static final String FIND = "SELECT ref FROM stock WHERE id = ?";
}
"""


def test_non_sql_format_call_is_not_a_source() -> None:
    """`String.format` alone is not evidence — the format string must carry SQL."""
    assert _sql_sources(JAVA_NON_SQL_FORMAT) == []


def test_sql_keyword_in_a_comment_is_not_a_source() -> None:
    """A keyword in prose is not a query; the patterns require a string literal."""
    assert _sql_sources(JAVA_SQL_IN_A_COMMENT) == []


def test_static_sql_constant_is_not_a_source() -> None:
    """A fully static statement is not dynamically constructed, so not a source."""
    assert _sql_sources(JAVA_STATIC_SQL_CONSTANT) == []


# --------------------------------------------------------------------------
# One statement should not become several nodes
# --------------------------------------------------------------------------

def test_one_constructed_statement_yields_one_node_per_line() -> None:
    """Overlapping patterns must not inflate a single statement into many findings.

    `String.format("SELECT … '%s'", ref)` matches the format-call pattern *and*
    the template pattern, since its format string carries both a SQL keyword and
    an interpolation marker. Emitting a node each would make one injection look
    like a cluster. The fixture is chosen for that overlap — a plain
    concatenation matches only one pattern and so cannot detect the loss of
    de-duplication.
    """
    from src2sink.extractors.patterns import SQL_SOURCE_RX

    line = JAVA_STRING_FORMAT.splitlines()[3]
    overlapping = [kind for pat, kind in SQL_SOURCE_RX if pat.search(line)]
    assert len(overlapping) > 1, f"fixture must match several patterns, got {overlapping}"

    nodes = _sql_sources(JAVA_STRING_FORMAT)
    lines = [n.line for n in nodes]
    assert len(lines) == len(set(lines)), f"duplicate nodes on one line: {lines}"


# --------------------------------------------------------------------------
# OI-11: the SQL keyword may live in a constant the fragments only reference
# --------------------------------------------------------------------------

JAVA_CONSTANT_BASE_PLUS_CLAUSE = """
public class StockDao {
    private static final String SAFE = "SELECT ref FROM stock WHERE id = ?";

    List<Stock> find(String ref, long id) {
        String sql = SAFE + " AND ref = '" + ref + "'";
        return jdbcTemplate.query(sql, mapper, id);
    }
}
"""

JAVA_NON_SQL_CONSTANT_CONCAT = """
public class Greeter {
    private static final String GREETING = "Hello there";

    String greet(String name) {
        return GREETING + ", " + name;
    }
}
"""


def test_constant_base_query_with_appended_clause_is_a_source() -> None:
    """OI-11: the base query in a constant, the injection in the appended clause.

    Every other pattern anchors on a SQL keyword inside the literal next to the
    operator. Here the keyword is in `SAFE` and the concatenated fragments —
    `" AND ref = '"` and `"'"` — carry none, so nothing matched and the most
    common hand-written-DAO shape produced no finding at all.
    """
    nodes = _sql_sources(JAVA_CONSTANT_BASE_PLUS_CLAUSE)
    assert nodes, "constant-mediated concatenation produced no sql source"
    assert [n.line for n in nodes] == [6], "the node belongs on the concatenation line"


def test_non_sql_constant_does_not_make_a_concatenation_sql() -> None:
    """Resolving identifiers must not manufacture SQL out of ordinary strings."""
    assert _sql_sources(JAVA_NON_SQL_CONSTANT_CONCAT) == []
