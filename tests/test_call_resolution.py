"""OI-17, step three: a call resolved to the method it invokes.

Steps 1 and 2 recorded the facts — enclosing method on every node, declarations
with parameters and spans, field types and supertypes. None of it connected
anything. This step joins them, and it is where the tool stops seeing two ends of
a path and starts seeing the path.

Two things had to happen. Observation was **widened** to every call, because
`stockService.process(...)` is the middle of every layered chain and was recorded
nowhere — the filter only kept names that looked like sinks. And resolution binds
a call to a declaration in three tiers, each recorded on the edge, because they
are not interchangeable evidence.

The interface case is the one that decides whether this is useful. A
constructor-injected interface field is the standard Spring shape, so a resolver
stopping at the declared type would report a dead end for most of the fleet — and
a confident dead end reads as a clean result, which is worse than no answer.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file
from src2sink.resolve import build_symbol_table, call_edges, resolve_calls

# The canonical layered shape the issue is written against: an endpoint, an
# interface-typed collaborator, an implementation, and a sink at the bottom.
_LAYERED = """
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

interface StockService {
    Result process(String filter);
}

class StockServiceImpl implements StockService {
    private final StockDao stockDao;

    public Result process(String filter) {
        return stockDao.findMatching(filter);
    }
}

class StockDao {
    private JdbcTemplate jdbcTemplate;

    Result findMatching(String filter) {
        return jdbcTemplate.query("SELECT ref FROM stock WHERE " + filter);
    }

    Result countAll() {
        return jdbcTemplate.query("SELECT count(*) FROM stock");
    }
}
"""

_KOTLIN_LAYERED = """
class StockController(private val stockService: StockService) {

    @PostMapping("/stock")
    fun submit(@RequestBody req: StockRequest): StockResult =
        stockService.process(req.filter)

    fun unrelated() {
        log.info("nothing to do")
    }
}

interface StockService {
    fun process(filter: String): Result
}

class StockServiceImpl(private val stockDao: StockDao) : StockService {
    fun process(filter: String): Result = stockDao.findMatching(filter)
}

class StockDao {
    private val jdbcTemplate: JdbcTemplate = tpl
    fun findMatching(filter: String): Result = jdbcTemplate.query("SELECT ref FROM stock WHERE " + filter)
}
"""


def _observations(source: str, language: str = "java", rel_path: str = "src/Stock.java"):
    return extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source
    )[0]


def _hops(source: str, **kw) -> set[tuple[str, str, str]]:
    """Resolved calls as (caller method, callee class, callee method)."""
    return {
        (
            r.call.detail.get("enclosing_method") or "",
            r.target.detail.get("class") or "",
            r.target.detail.get("method") or "",
        )
        for r in resolve_calls(_observations(source, **kw))
    }


def test_the_middle_of_a_path_is_observed_at_all():
    """Everything below depends on this, and before step 3 it was false."""
    symbols = {
        n.detail["symbol"] for n in _observations(_LAYERED) if n.family == "call-site"
    }
    assert "process" in symbols, "the hop the whole chain runs through"
    assert "findMatching" in symbols


def test_a_field_typed_receiver_resolves_at_t1():
    """`private final StockDao stockDao` says what the call binds to."""
    resolved = {
        (r.tier, r.target.detail["class"], r.target.detail["method"])
        for r in resolve_calls(_observations(_LAYERED))
    }
    assert ("T1", "StockDao", "findMatching") in resolved


def test_an_interface_typed_receiver_expands_to_its_implementation():
    """The Spring shape. Stopping at the interface would report a dead end.

    `StockService.process` has no body, so a resolver that bound to it would
    find nothing beyond and call the path finished — confidently, and wrongly.
    """
    resolved = [r for r in resolve_calls(_observations(_LAYERED)) if r.tier == "T2"]
    assert [(r.target.detail["class"], r.target.detail["method"]) for r in resolved] == [
        ("StockServiceImpl", "process"),
    ]
    assert resolved[0].confidence == "medium"
    assert resolved[0].ambiguous is False


def test_the_full_chain_is_resolvable_end_to_end():
    """The issue's stated exit criterion, as one assertion."""
    hops = _hops(_LAYERED)
    assert ("submit", "StockServiceImpl", "process") in hops
    assert ("process", "StockDao", "findMatching") in hops


def test_an_unrelated_method_is_not_on_the_chain():
    """`unrelated` calls `log.info` and must not appear as a hop to anything ours."""
    hops = _hops(_LAYERED)
    assert not [h for h in hops if h[0] == "unrelated"], (
        "log.info resolves outside the repo and must not become an edge"
    )


def test_two_implementations_are_ambiguous_and_never_confident():
    """Which one runs is a runtime fact. Reporting one would be a guess."""
    source = """
    class Caller {
        private final Store store;
        void go() { store.put("k"); }
    }
    interface Store { void put(String k); }
    class RedisStore implements Store { public void put(String k) { } }
    class MemoryStore implements Store { public void put(String k) { } }
    """
    resolved = [r for r in resolve_calls(_observations(source)) if r.tier == "T2"]
    assert sorted(r.target.detail["class"] for r in resolved) == [
        "MemoryStore", "RedisStore",
    ]
    assert all(r.ambiguous for r in resolved)
    assert all(r.confidence == "low" for r in resolved), (
        "an ambiguous resolution must not read as a confident one"
    )


def test_a_unique_name_resolves_at_t3():
    """No type for the receiver, but only one declaration can be meant."""
    source = """
    class Caller {
        void go(Helper h) { h.doTheOneThing(); }
    }
    class Helper { void doTheOneThing() { } }
    """
    resolved = resolve_calls(_observations(source))
    assert [(r.tier, r.target.detail["method"]) for r in resolved] == [
        ("T3", "doTheOneThing"),
    ]
    assert resolved[0].confidence == "low"


