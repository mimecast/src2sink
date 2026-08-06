"""Unit tests for the tree-sitter walking helpers.

`ast_walk` decides, for every call site in every supported language, what the
method is called and what it was called *on*. Both answers feed the `sql` family's
evidence gate (OI-7), so a language whose walker silently returns nothing loses
that language's SQL sinks entirely — with no error and no empty-result signal.

At 71% line coverage this module was the least-tested part of the detection path
while carrying some of its most consequential logic. These tests exercise each
language's name and receiver extraction directly, rather than through
`extract_from_file`, so a gap shows up here rather than as a missing node three
layers away.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.ast_walk import (
    CALL_NODE_TYPES,
    extract_call_name,
    extract_call_receiver,
    iter_calls,
    line_number,
    node_text,
    walk,
)
from src2sink.extractors.base import parse_source, supported_languages


def _calls(source: str, language: str):
    """Return ``[(name, receiver)]`` for every call in a source string."""
    raw = source.encode("utf-8")
    tree = parse_source(language, raw)
    return [
        (name, extract_call_receiver(raw, node, language))
        for node, name in iter_calls(raw, tree.root_node, language)
    ]


# --------------------------------------------------------------------------
# Name and receiver, per language
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("language", "source", "expected"),
    [
        (
            "java",
            "class A { void m() { jdbcTemplate.query(SQL); execute(x); this.dao.find(y); } }",
            [("query", "jdbcTemplate"), ("execute", None), ("find", "this.dao")],
        ),
        (
            "python",
            "def m():\n    cursor.execute(SQL)\n    self.db.execute(q)\n    execute(z)\n",
            [("execute", "cursor"), ("execute", "self.db"), ("execute", None)],
        ),
        (
            "go",
            "package p\nfunc m() { db.Query(sql); Query(sql) }\n",
            [("Query", "db"), ("Query", None)],
        ),
        (
            "javascript",
            "function m() { conn.query(sql); query(sql); }",
            [("query", "conn"), ("query", None)],
        ),
    ],
)
def test_call_name_and_receiver(language: str, source: str, expected) -> None:
    """Each grammar names the method and the thing it was called on."""
    assert _calls(source, language) == expected


def test_a_bare_call_has_no_receiver() -> None:
    """`execute(x)` is not a method on anything — the distinction the gate turns on."""
    raw = b"class A { void m() { execute(x); } }"
    tree = parse_source("java", raw)
    node, _name = next(iter(iter_calls(raw, tree.root_node, "java")))
    assert extract_call_receiver(raw, node, "java") is None


def test_a_chained_receiver_is_returned_whole() -> None:
    """`QueryRequest.builder().sql(x)` — the receiver is the preceding expression."""
    raw = b"class A { void m() { QueryRequest.builder().sql(x); } }"
    tree = parse_source("java", raw)
    receivers = [
        extract_call_receiver(raw, node, "java")
        for node, _name in iter_calls(raw, tree.root_node, "java")
    ]
    assert "QueryRequest.builder()" in receivers


# --------------------------------------------------------------------------
# Kotlin: a documented gap, asserted so it cannot regress quietly
# --------------------------------------------------------------------------

@pytest.mark.skipif("kotlin" not in supported_languages(), reason="kotlin grammar absent")
def test_kotlin_calls_are_named_by_the_kotlin_walker() -> None:
    """Kotlin call sites reach the AST pass (OI-13).

    Replaces, rather than deletes, the test that asserted the opposite. That test
    documented a real gap: `extract_call_name` routed Kotlin to
    `call_name_java_kotlin`, which requires a `method_invocation` node — a Java
    grammar type Kotlin never produces, since it uses `call_expression` wrapping a
    `navigation_expression`. So every Kotlin call site was invisible and Kotlin
    SQL sinks were found only when a regex tier happened to match.

    Kept as a test rather than removed so the gap cannot reopen quietly, which is
    how it stayed open in the first place.
    """
    raw = b"fun m() { jdbcTemplate.query(SQL) }"
    tree = parse_source("kotlin", raw)
    names = [name for _node, name in iter_calls(raw, tree.root_node, "kotlin")]
    assert names == ["query"]



def test_an_unsupported_language_yields_no_calls() -> None:
    """A language with no entry in CALL_NODE_TYPES walks to nothing, not an error."""
    raw = b"class A { void m() { x.y(); } }"
    tree = parse_source("java", raw)
    assert list(iter_calls(raw, tree.root_node, "cobol")) == []
    assert "cobol" not in CALL_NODE_TYPES


def test_name_and_receiver_are_none_for_a_non_call_node() -> None:
    """Handed something that is not a call, the extractors decline rather than guess."""
    raw = b"class A { int x = 1; }"
    tree = parse_source("java", raw)
    root = tree.root_node
    assert extract_call_name(raw, root, "java") is None
    assert extract_call_receiver(raw, root, "java") is None
    assert extract_call_name(raw, root, "python") is None
    assert extract_call_name(raw, root, "go") is None
    assert extract_call_name(raw, root, "javascript") is None


def test_walk_visits_every_node_once() -> None:
    """The traversal is the base every extractor builds on."""
    raw = b"class A { void m() { x(); } }"
    tree = parse_source("java", raw)
    nodes = list(walk(tree.root_node))
    # Identity is by (type, span): tree-sitter hands back a fresh Python wrapper
    # on each attribute access, so `is` compares nothing useful.
    spans = [(n.type, n.start_byte, n.end_byte) for n in nodes]
    assert len(spans) == len(set(spans)), "a node was visited twice"
    assert spans[0] == (
        tree.root_node.type, tree.root_node.start_byte, tree.root_node.end_byte,
    ), "traversal must start at the root"


def test_node_text_and_line_number_locate_a_match() -> None:
    """A finding a reader cannot locate is not much of a finding."""
    raw = b"class A {\n  void m() {\n    jdbcTemplate.query(SQL);\n  }\n}"
    tree = parse_source("java", raw)
    node, name = next(iter(iter_calls(raw, tree.root_node, "java")))
    assert name == "query"
    assert line_number(raw, node) == 3
    assert node_text(raw, node) == "jdbcTemplate.query(SQL)"


def test_node_text_survives_invalid_utf8() -> None:
    """Scanned repos contain arbitrary bytes; decoding must not raise."""
    raw = b"class A { void m() { x(\xff\xfe); } }"
    tree = parse_source("java", raw)
    assert node_text(raw, tree.root_node)


# --------------------------------------------------------------------------
# Extractor helpers that carry precision logic but had no direct tests
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("rel_path", "line_text", "field", "skipped"),
    [
        # `query` and `expression` are dangerous-payload names in general, but in
        # a regex module they name a pattern, not injectable input.
        ("src/regex/Matcher.java", "String query = ...", "query", True),
        ("src/RegexHelper.java", "String expression = ...", "expression", True),
        ("src/StockDao.java", "String query = ...", "query", False),
        # `script` in an HTML resource or a line building a <script> tag is
        # markup, not executable payload.
        ("src/htmlPages/Page.java", "String script = ...", "script", True),
        ("src/Page.java", '.append("<script', "script", True),
        ("src/StockDao.java", "String script = ...", "script", False),
    ],
)
def test_dangerous_payload_field_suppression(
    rel_path: str, line_text: str, field: str, skipped: bool,
) -> None:
    """Field-name heuristics need path context or they flag half the fleet.

    A field called `query` is a dangerous payload in a DAO and a regex in a
    matcher. Suppressing by path is what keeps the `data-class-field` family from
    firing on every HTML template and regex helper in the corpus — precision
    logic that had no direct test.
    """
    from src2sink.extractors.regex_extractors import _skip_dangerous_payload_field

    assert _skip_dangerous_payload_field(rel_path, line_text, field) is skipped


@pytest.mark.parametrize(
    ("language", "bucket"),
    [
        ("java", "java-kotlin"), ("kotlin", "java-kotlin"), ("python", "python"),
        ("javascript", "javascript"), ("typescript", "javascript"), ("tsx", "javascript"),
        ("go", "go"), ("cobol", None),
    ],
)
def test_http_language_bucket(language: str, bucket: str | None) -> None:
    """Inbound-route patterns are grouped by ecosystem, not by file extension."""
    from src2sink.extractors.regex_extractors import _http_language_bucket

    assert _http_language_bucket(language) == bucket


# ---------------------------------------------------------------------------
# OI-13: Kotlin parity with Java in the AST pass
# ---------------------------------------------------------------------------

_KOTLIN_DAO = b'''class StockDao(private val jdbcTemplate: JdbcTemplate) {
    fun find(id: Long): List<Stock> {
        return jdbcTemplate.query("SELECT ref FROM stock WHERE id = ?", mapper, id)
    }
    fun bare() { execute("SELECT 1") }
    fun chained() { this.dao.findMatching(filter) }
}'''


def test_kotlin_call_receivers_match_the_java_shape() -> None:
    """The receiver is what tells `jdbcTemplate.execute` from `httpClient.execute`.

    Without it every Kotlin SQL classification falls back to file-level evidence,
    which `OI-26` showed is too coarse to decide a call on its own.
    """
    tree = parse_source("kotlin", _KOTLIN_DAO)
    found = {
        name: extract_call_receiver(_KOTLIN_DAO, node, "kotlin")
        for node, name in iter_calls(_KOTLIN_DAO, tree.root_node, "kotlin")
    }
    assert found["query"] == "jdbcTemplate"
    assert found["bare"] is None if "bare" in found else True
    assert found["execute"] is None
    assert found["findMatching"] == "this.dao"


def test_a_kotlin_call_is_yielded_once() -> None:
    """`navigation_expression` is a property access, not a call.

    Listing it as a call node type made `jdbcTemplate.query(...)` arrive twice —
    once as the call and once as the navigation beneath it — which would double
    every Kotlin finding.
    """
    tree = parse_source("kotlin", _KOTLIN_DAO)
    names = [name for _n, name in iter_calls(_KOTLIN_DAO, tree.root_node, "kotlin")]
    assert names.count("query") == 1
    assert sorted(names) == ["execute", "findMatching", "query"]


def test_every_supported_language_names_a_call() -> None:
    """The class-wide guard: a language in CALL_NODE_TYPES must yield a call name.

    `kotlin` was in the table and produced nothing, because the table and the
    name extractor disagreed and neither said so.
    """
    samples = {
        "java": (b"class A { void m() { dao.query(SQL); } }", "query"),
        "kotlin": (b"fun m() { dao.query(SQL) }", "query"),
        "python": (b"def m():\n    dao.query(SQL)\n", "query"),
        "javascript": (b"function m() { dao.query(SQL); }", "query"),
        "go": (b"package p\nfunc m() { dao.Query(SQL) }\n", "Query"),
    }
    for language, (raw, expected) in samples.items():
        if language not in supported_languages():
            continue
        tree = parse_source(language, raw)
        names = [n for _node, n in iter_calls(raw, tree.root_node, language)]
        assert expected in names, f"{language} yielded {names}"
