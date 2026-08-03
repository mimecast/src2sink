"""Collect v2 flow nodes into catalogue buckets before markdown/jsonl export."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaintCatalogueBuckets:
    """Grouped node records keyed by taint family."""

    sql_sources: list[dict[str, Any]] = field(default_factory=list)
    sql_sinks: list[dict[str, Any]] = field(default_factory=list)
    file_sinks: list[dict[str, Any]] = field(default_factory=list)
    http_sinks: list[dict[str, Any]] = field(default_factory=list)
    pii_sources: list[dict[str, Any]] = field(default_factory=list)
    data_class_fields: list[dict[str, Any]] = field(default_factory=list)
    pii_sinks: list[dict[str, Any]] = field(default_factory=list)
    crypto_ops: list[dict[str, Any]] = field(default_factory=list)
    raw_payload: list[dict[str, Any]] = field(default_factory=list)
    # Outbound requests carrying SQL — the dual of raw_payload, which is
    # inbound (OI-9).
    sql_payload_out: list[dict[str, Any]] = field(default_factory=list)
    config_stores: list[dict[str, Any]] = field(default_factory=list)
    config_security: list[dict[str, Any]] = field(default_factory=list)
    crypto_config: list[dict[str, Any]] = field(default_factory=list)


def collect_taint_buckets(repo_jsons: list[Path]) -> TaintCatalogueBuckets:
    """Walk per-repo v2 JSON files and partition nodes into export buckets."""
    buckets = TaintCatalogueBuckets()
    for jp in repo_jsons:
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        repo_id = f"{data['group']}/{data['name']}"
        for node in data.get("nodes", []):
            _route_node(buckets, node, repo_id)
    return buckets


# Families that map 1:1 to a bucket regardless of kind.
_SIMPLE_FAMILY_BUCKET = {
    "file": "file_sinks",
    "http-out": "http_sinks",
    "pii-field": "pii_sources",
    "data-class-field": "data_class_fields",
    "crypto-algorithm": "crypto_ops",
    "raw-code-payload": "raw_payload",
    "sql-payload-out": "sql_payload_out",
    "config-security": "config_security",
    "crypto-config": "crypto_config",
}


def _route_node(buckets: TaintCatalogueBuckets, node: dict[str, Any], repo_id: str) -> None:
    """Append a repo-tagged copy of the node to the bucket for its family/kind."""
    rec = {**node, "repo": repo_id}
    family = node.get("family", "")
    kind = node.get("kind", "")

    if family == "sql":
        if kind == "source":
            buckets.sql_sources.append(rec)
        elif kind == "sink" and node.get("detail", {}).get("execution", True):
            buckets.sql_sinks.append(rec)
    elif family in ("pii-log", "pii-storage"):
        buckets.pii_sinks.append(rec)
    elif family == "data-store" and kind == "store":
        buckets.config_stores.append(rec)
    else:
        attr = _SIMPLE_FAMILY_BUCKET.get(family)
        if attr:
            getattr(buckets, attr).append(rec)
