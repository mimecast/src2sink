"""Unified per-file extractor: tree-sitter call sites + regex heuristics."""

from __future__ import annotations

from ..constants import TEST_PATH_RX
from ..derive import derive_from_observations
from .file_context import FileExtractionContext
from .regex_extractors import (
    extract_api_client_imports,
    extract_crypto_and_auth,
    extract_file_sinks,
    extract_http_inbound,
    extract_http_outbound,
    extract_data_class_field_declarations,
    extract_path_constants,
    extract_pii_field_declarations,
    extract_pii_sinks,
    extract_queue_io,
    extract_raw_sql_field_markers,
    extract_sql_string_sources,
)
from .ts_extractors import (
    extract_tree_sitter_calls,
    link_sql_payload_out,
)
from ..schema import FlowEdge, FlowNode


def _is_test_path(rel_path: str) -> bool:
    """True when ``rel_path`` matches a test-file path pattern (skipped from extraction)."""
    return bool(TEST_PATH_RX.search(rel_path.replace("\\", "/")))


def extract_from_file(
    *,
    repo_id: str,
    rel_path: str,
    language: str,
    source: str,
) -> tuple[list[FlowNode], list[FlowEdge]]:
    """Run all extraction passes for one source file (regex, then tree-sitter)."""
    if _is_test_path(rel_path):
        return [], []

    ctx = FileExtractionContext(
        repo_id=repo_id,
        rel_path=rel_path,
        language=language,
        source=source,
    )

    # Regex passes — order only matters for raw-code-payload prerequisites.
    extract_http_inbound(ctx)
    extract_sql_string_sources(ctx)
    extract_file_sinks(ctx)
    extract_api_client_imports(ctx)
    extract_http_outbound(ctx)
    extract_path_constants(ctx)
    extract_queue_io(ctx)
    extract_crypto_and_auth(ctx)
    extract_pii_field_declarations(ctx)
    extract_data_class_field_declarations(ctx)
    extract_raw_sql_field_markers(ctx)
    extract_pii_sinks(ctx)

    # AST pass. Records observations only — no classification happens here.
    extract_tree_sitter_calls(ctx)
    link_sql_payload_out(ctx)

    # Findings are derived from the observations, not produced alongside them.
    # A scan runs this inline for convenience; the same call re-runs over a
    # stored record with no source in sight, which is the point (OI-26, OI-20).
    derived_nodes, derived_edges = derive_from_observations(ctx.nodes)
    return [*ctx.nodes, *derived_nodes], [*ctx.edges, *derived_edges]
