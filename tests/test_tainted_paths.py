"""OI-17, step four: does a value from this door reach that sink?

Step 3 built the call graph. This walks it, and it is the claim the tool is named
for — not "this service has an endpoint and also a sink somewhere", but *a value
from this door arrives at this sink, by these hops, and here is the weakest link*.

Reachability alone would report every endpoint as reaching every sink its service
can touch: true, and useless. What makes a path a finding is that a value
travels. So the entry point's parameters are tainted, an argument mentioning a
tainted name taints the callee's corresponding parameter, and a hop carrying
nothing is not walked.

**Three of these tests contradict the issue's own step-4 wording**, and follow
`docs/plans/observe-then-classify.md` instead, which was written later and with
measurements:

* depth is **unbounded**, not a BFS to some limit — capping at three hops finds
  25% of what depth eight finds (§5);
* path confidence is the **minimum** hop, never a product — the issue's "four
  `medium` hops must not report as `medium`" presumes multiplication, which §6
  retracts because it destroys exactly the deep paths that hold the value;
* there is **no confidence floor** — §7 retracts it outright, because for an
  indicator a floor turns cheap false positives into expensive invisible false
  negatives.
"""

from __future__ import annotations

import pytest

from src2sink.derive import derive_from_observations, is_derived
from src2sink.extractors.unified import extract_from_file
from src2sink.paths import find_tainted_paths

_LAYERED = """
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
@RestController
class StockController(private val stockService: StockService) {

    @PostMapping("/stock")
    fun submit(@RequestBody req: StockRequest): StockResult =
        stockService.process(req.filter)
}

interface StockService {
    fun process(filter: String): Result
}

class StockServiceImpl(private val stockDao: StockDao) : StockService {
    fun process(filter: String): Result = stockDao.findMatching(filter)
}

class StockDao {
    private val jdbcTemplate: JdbcTemplate = tpl
    fun findMatching(filter: String): Result =
        jdbcTemplate.query("SELECT ref FROM stock WHERE " + filter)
}
"""


def _paths(source: str, language: str = "java", rel_path: str = "src/Stock.java"):
    nodes = extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source
    )[0]
    observations = [n for n in nodes if not is_derived(n)]
    derived, _edges = derive_from_observations(observations)
    found, _truncated = find_tainted_paths(observations, derived)
    return found


def _findings(source: str, **kw):
    nodes = extract_from_file(
        repo_id="g/r", rel_path=kw.get("rel_path", "src/Stock.java"),
        language=kw.get("language", "java"), source=source,
    )[0]
    observations = [n for n in nodes if not is_derived(n)]
    derived, _edges = derive_from_observations(observations)
    return [n for n in derived if n.family == "tainted-path"]


def test_the_canonical_chain_yields_exactly_one_path():
    """The issue's stated exit criterion, in full."""
    paths = _paths(_LAYERED)
    assert len(paths) == 1

    path = paths[0]
    assert path.entry.detail["channel"] == "/stock"
    assert path.sink.detail["symbol"] == "query"
    assert path.tainted_at_sink == ("filter",)
    assert [(h.to_class, h.to_method) for h in path.hops] == [
        ("StockServiceImpl", "process"),
        ("StockDao", "findMatching"),
    ]


def test_the_static_query_is_never_on_a_path():
    """`countAll` executes SQL and takes nothing from anyone.

    It is a sink, and a reachability-only answer would report it. It carries no
    tainted value, so it is not a finding.
    """
    for path in _paths(_LAYERED):
        assert path.sink.line != 35, "the static query must not appear"
        assert "count(*)" not in str(path.sink.detail.get("raw", ""))


def test_an_unrelated_method_is_on_no_path():
    """`unrelated` is reachable code that no tainted value passes through."""
    for path in _paths(_LAYERED):
        assert "unrelated" not in [h.from_method for h in path.hops]
        assert "unrelated" not in [h.to_method for h in path.hops]


