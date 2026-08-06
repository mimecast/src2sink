"""SQL classification reads observations, not source.

Step two of the observation layer. Step one recorded what was seen; this makes
the classification *consume* that record and nothing else.

The property that matters is the one asserted first: the classifier can be
handed observations with no source in sight and still produce the same `sql`
nodes. Once that holds, correcting a classification — `OI-26`, or any catalogue
change in `OI-20` — is a change to one function over stored data, so it can be
re-run without re-extracting the fleet.

It also forces the observation record to be *sufficient*. Anything the classifier
still needed from the source would be a field the observation was missing, and
the test would fail rather than the gap going unnoticed.

See docs/plans/observe-then-classify.md §3.
"""

from __future__ import annotations

from src2sink.extractors.file_context import FileExtractionContext
from src2sink.extractors.ts_extractors import classify_sql_from_observations
from src2sink.extractors.unified import extract_from_file

_LAYERED = """
public class Dao {
    private final JdbcTemplate jdbcTemplate;
    private final HttpClient httpClient;
    void read(String f) { jdbcTemplate.query("SELECT ref FROM stock WHERE x = " + f, m); }
    void send(Request r) { httpClient.execute(r); }
}
"""


def _nodes(source: str):
    return extract_from_file(
        repo_id="g/r", rel_path="src/Dao.java", language="java", source=source
    )[0]


def test_the_classifier_needs_no_source():
    """Hand it observations and an empty source; it must still classify.

    This is the whole point of the step. If the classifier reaches for the source
    it has not moved downstream — it has just been relocated.
    """
    observed = [n for n in _nodes(_LAYERED) if n.family == "call-site"]
    assert observed, "fixture must produce observations"

    ctx = FileExtractionContext(
        repo_id="g/r", rel_path="src/Dao.java", language="java", source=""
    )
    ctx.nodes.extend(observed)
    classify_sql_from_observations(ctx)

    sql = [n for n in ctx.nodes if n.family == "sql" and n.kind == "sink"]
    # Both, because the move is behaviour-preserving and `OI-26` is part of the
    # behaviour: file-scoped SQL evidence admits `httpClient.execute` too. The
    # point of this step is that the defect now lives in one function over
    # stored data, so fixing it costs a re-aggregation rather than a rescan.
    assert sorted(n.detail["symbol"] for n in sql) == ["execute", "query"]


def test_an_observation_carries_the_parameterisation_posture():
    """Posture needs the whole source to compute, so it is observed, not classified.

    `sql_parameterisation` falls back to scanning the file when the call text has
    no literal of its own. That is an observation about the call — it says what
    the statement looks like, not whether it is dangerous — so it belongs on the
    record rather than being recomputed by a classifier that has no source.
    """
    observed = {
        n.detail["symbol"]: n.detail for n in _nodes(_LAYERED) if n.family == "call-site"
    }
    # Concatenated into the statement, with no bound parameters.
    assert observed["query"]["parameterised"] == "raw"


def test_classification_is_unchanged_by_the_move():
    """The move must be behaviour-preserving; only the input changes."""
    sql = [n for n in _nodes(_LAYERED) if n.family == "sql" and n.kind == "sink"]
    assert sorted((n.detail["symbol"], n.detail["receiver"]) for n in sql) == [
        ("execute", "httpClient"),   # OI-26, preserved by the move
        ("query", "jdbcTemplate"),
    ]
    node = next(n for n in sql if n.detail["symbol"] == "query")
    assert node.detail["execution"] is True
    assert node.detail["parameterised"] == "raw"
    assert node.confidence == "high"


def test_execution_sinks_still_reach_the_intra_file_linker():
    """`raw-code-payload` depends on execution sinks being registered.

    The classifier populates `sql_execution_sinks`, which
    `link_raw_code_payload_endpoints` consumes. Moving classification without
    preserving that would silently delete the raw-code-payload family.
    """
    source = """
    @RestController
    public class Api {
        private final JdbcTemplate jdbcTemplate;
        @PostMapping("/run")
        public String run(@RequestBody String sql) {
            return jdbcTemplate.query(sql, mapper);
        }
    }
    """
    families = {n.family for n in _nodes(source)}
    assert "raw-code-payload" in families


def test_a_rejected_observation_leaves_no_sql_node():
    """The classifier declining is recorded only by the absence of a sql node."""
    source = """
    public class A {
        private final HttpClient httpClient;
        void f(Request r) { httpClient.execute(r); }
    }
    """
    nodes = _nodes(source)
    assert [n.family for n in nodes if n.family == "sql"] == []
    assert [n.detail["symbol"] for n in nodes if n.family == "call-site"] == ["execute"]
