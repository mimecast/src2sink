"""Derivation runs over observations, so it can be re-run without the source.

The last two steps made classification *separable* — one function, provably
needing no source. This makes it actually separate: extraction records
observations, and a derive pass turns them into findings.

The property under test is the one that pays for the whole exercise: take a
record produced by a scan, throw the source away, re-derive, and get the same
findings back. When that holds, correcting a classification — `OI-26`, or any
entry in the `OI-20` catalogue — is a pass over records already on disk. No
repositories checked out, no parsing, no `DETECTION_VERSION` bump, no fleet
rescan.

See docs/plans/observe-then-classify.md §3.
"""

from __future__ import annotations

from src2sink.derive import DERIVED_FAMILIES, derive_from_observations
from src2sink.extractors.unified import extract_from_file

_INJECTABLE_ENDPOINT = """
@RestController
public class QueryApi {
    private final JdbcTemplate jdbcTemplate;
    @PostMapping("/run")
    public String run(@RequestBody String sql) {
        return jdbcTemplate.query("SELECT ref FROM stock WHERE x = " + sql, mapper);
    }
}
"""

_HTTP_PROXY = """
@RestController
public class Proxy {
    private static final String AUDIT_SQL = "SELECT ref FROM stock";
    private final HttpClient httpClient;
    @PostMapping("/forward")
    public String forward(@RequestBody String sql) { return httpClient.execute(sql); }
}
"""


def _scan(source: str, rel_path: str = "src/A.java"):
    return extract_from_file(
        repo_id="g/r", rel_path=rel_path, language="java", source=source
    )


def _families(nodes):
    return sorted(n.family for n in nodes)


def test_derivation_reproduces_the_findings_from_observations_alone():
    """The payoff: re-derive from a record, with no source anywhere.

    Strip every derived family out of a scanned record, hand back only what was
    *observed*, and derivation must rebuild exactly what the scan produced.
    """
    nodes, edges = _scan(_INJECTABLE_ENDPOINT)
    observed = [n for n in nodes if n.family not in DERIVED_FAMILIES]
    assert len(observed) < len(nodes), "fixture must produce something derived"

    rederived_nodes, rederived_edges = derive_from_observations(observed)
    assert _families(observed + rederived_nodes) == _families(nodes)
    assert len(rederived_edges) == len(edges)


def test_derivation_is_idempotent():
    """Re-running must not duplicate findings — a derive pass will be run again."""
    nodes, _ = _scan(_INJECTABLE_ENDPOINT)
    observed = [n for n in nodes if n.family not in DERIVED_FAMILIES]

    once, _ = derive_from_observations(observed)
    twice, _ = derive_from_observations(observed)
    assert _families(once) == _families(twice)


def test_a_corrected_classification_needs_no_rescan():
    """`OI-26` re-checked through the derive path, which is where it now lives."""
    nodes, _ = _scan(_HTTP_PROXY)
    observed = [n for n in nodes if n.family not in DERIVED_FAMILIES]

    derived, _ = derive_from_observations(observed)
    assert [n.family for n in derived if n.family == "sql"] == []
    assert [n.family for n in derived if n.family == "raw-code-payload"] == []


def test_the_raw_sql_field_marker_is_observed_not_transient():
    """The linker's third input was a bare list of line numbers, held in memory.

    It has to be on the record, or derivation cannot see it and the
    raw-code-payload family silently disappears when the pass moves.
    """
    nodes, _ = _scan(_INJECTABLE_ENDPOINT)
    markers = [n for n in nodes if n.family == "sql-field-marker"]
    assert markers, "the marker must survive as an observation"
    assert all(n.kind == "reference" for n in markers)


def test_extraction_no_longer_classifies():
    """Extraction emits observations; the derive pass emits findings.

    Asserted directly, because the two could drift back together without any
    output changing — and then the re-derive property would quietly be lost.
    """
    from src2sink.extractors.file_context import FileExtractionContext
    from src2sink.extractors.ts_extractors import extract_tree_sitter_calls

    ctx = FileExtractionContext(
        repo_id="g/r", rel_path="src/A.java", language="java",
        source=_INJECTABLE_ENDPOINT,
    )
    extract_tree_sitter_calls(ctx)
    assert [n.family for n in ctx.nodes if n.family in DERIVED_FAMILIES] == []
    assert [n.family for n in ctx.nodes if n.family == "call-site"]


def test_scanning_still_produces_findings_end_to_end():
    """The build must still emit findings — derivation runs as part of a scan."""
    nodes, _ = _scan(_INJECTABLE_ENDPOINT)
    families = set(_families(nodes))
    assert "sql" in families
    assert "raw-code-payload" in families
