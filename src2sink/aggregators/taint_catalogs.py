"""Cross-repo taint catalogues from v2 flow nodes."""

from __future__ import annotations

from pathlib import Path

from .taint_buckets import collect_taint_buckets
from .taint_writers import (
    MAX_MD_ROWS,
    MAX_PII_MD_BYTES,
    write_config_catalogues,
    write_crypto_and_payload_catalogues,
    write_file_and_http_catalogues,
    write_pii_catalogues,
    write_sql_catalogues,
)

__all__ = [
    "MAX_MD_ROWS",
    "MAX_PII_MD_BYTES",
    "aggregate_taint_catalogs_v2",
]


def aggregate_taint_catalogs_v2(metabase_root: Path, repo_jsons: list[Path]) -> None:
    """Collect nodes from v2 JSONs and write all `metabase/taint/*` catalogues."""
    taint_dir = metabase_root / "taint"
    taint_dir.mkdir(parents=True, exist_ok=True)

    buckets = collect_taint_buckets(repo_jsons)
    write_sql_catalogues(taint_dir, buckets)
    write_file_and_http_catalogues(taint_dir, buckets)
    write_pii_catalogues(taint_dir, buckets)
    write_crypto_and_payload_catalogues(taint_dir, buckets)
    write_config_catalogues(taint_dir, buckets)
