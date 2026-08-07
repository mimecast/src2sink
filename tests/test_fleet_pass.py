"""OI-41: one streamed pass instead of fourteen full parses.

Aggregation parsed the whole metabase **fourteen times per run** — 2.2 GB each on
the observed fleet, ~31 GB of JSON decoded to read the same bytes repeatedly.
Measured A/B, the repetition is **67% of aggregation**, and aggregation is 78% of
the run.

Memoising the load would remove it and cost a *held* copy of the fleet where
re-parsing created and discarded — measured at +118 MB on a 29 MB metabase, which
at fleet scale is `OI-15`'s ceiling reached through the fix. So the collectors
reduce as they go and retain only their results.

The tests that matter are the two invariants, not the count: a collector must not
retain records, and the streamed and loading paths must produce identical
results. `tests/test_aggregate_output_golden.py` covers the rendered output
byte-for-byte on top of this.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

# One import style per module: three tests monkeypatch `fleet_pass` internals
# and need the module object, so the symbol import goes rather than mixing
# both. Flagged by CodeQL on earlier PRs for the same reason.
from src2sink.aggregators import fleet_pass as fp
from src2sink.aggregators.data_stores import StoreCollector, _collect_stores
from src2sink.aggregators.queues import QueueCollector, compute_queue_graph
from src2sink import graph_common as gc


def _record(group: str, name: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 2, "group": group, "name": name,
        "nodes": nodes, "edges": [], "dependencies_internal": [],
    }


_FLEET = [
    _record("app", "producer", [
        {"family": "queue-pub", "kind": "sink", "file": "P.java", "line": 1,
         "detail": {"topic": "stock-updates", "system": "kafka"}},
        {"family": "data-store", "kind": "store", "file": "P.java", "line": 9,
         "detail": {"vendor": "postgres", "url": "jdbc:postgresql://h/db"}},
    ]),
    _record("app", "consumer", [
        {"family": "queue-sub", "kind": "source", "file": "C.java", "line": 3,
         "detail": {"topic": "stock-updates", "system": "kafka"}},
        {"family": "sql", "kind": "sink", "file": "C.java", "line": 7,
         "detail": {"symbol": "query", "execution": True}},
    ]),
]


@pytest.fixture
def metabase(tmp_path):
    """A metabase on disk, so the streamed path reads what the loading path does."""
    for rec in _FLEET:
        d = tmp_path / "repos" / rec["group"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec['name']}.json").write_text(json.dumps(rec), encoding="utf-8")
    return tmp_path


# --- the invariants -----------------------------------------------------------


def test_streaming_and_loading_agree(metabase):
    """A faster reduction that reduces differently is not a fix."""
    queues, stores = QueueCollector(), StoreCollector()
    fp.run_fleet_pass(metabase, (queues, stores))

    records = gc.load_v2_repo_records(metabase)
    assert queues.result() == compute_queue_graph(records)
    assert stores.result() == _collect_stores(records)


def test_a_collector_does_not_retain_records(metabase):
    """The whole reason this is a stream rather than a memo.

    Retaining records is the memory cost `OI-41` exists to avoid — memoising was
    measured at +118 MB on a 29 MB metabase, and at fleet scale that is `OI-15`'s
    ceiling. A collector that stashed the record it was handed would reintroduce
    it silently, and nothing about its output would look wrong.
    """
    collectors = [QueueCollector(), StoreCollector()]
    fp.run_fleet_pass(metabase, collectors)

    for collector in collectors:
        for value in vars(collector).values():
            flat = json.dumps(value, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
            assert "schema_version" not in flat, (
                f"{type(collector).__name__} retained a record, not a reduction"
            )


def test_the_fleet_is_read_once_for_all_collectors(metabase, monkeypatch):
    """The count, which is the point but not the risk."""
    reads = [0]
    original = fp.iter_v2_repo_records

    def counted(*a, **kw):
        reads[0] += 1
        yield from original(*a, **kw)

    monkeypatch.setattr(fp, "iter_v2_repo_records", counted)
    fp.run_fleet_pass(metabase, (QueueCollector(), StoreCollector(), QueueCollector()))
    assert reads[0] == 1, "three collectors must cost one pass, not three"


# --- the mechanism's edges ----------------------------------------------------


def test_every_collector_sees_every_record(metabase):
    """A collector added later must not miss records consumed before it existed."""
    seen: list[list[str]] = [[], []]

    class Spy:
        def __init__(self, slot): self.slot = slot
        def consume(self, record): seen[self.slot].append(record["name"])
        def result(self): return seen[self.slot]

    fp.run_fleet_pass(metabase, (Spy(0), Spy(1)))
    assert seen[0] == seen[1] == ["consumer", "producer"]


def test_record_order_is_the_loading_order(metabase):
    """Several reductions sort only at the end, so order must not move."""
    order: list[str] = []

    class Spy:
        def consume(self, record): order.append(f"{record['group']}/{record['name']}")
        def result(self): return order

    fp.run_fleet_pass(metabase, (Spy(),))
    assert order == [f"{r['group']}/{r['name']}" for r in gc.load_v2_repo_records(metabase)]


def test_no_collectors_reads_nothing(metabase, monkeypatch):
    """An empty pass must not walk the fleet to do nothing with it."""
    def explode(*a, **kw):
        raise AssertionError("the fleet was read for zero collectors")

    monkeypatch.setattr(fp, "iter_v2_repo_records", explode)
    fp.run_fleet_pass(metabase, ())


def test_an_empty_metabase_is_not_an_error(tmp_path):
    """Aggregation runs before any record exists on a first build."""
    queues = QueueCollector()
    fp.run_fleet_pass(tmp_path, (queues,))
    assert queues.result().topics == ()


# --- the regression guard -----------------------------------------------------


def _count_fleet_work(tmp_path, monkeypatch) -> tuple[int, int]:
    """Return (full parses, service-edge builds) for one aggregation."""
    import src2sink.aggregators.service_call_collect as scc

    parses, edges = [0], [0]
    real_load, real_edges = gc.load_v2_repo_records, scc.collect_service_edges

    def counted_load(root, **kw):
        parses[0] += 1
        return real_load(root, **kw)

    def counted_edges(records, *a, **kw):
        edges[0] += 1
        return real_edges(records, *a, **kw)

    monkeypatch.setattr(gc, "load_v2_repo_records", counted_load)
    monkeypatch.setattr(scc, "collect_service_edges", counted_edges)
    for name in (
        "service_call_report", "queues", "data_stores", "index_v2", "pii_flow_v2",
        "payload_producers", "pii_cross_repo", "pii_lifecycle_report", "ropa",
        "auth_cards", "crypto_cards", "graphs", "service_calls", "phase3",
    ):
        mod = __import__(f"src2sink.aggregators.{name}", fromlist=["x"])
        if hasattr(mod, "load_v2_repo_records"):
            monkeypatch.setattr(mod, "load_v2_repo_records", counted_load)
        if hasattr(mod, "collect_service_edges"):
            monkeypatch.setattr(mod, "collect_service_edges", counted_edges)

    from src2sink.aggregators.graphs import aggregate_graphs_v2

    for rec in _FLEET:
        d = tmp_path / "repos" / rec["group"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec['name']}.json").write_text(json.dumps(rec), encoding="utf-8")
    aggregate_graphs_v2(tmp_path, sorted(tmp_path.glob("repos/*/*.json")), phase3=True)
    return parses[0], edges[0]


def test_aggregation_does_not_reparse_the_fleet(tmp_path, monkeypatch):
    """A ratchet on the cost `OI-41` is about.

    Fourteen full parses when this was filed. Three remain, and they are three
    genuinely separate phases with a data dependency: the shared load feeding the
    service-call report and producer index, the PII lifecycle pass that *produces*
    the touchpoints, and phase 3 which consumes them. Collapsing those would mean
    holding records across the whole aggregation, which is the memory cost this
    exists to avoid.

    The bound is what matters, not the exact number — a new aggregator that loads
    for itself pushes it up and fails here.
    """
    parses, edges = _count_fleet_work(tmp_path, monkeypatch)
    assert parses <= 3, (
        f"aggregation parsed the metabase {parses} times; it was 14 when OI-41 "
        "was filed and 3 after. A new aggregator should take the shared records "
        "or a collector on the streamed pass, not load for itself."
    )
    assert edges <= 1, (
        f"collect_service_edges ran {edges} times. It is fleet-wide and "
        "target-independent — the derivation OI-14 identified as dominating "
        "cost — so it is built once and passed on."
    )
