"""The observation layer: record what was seen, classify it later.

Today a call that carries a sink-shaped name but fails the evidence gate is
**discarded**. That is what makes the boundary catalogue expensive: changing it
changes which nodes *exist*, so every change needs a fleet rescan, and a
misclassification like `OI-26` can only be fixed by re-extracting.

So the extractor emits a `call-site` observation for every call it examined,
carrying the inputs a classifier needs — receiver, whether that receiver reads as
a database, whether the call text names a library, whether the file shows SQL
evidence. Classification then happens downstream, where it is cheap to revise.

An observation asserts nothing about danger: `kind` is `reference`, not `sink`.
It says "this call exists and here is what we could tell about it", which is why
recording one for a call we currently reject costs nothing in precision.

See docs/plans/observe-then-classify.md §3.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file

_HTTP_ONLY = """
public class A {
    private final HttpClient httpClient;
    void f(Request r) { httpClient.execute(r); }
}
"""

_HTTP_PLUS_SQL = """
public class A {
    private final HttpClient httpClient;
    private final JdbcTemplate jdbcTemplate;
    void f(Request r) { httpClient.execute(r); }
    void g() { jdbcTemplate.query("SELECT ref FROM stock", mapper); }
}
"""

_NO_SINK_NAMES = """
public class A {
    void f() { logger.info("nothing interesting here"); helper.transform(value); }
}
"""


def _nodes(source: str, rel_path: str = "src/A.java"):
    return extract_from_file(
        repo_id="g/r", rel_path=rel_path, language="java", source=source
    )[0]


def _observations(source: str):
    return [n for n in _nodes(source) if n.family == "call-site"]


def test_a_call_rejected_by_the_evidence_gate_is_still_observed():
    """The rejection is a classification, and classifications must not lose data.

    `httpClient.execute(r)` in a file with no SQL anywhere is correctly *not* a
    sql sink. It is still a call that was examined, and the record should say so.
    """
    assert [n.family for n in _nodes(_HTTP_ONLY) if n.family == "sql"] == []
    obs = _observations(_HTTP_ONLY)
    assert [o.detail["symbol"] for o in obs] == ["execute"]


def test_an_observation_asserts_nothing_about_danger():
    """`kind` is `reference`: it records that a call exists, not that it is a sink."""
    for o in _observations(_HTTP_PLUS_SQL):
        assert o.kind == "reference"


def test_an_observation_carries_the_inputs_a_classifier_needs():
    """A downstream classifier must not have to re-read the source to decide."""
    obs = {o.detail["symbol"]: o.detail for o in _observations(_HTTP_PLUS_SQL)}

    http = obs["execute"]
    assert http["receiver"] == "httpClient"
    assert http["receiver_is_database"] is False
    assert http["library_hint"] is False
    # The file-scoped fact that OI-26 shows is too coarse to decide on alone —
    # recorded so a classifier can weigh it, not act on it blindly.
    assert http["file_sql_evidence"] is True

    sql = obs["query"]
    assert sql["receiver"] == "jdbcTemplate"
    assert sql["receiver_is_database"] is True


def test_calls_that_pass_the_gate_are_observed_too():
    """The observation set must be complete, or a classifier sees a biased sample."""
    symbols = sorted(o.detail["symbol"] for o in _observations(_HTTP_PLUS_SQL))
    assert symbols == ["execute", "query"]


def test_ordinary_calls_are_not_observed():
    """Volume is bounded by the sink-shaped name sets, not by every call in the file."""
    assert _observations(_NO_SINK_NAMES) == []


def test_observing_is_independent_of_classifying():
    """Every examined call is observed; only some are classified.

    `httpClient.execute` is observed and *not* classified — the gap between the
    two lists is the point of the layer. It was classified until `OI-26` was
    fixed, and fixing that changed only the classifier.
    """
    nodes = _nodes(_HTTP_PLUS_SQL)
    observed = sorted(n.detail["symbol"] for n in nodes if n.family == "call-site")
    classified = sorted(
        n.detail["symbol"] for n in nodes if n.family == "sql" and n.kind == "sink"
    )
    assert observed == ["execute", "query"]
    assert classified == ["query"]


@pytest.mark.parametrize("source", [_HTTP_ONLY, _HTTP_PLUS_SQL, _NO_SINK_NAMES])
def test_observations_never_outnumber_the_calls_examined(source):
    """One observation per examined call — no duplication per family."""
    obs = _observations(source)
    keys = [(o.file, o.line, o.detail["symbol"]) for o in obs]
    assert len(keys) == len(set(keys))
