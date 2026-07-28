"""Normalize extractor output for stable JSON snapshots (Phase 4)."""

from __future__ import annotations

import json
from typing import Any

from src2sink.schema import FlowEdge, FlowNode


def _trim_detail(detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in sorted(detail.items()):
        if key == "raw" and isinstance(value, str):
            out[key] = value[:80]
        else:
            out[key] = value
    return out


def normalize_extraction(
    nodes: list[FlowNode],
    edges: list[FlowEdge],
) -> dict[str, Any]:
    """Stable dict for snapshot comparison (omits volatile node ids)."""
    norm_nodes = sorted(
        [
            {
                "family": n.family,
                "kind": n.kind,
                "file": n.file,
                "line": n.line,
                "language": n.language,
                "framework": n.framework,
                "pii_classification": n.pii_classification,
                "data_class": n.data_class,
                "confidence": n.confidence,
                "detail": _trim_detail(dict(n.detail)),
            }
            for n in nodes
        ],
        key=lambda x: (x["family"], x["kind"], x["line"], x["file"]),
    )
    return {
        "node_count": len(norm_nodes),
        "edge_count": len(edges),
        "families": sorted({n["family"] for n in norm_nodes}),
        "nodes": norm_nodes,
    }


def load_snapshot(path: Any) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_snapshot(path: Any, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
