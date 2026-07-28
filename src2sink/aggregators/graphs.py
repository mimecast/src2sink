"""Orchestrate v2 cross-repo graph generation."""

from __future__ import annotations

from pathlib import Path

from .data_stores import write_data_store_graph
from .index_v2 import write_index_v2
from .openapi_edges import write_openapi_artifacts
from .payload_producers import write_payload_producer_index
from .phase3 import aggregate_phase3_v2
from .pii_flow_v2 import write_pii_flow_v2
from .queues import write_queue_graph
from .service_calls import write_service_call_graph
from .traces_index import write_traces_index


def aggregate_graphs_v2(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    repos_root: Path | None = None,
    phase3: bool = True,
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