def test_a_decoy_is_pruned_rather_than_ranked_lower():
    """The difference between a finding and a list of everything.

    `safe` is called from the endpoint, and it reaches a SQL sink. Nothing
    tainted is passed to it, so the hop is not walked at all — it must be absent,
    not present with a low score.
    """
    source = """
    @RestController
    public class Api {
        private final Svc svc;

        @PostMapping("/go")
        public String go(@RequestBody String body) {
            svc.risky(body);
            svc.safe("constant");
            return "ok";
        }
    }

    class Svc {
        private JdbcTemplate jdbcTemplate;

        void risky(String x) { jdbcTemplate.query("SELECT a FROM t WHERE " + x); }
        void safe(String y) { jdbcTemplate.query("SELECT b FROM t WHERE " + y); }
    }
    """
    reached = {p.sink.line for p in _paths(source)}
    risky_lines = {
        h.line for p in _paths(source) for h in p.hops if h.to_method == "risky"
    }
    assert risky_lines, "the tainted hop must be walked"
    assert not [p for p in _paths(source) if any(h.to_method == "safe" for h in p.hops)], (
        "a hop carrying no tainted value must be pruned, not ranked lower"
    )
    assert len(reached) == 1


def test_a_substring_of_a_tainted_name_is_not_tainted():
    """`filterChain` contains `filter` and is a different variable.

    Substring matching would carry taint into code that never received it, which
    for an exclusion claim is the cheap error and for a finding is a fabrication.
    """
    source = """
    @RestController
    public class Api {
        private final Svc svc;
        @PostMapping("/go")
        public String go(@RequestBody String filter) {
            svc.run(filterChain);
            return "ok";
        }
    }
    class Svc {
        private JdbcTemplate jdbcTemplate;
        void run(String z) { jdbcTemplate.query("SELECT a FROM t WHERE " + z); }
    }
    """
    assert _paths(source) == []


def test_depth_is_not_capped_at_three():
    """`A -> B -> C -> D -> sink` is the common case, not the exception.

    Measured on a 2,000-service fleet, capping at three hops finds 25% of what
    depth eight finds. A BFS to some limit would silently be a wrong answer for
    most of the fleet.
    """
    source = """
    @RestController
    public class Api {
        private final B b;
        @PostMapping("/go")
        public String go(@RequestBody String p0) { b.two(p0); return "ok"; }
    }
    class B { private final C c; void two(String p1) { c.three(p1); } }
    class C { private final D d; void three(String p2) { d.four(p2); } }
    class D { private final E e; void four(String p3) { e.five(p3); } }
    class E {
        private JdbcTemplate jdbcTemplate;
        void five(String p4) { jdbcTemplate.query("SELECT a FROM t WHERE " + p4); }
    }
    """
    paths = _paths(source)
    assert len(paths) == 1
    assert paths[0].length == 4, "a four-hop chain must be found in full"


def test_path_confidence_is_the_minimum_hop_not_a_product():
    """Contradicts the issue, follows the amendment that replaced it.

    The issue asks that four `medium` hops not report as `medium`, which presumes
    multiplication — and multiplying takes eight `medium` hops to 0.058, burying
    exactly the deep paths that hold most of the value. Hops are not independent
    coin flips: eight individually resolved calls with declared receiver types are
    not less trustworthy than two fuzzy string matches.

    So the minimum is the answer, and length is reported *beside* it rather than
    folded into it.
    """
    path = _paths(_LAYERED)[0]
    assert [h.confidence for h in path.hops] == ["medium", "high"]
    assert path.confidence == "medium", "the weakest hop, not a product"
    assert path.length == 2, "length is recorded separately, not folded in"


def test_the_weakest_link_is_named():
    """A reader can act on "the B->C binding"; nobody can act on 0.058."""
    path = _paths(_LAYERED)[0]
    weakest = path.weakest_link
    assert weakest is not None
    assert weakest.confidence == "medium"
    assert weakest.tier == "T2"
    assert "StockServiceImpl" in weakest.describe()
    assert "src/Stock.java:" in weakest.describe()


def test_a_low_confidence_path_is_still_emitted():
    """There is no confidence floor, and that is deliberate (§7).

    For an indicator, a floor converts cheap false positives into expensive,
    invisible false negatives. Emit broadly, rank honestly, never suppress on
    confidence alone.
    """
    source = """
    @RestController
    public class Api {
        @PostMapping("/go")
        public String go(@RequestBody String body) { helper(body); return "ok"; }
        void helper(String v) { jdbcTemplate.query("SELECT a FROM t WHERE " + v); }
    }
    """
    paths = _paths(source)
    assert paths, "a weakly-resolved path must still be reported"


