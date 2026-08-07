"""Phase 3 aggregators: PII lifecycle, ROPA, auth/crypto conventions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .auth_cards import _collect_repo_auth, write_auth_models_catalog
from .crypto_cards import _collect_repo_crypto, write_crypto_agility_catalog
from ..graph_common import load_v2_repo_records
from .service_calls import collect_service_edges
from .pii_cross_repo import write_pii_cross_repo_graph
from ..models.ropa import ROPA_SHOWCASE_FIELDS
from .pii_lifecycle import write_pii_lifecycle_graph
from .ropa import write_ropa_view


def aggregate_phase3_v2(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    edges: list[Any] | None = None,
) -> None:
    """Run the phase 3 aggregators: PII lifecycle/cross-repo, ROPA, auth, and crypto."""
    touches = write_pii_lifecycle_graph(metabase_root, repo_jsons)
    # Parsed once for every field. The input does not vary with `field_key`, so
    # loading per field parsed the whole metabase three times over (`OI-41`).
    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    # Fleet-wide and field-independent, so built once for all three fields —
    # and taken from the caller when aggregation has already built it (`OI-41`).
    if edges is None:
        edges, _unmatched = collect_service_edges(records)
    for field_key in sorted(ROPA_SHOWCASE_FIELDS):
        write_pii_cross_repo_graph(
            metabase_root,
            touches,
            repo_jsons,
            field_key=field_key,
            records=records,
            edges=edges,
        )
    write_ropa_view(metabase_root, repo_jsons, touches=touches, records=records)
    write_auth_models_catalog(
        metabase_root, repo_jsons, cards=[_collect_repo_auth(d) for d in records],
    )
    write_crypto_agility_catalog(
        metabase_root, repo_jsons, cards=[_collect_repo_crypto(d) for d in records],
    )
