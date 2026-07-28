"""Construct FlowNode / FlowEdge with stable ids."""

from __future__ import annotations

import hashlib
from typing import Any

from ..schema import FlowEdge, FlowNode


def _slug(*parts: str) -> str:
    """Return a short stable 12-char hex digest of the joined ``parts``."""
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def make_node(
    *,
    repo: str,
    file: str,
    line: int,
    language: str,
    kind: str,
    family: str,
    detail: dict[str, Any] | None = None,
    framework: str | None = None,
    pii_classification: str | None = None,
    data_class: str | None = None,
    confidence: str = "medium",
) -> FlowNode:
    """Construct a FlowNode with a deterministic id derived from its location and kind."""
    detail = detail or {}
    node_id = f"{repo}:{file}:{line}:{family}:{kind}:{_slug(family, kind, str(line), detail.get('symbol', ''))}"
    return FlowNode(
        id=node_id,
        repo=repo,
        file=file,
        line=line,
        language=language,
        framework=framework,
        kind=kind,
        family=family,
        detail=detail,
        pii_classification=pii_classification,
        data_class=data_class,
        confidence=confidence,
    )


def make_edge(
    src: FlowNode,
    dst: FlowNode,
    *,
    kind: str,
    evidence: str,
    confidence: str = "medium",
) -> FlowEdge:
    """Construct a FlowEdge linking source node ``src`` to destination node ``dst``."""
    return FlowEdge(
        src_id=src.id,
        dst_id=dst.id,
        kind=kind,
        evidence=evidence,
        confidence=confidence,
    )