def test_every_hop_cites_a_location_a_reader_can_check():
    """The path is the proof, so each hop has to be checkable against source."""
    for hop in _paths(_LAYERED)[0].hops:
        assert hop.file == "src/Stock.java"
        assert hop.line > 0
        assert hop.tier in ("T1", "T2", "T3")
        assert hop.argument, "the argument that carried the value must be named"


def test_a_cycle_terminates():
    """A call graph may contain one, and the search must not loop on it."""
    source = """
    @RestController
    public class Api {
        private final Svc svc;
        @PostMapping("/go")
        public String go(@RequestBody String body) { svc.a(body); return "ok"; }
    }
    class Svc {
        private JdbcTemplate jdbcTemplate;
        void a(String x) { b(x); }
        void b(String y) { a(y); jdbcTemplate.query("SELECT q FROM t WHERE " + y); }
    }
    """
    paths = _paths(source)
    assert len(paths) == 1, "the cycle must be traversed once, not forever"


def test_a_scheduled_job_with_no_parameters_starts_no_path():
    """Nothing enters by that door that a parameter names.

    `OI-21` already records that a cron job is not externally triggered; here it
    simply has nothing to taint.
    """
    source = """
    @Component
    public class Job {
        private JdbcTemplate jdbcTemplate;
        @Scheduled(fixedRate = 60000)
        public void sweep() { jdbcTemplate.query("SELECT a FROM t"); }
    }
    """
    assert _paths(source) == []


@pytest.mark.parametrize(
    ("source", "language", "rel_path"),
    [
        (_LAYERED, "java", "src/Stock.java"),
        (_KOTLIN_LAYERED, "kotlin", "src/Stock.kt"),
    ],
)
def test_java_and_kotlin_find_the_same_path(source, language, rel_path):
    """Half the JVM fleet is Kotlin, and a language being invisible reads as clean."""
    paths = _paths(source, language=language, rel_path=rel_path)
    assert len(paths) == 1
    assert [(h.to_class, h.to_method) for h in paths[0].hops] == [
        ("StockServiceImpl", "process"),
        ("StockDao", "findMatching"),
    ]


def test_the_path_is_emitted_as_a_derived_finding():
    """It must reach the record, or nothing downstream can read it."""
    findings = _findings(_LAYERED)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.family == "tainted-path"
    assert finding.kind == "finding"
    assert finding.confidence == "medium"
    assert finding.detail["hops"] == 2
    assert finding.detail["entry_channel"] == "/stock"
    assert finding.detail["weakest_link"]
    assert len(finding.detail["path"]) == 2


def test_paths_are_derived_and_need_no_source():
    """A rule change here must cost a re-derive, not a fleet rescan.

    Enforced by rebuilding the observations from serialised records: a round trip
    through JSON keeps only what was written down.
    """
    import dataclasses
    import json

    from src2sink.schema import FlowNode

    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/Stock.java", language="java", source=_LAYERED,
    )[0]
    observations = [
        FlowNode(**json.loads(json.dumps(dataclasses.asdict(n))))
        for n in nodes if not is_derived(n)
    ]
    derived, _edges = derive_from_observations(observations)
    assert [n for n in derived if n.family == "tainted-path"], (
        "path search must survive a record round trip"
    )


def test_the_same_sink_reached_twice_is_one_finding():
    """Two routes to one sink is one thing to look at, not two."""
    source = """
    @RestController
    public class Api {
        private final Svc svc;
        @PostMapping("/go")
        public String go(@RequestBody String body) {
            svc.viaOne(body);
            svc.viaTwo(body);
            return "ok";
        }
    }
    class Svc {
        private JdbcTemplate jdbcTemplate;
        void viaOne(String x) { sink(x); }
        void viaTwo(String y) { sink(y); }
        void sink(String z) { jdbcTemplate.query("SELECT a FROM t WHERE " + z); }
    }
    """
    assert len(_paths(source)) == 1
