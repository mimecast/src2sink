"""Orchestrate v2 cross-repo graph generation."""

from __future__ import annotations

from pathlib import Path

from ..graph_common import (
    iter_v2_repo_records,
    load_v2_repo_records,
    v2_record_paths,
)
from ..index_store import build_index
from .data_stores import write_data_store_graph
from .index_v2 import write_index_v2
from .openapi_edges import write_openapi_artifacts
from .payload_producers import build_producer_indices, write_payload_producer_index
from .phase3 import aggregate_phase3_v2
from .pii_flow_v2 import write_pii_flow_v2
from .queues import write_queue_graph
from .service_calls import collect_service_edges, write_service_call_graph
from .traces_index import write_traces_index


def write_fleet_index(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    repos_root: Path | None = None,
) -> Path:
    """Persist the four things a trace consults, keyed by target repo (`OI-15`).

    Built here because aggregation already walks every repo, so the index is one
    more pass over data that is being read anyway rather than a second crawl.

    The two fleet-wide derivations are computed the same way `trace` computed
    them, from the same functions — the index is a cache of this code's output,
    not a reimplementation of it, which is what keeps the two from drifting.
    """
    paths = v2_record_paths(metabase_root, json_paths=repo_jsons)
    records = load_v2_repo_records(metabase_root, json_paths=paths)
    call_edges, _unmatched = collect_service_edges(records)
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
    write_service_call_graph(metabase_root, repo_jsons, repos_root=repos_root)
    if repos_root:
        write_openapi_artifacts(metabase_root, repos_root, repo_jsons)
    write_queue_graph(metabase_root, repo_jsons)
    write_data_store_graph(metabase_root, repo_jsons)
    write_payload_producer_index(metabase_root, repo_jsons, repos_root=repos_root)
    write_index_v2(metabase_root, repo_jsons)
    write_pii_flow_v2(metabase_root, repo_jsons)
    write_traces_index(metabase_root)
    if phase3:
        aggregate_phase3_v2(metabase_root, repo_jsons)
    if fleet_index:
        write_fleet_index(metabase_root, repo_jsons, repos_root=repos_root)
