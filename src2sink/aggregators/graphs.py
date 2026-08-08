"""Orchestrate v2 cross-repo graph generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..graph_common import (
    iter_v2_repo_records,
    load_v2_repo_records,
    v2_record_paths,
)
from ..index_store import build_index
from .. import run_timing
from .data_stores import StoreCollector, write_data_store_graph
from .index_v2 import _row_from_record, write_index_v2
from .openapi_edges import write_openapi_artifacts
from .payload_producers import build_producer_indices, write_payload_producer_index
from .phase3 import aggregate_phase3_v2
from .pii_flow_v2 import PiiFlowCollector, write_pii_flow_v2
from .fleet_pass import MapCollector, run_fleet_pass
from .queues import QueueCollector, write_queue_graph
from .service_calls import collect_service_edges, write_service_call_graph
from .traces_index import write_traces_index


def write_fleet_index(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    repos_root: Path | None = None,
    producer_indices: list[Any] | None = None,
    call_edges: list[Any] | None = None,
) -> Path:
    """Persist the four things a trace consults, keyed by target repo (`OI-15`).

    Built here because aggregation already walks every repo, so the index is one
    more pass over data that is being read anyway rather than a second crawl.

    The two fleet-wide derivations are computed the same way `trace` computed
    them, from the same functions — the index is a cache of this code's output,
    not a reimplementation of it, which is what keeps the two from drifting.

    ``producer_indices`` is taken from the caller when aggregation has already
    built it. Rebuilding it here meant the fleet's source tree was scanned a
    second time, which doubled the slowest step of the whole run (`OI-30`).
    """
    paths = v2_record_paths(metabase_root, json_paths=repo_jsons)
    records: list[dict[str, Any]] | None = None
    if call_edges is None:
        # Fleet-wide and target-independent, and three consumers each recomputed
        # it — the derivation `OI-14` identified as dominating cost (`OI-41`).
        records = load_v2_repo_records(metabase_root, json_paths=paths)
        call_edges, _unmatched = collect_service_edges(records)
    if producer_indices is None:
        producer_indices = build_producer_indices(
            metabase_root, repos_root=repos_root, json_paths=paths,
        )
    del records  # the streamed pass below must not be served from this list

    return build_index(
        metabase_root,
        paths,
        call_edges=call_edges,
        producer_indices=producer_indices,
        records=iter_v2_repo_records(metabase_root, json_paths=paths),
    )


def aggregate_graphs_v2(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    repos_root: Path | None = None,
    phase3: bool = True,
    fleet_index: bool = True,
) -> None:
    """Run all v2 cross-repo graph writers (service, queue, data-store, PII, phase 3)."""
    # One load serves the report and the index, and the edges it computes are
    # handed on rather than recomputed (`OI-41`).
    with run_timing.phase("shared-load"):
        shared = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    with run_timing.phase("service-call-graph"):
        call_edges, _unmatched = write_service_call_graph(
            metabase_root, repo_jsons, repos_root=repos_root, records=shared,
        )
    if repos_root:
        with run_timing.phase("openapi"):
            write_openapi_artifacts(metabase_root, repos_root, repo_jsons)
    # One streamed pass drives every converted aggregator, instead of each
    # parsing the whole metabase for itself (`OI-41`). Collectors retain their
    # reductions, never the records — so this buys the time without the resident
    # copy that memoising would cost (`OI-15`).
    queues, stores = QueueCollector(), StoreCollector()
    index_rows = MapCollector(_row_from_record)
    pii_flow = PiiFlowCollector()
    with run_timing.phase("streamed-pass"):
        run_fleet_pass(
            metabase_root, (queues, stores, index_rows, pii_flow), json_paths=repo_jsons,
        )
    with run_timing.phase("queue-and-store-graphs"):
        write_queue_graph(metabase_root, repo_jsons, graph=queues.result())
        write_data_store_graph(metabase_root, repo_jsons, collected=stores.result())
    with run_timing.phase("payload-producers"):
        producer_indices = write_payload_producer_index(
            metabase_root, repo_jsons, repos_root=repos_root, records=shared,
        )
    del shared   # released before the streamed pass below
    with run_timing.phase("index-and-pii"):
        write_index_v2(metabase_root, repo_jsons, rows=index_rows.result())
        write_pii_flow_v2(metabase_root, repo_jsons, counts=pii_flow.result())
        write_traces_index(metabase_root)
    if phase3:
        with run_timing.phase("phase3"):
            aggregate_phase3_v2(metabase_root, repo_jsons, edges=call_edges)
    if fleet_index:
        with run_timing.phase("fleet-index"):
            write_fleet_index(
                metabase_root, repo_jsons, repos_root=repos_root,
                producer_indices=producer_indices, call_edges=call_edges,
            )