def test_an_ambiguous_name_is_dropped_not_guessed():
    """Declared twice with nothing to choose between them is not evidence."""
    source = """
    class Caller {
        void go(Thing t) { t.handle(); }
    }
    class A { void handle() { } }
    class B { void handle() { } }
    """
    assert [r for r in resolve_calls(_observations(source)) if r.tier == "T3"] == []


def test_a_self_reference_prefix_is_the_same_receiver():
    """`this.dao.find()` is the same call as `dao.find()`."""
    source = """
    class Caller {
        private final Dao dao;
        void go() { this.dao.find(); }
    }
    class Dao { void find() { } }
    """
    resolved = resolve_calls(_observations(source))
    assert [(r.tier, r.target.detail["class"]) for r in resolved] == [("T1", "Dao")]


def test_a_call_does_not_resolve_to_the_declaration_it_sits_in():
    """A traversal would loop on an edge from a node to itself and never leave."""
    source = """
    class Caller {
        void loopy() { loopy(); }
    }
    """
    assert [r for r in resolve_calls(_observations(source)) if r.call.id == r.target.id] == []


def test_recursion_between_two_methods_still_resolves():
    """A cycle is a real call graph, and must resolve rather than be suppressed.

    Terminating on it is the traversal's job (step 4), not the resolver's — a
    resolver that dropped cyclic edges would hide real paths through them.
    """
    source = """
    class Caller {
        void a() { b(); }
        void b() { a(); }
    }
    """
    hops = _hops(source)
    assert ("a", "Caller", "b") in hops
    assert ("b", "Caller", "a") in hops


@pytest.mark.parametrize(
    ("source", "language", "rel_path"),
    [
        (_LAYERED, "java", "src/Stock.java"),
        (_KOTLIN_LAYERED, "kotlin", "src/Stock.kt"),
    ],
)
def test_java_and_kotlin_resolve_the_same_chain(source, language, rel_path):
    """Half the JVM fleet is Kotlin, and reachability that covers one language
    silently is worse than none — its absence looks like a clean result."""
    hops = _hops(source, language=language, rel_path=rel_path)
    assert ("submit", "StockServiceImpl", "process") in hops
    assert ("process", "StockDao", "findMatching") in hops


def test_the_tier_is_recorded_on_every_edge():
    """A reader has to be able to tell a declared type from a name coincidence."""
    edges = call_edges(_observations(_LAYERED))
    assert edges, "the layered fixture must produce edges"
    for edge in edges:
        assert edge.kind == "intra-repo"
        assert edge.evidence.startswith(("[T1]", "[T2]", "[T3]"))


def test_these_are_the_first_intra_repo_edges():
    """`FlowEdge` has advertised the kind since the schema was written.

    Nothing emitted one: cross-repo links lived in a separate `CallEdge` type and
    within-repo links were never made at all.
    """
    assert {e.kind for e in call_edges(_observations(_LAYERED))} == {"intra-repo"}


def test_resolution_needs_no_source_text():
    """It is a derivation, so a rule change must cost a re-derive not a rescan.

    Every fact resolution uses has to be *in the observations*. Asserted by
    rebuilding them from serialised records — a round trip through JSON keeps
    only what was written down, so anything the resolver secretly needed from
    the extraction context is gone by the time it runs.
    """
    import dataclasses
    import json

    from src2sink.schema import FlowNode

    observations = _observations(_LAYERED)
    round_tripped = [
        FlowNode(**json.loads(json.dumps(dataclasses.asdict(n))))
        for n in observations
    ]
    assert resolve_calls(round_tripped), "resolution must survive a record round trip"
    assert {(r.tier, r.target.detail["method"]) for r in resolve_calls(round_tripped)} == \
           {(r.tier, r.target.detail["method"]) for r in resolve_calls(observations)}


def test_an_argument_is_recorded_for_the_tainted_path_search():
    """Step 4 needs what the call passes, and it rides this rescan.

    Recording arguments changes a record, so it costs a `DETECTION_VERSION` bump.
    Step 3's widening already forces one — doing both now is one rescan instead
    of two.
    """
    calls = {
        n.detail["symbol"]: n.detail
        for n in _observations(_LAYERED) if n.family == "call-site"
    }
    assert calls["process"]["arguments"] == ["req.getFilter()"]
    assert calls["findMatching"]["arguments"] == ["filter"]


def test_the_symbol_table_records_interfaces_separately_from_classes():
    """T2 exists only because an interface method has no body to reach."""
    table = build_symbol_table(_observations(_LAYERED))
    assert table.is_interface["StockService"] is True
    assert table.is_interface["StockServiceImpl"] is False
    assert table.implementations["StockService"] == ["StockServiceImpl"]


def test_kotlin_arguments_are_recorded_too():
    """The step 3 argument test covered Java only, and Kotlin recorded nothing.

    Kotlin names no argument field — a `call_expression` holds a
    `value_arguments` child — so every Kotlin call carried an empty argument
    list. No Kotlin hop could carry taint, and step 4 found no Kotlin paths at
    all: a clean-looking result across half the JVM fleet.
    """
    calls = {
        n.detail["symbol"]: n.detail
        for n in _observations(_KOTLIN_LAYERED, language="kotlin", rel_path="src/Stock.kt")
        if n.family == "call-site"
    }
    assert calls["process"]["arguments"] == ["req.filter"]
    assert calls["findMatching"]["arguments"] == ["filter"]
