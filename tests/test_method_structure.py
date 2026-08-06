"""OI-17, step one: nodes know which method they are in.

`src2sink` finds sources and finds sinks. It does not connect them, and the first
reason is that there is nothing to connect: a `FlowNode` records `file` and
`line` and nothing about the method it sits in. There is no "entrypoint 1 of B"
to reason about — only a line number.

Measured on the canonical layered shape before this change:

    StockController.java   -> ['http-in/source']      entrypoint found
    StockService.java      -> (nothing)               middle layer invisible
    StockDao.java          -> ['sql/sink', ...]       injectable sink found
    edges produced: 0

This step gives every node its enclosing method and records the declarations
themselves. Resolution and reachability build on it; neither is possible without
it.

Deliberately *not* in this step: resolving a call to the method it invokes, and
searching for paths. Those are the next two, and they consume what this produces.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file

_JAVA = """
package com.example.warehouse;

@RestController
public class StockController {
    private final StockService stockService;

    @PostMapping("/stock")
    public StockResult submit(@RequestBody StockRequest req) {
        return stockService.process(req.getFilter());
    }

    void unrelated() {
        log.info("nothing to do");
    }
}
"""

_KOTLIN = """
@RestController
class StockController(private val stockService: StockService) {

    @PostMapping("/stock")
    fun submit(@RequestBody req: StockRequest): StockResult =
        stockService.process(req.filter)

    fun unrelated() {
        log.info("nothing to do")
    }
}
"""


def _nodes(source: str, language: str, rel_path: str):
    return extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source
    )[0]


def _decls(nodes):
    return {n.detail["method"]: n.detail for n in nodes if n.family == "method-decl"}


def test_method_declarations_are_recorded():
    """The unit a path is made of. Nothing was extracted for them at all."""
    decls = _decls(_nodes(_JAVA, "java", "src/StockController.java"))
    assert sorted(decls) == ["submit", "unrelated"]
    assert decls["submit"]["class"] == "StockController"
    assert decls["submit"]["params"] == ["req"]


def test_a_declaration_records_its_span():
    """Containment is what assigns a node to a method, so the span must be exact."""
    decls = _decls(_nodes(_JAVA, "java", "src/StockController.java"))
    submit, unrelated = decls["submit"], decls["unrelated"]
    assert submit["end_line"] > submit["start_line"]
    assert submit["end_line"] < unrelated["start_line"]


def test_every_node_knows_its_enclosing_method():
    """`file:line` alone cannot say which door a sink sits behind."""
    nodes = _nodes(_JAVA, "java", "src/StockController.java")
    placed = {
        n.family: (n.detail.get("enclosing_class"), n.detail.get("enclosing_method"))
        for n in nodes
        if n.family in ("http-in", "entry-point")
    }
    assert placed["http-in"] == ("StockController", "submit")
    # Derived nodes inherit the scope of the observation they came from, because
    # derivation also runs over a stored record with no extraction context.
    assert placed["entry-point"] == ("StockController", "submit")


def test_a_node_outside_any_method_is_not_falsely_placed():
    """A field or a class-level annotation belongs to no method, and must say so.

    Guessing the nearest method would attach a class-level finding to whichever
    method happened to follow it.
    """
    # The field is declared *after* the method deliberately. With it before, a
    # containment check that forgot the span end would still leave it unplaced,
    # and the test would pass while the rule was broken.
    source = """
    public class Payload {
        void run() { }
        private String sql;
        private String customerEmail;
    }
    """
    nodes = _nodes(source, "java", "src/Payload.java")
    fields = [n for n in nodes if n.family in ("data-class-field", "pii-field")]
    assert fields, "fixture must produce a class-level finding"
    assert all(f.detail.get("enclosing_method") is None for f in fields)


def test_the_innermost_method_wins():
    """A nested or local function must not be attributed to its outer method."""
    source = """
def outer():
    def inner():
        cursor.execute("SELECT ref FROM stock")
    return inner
"""
    nodes = _nodes(source, "python", "src/nested.py")
    calls = [n for n in nodes if n.family == "call-site"]
    assert calls, "fixture must produce a call observation"
    assert calls[0].detail["enclosing_method"] == "inner"


@pytest.mark.parametrize(
    ("source", "language", "rel_path"),
    [
        (_JAVA, "java", "src/StockController.java"),
        (_KOTLIN, "kotlin", "src/StockController.kt"),
    ],
)
def test_java_and_kotlin_agree(source, language, rel_path):
    """Parity, because `OI-13` is only useful if what it unlocked is symmetric."""
    decls = _decls(_nodes(source, language, rel_path))
    assert sorted(decls) == ["submit", "unrelated"]
    assert decls["submit"]["class"] == "StockController"


def test_declarations_are_observations_not_findings():
    """A declaration says what the code contains, never that anything is wrong."""
    nodes = _nodes(_JAVA, "java", "src/StockController.java")
    assert all(
        n.kind == "reference" for n in nodes if n.family == "method-decl"
    )
