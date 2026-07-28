"""Collect fleet-wide PII lifecycle touchpoints from v2 flow nodes."""

from __future__ import annotations

from typing import Any

from ..graph_common import iter_nodes, repo_id
from ..models.pii_lifecycle import PiiTouchpoint

from .pii_touchpoint_utils import (
    PROXIMITY_LINES,
    DELETE_RX,
    lines_by_file,
    nearby,
    proximity_touch,
    touch_from_node,
)


def _maybe_add_proximity(
    touches: list[PiiTouchpoint],
    seen_intra: set[tuple[str, str, str, int]],
    base: PiiTouchpoint,
    *,
    stage: str,
    family: str,
    evidence: str,
    file: str,
    line: int,
    by_file: dict[str, list[tuple[int, str, dict[str, Any]]]],
    probe_families: set[str],
) -> None:
    """Add a deduplicated proximity touchpoint if a probe family is near the line."""
    if not nearby(by_file, file, line, probe_families, window=PROXIMITY_LINES):
        return
    sig = (base.repo, base.field_key, stage, line)
    if sig in seen_intra:
        return
    seen_intra.add(sig)
    touches.append(proximity_touch(base, stage=stage, family=family, evidence=evidence))


def _collect_pii_field_node(
    rid: str,
    node: dict[str, Any],
    by_file: dict[str, list[tuple[int, str, dict[str, Any]]]],
    touches: list[PiiTouchpoint],
    seen_intra: set[tuple[str, str, str, int]],
) -> None:
    """Emit a collect touch for a pii-field node plus nearby process/transmit/encrypt touches."""
    line = int(node.get("line") or 0)
    file = node.get("file", "")
    t = touch_from_node(rid, "collect", node)
    if t:
        touches.append(t)
    if not t:
        return
    _maybe_add_proximity(
        touches, seen_intra, t,
        stage="process", family="sql+near-pii-field",
        evidence="sql sink within file window",
        file=file, line=line, by_file=by_file, probe_families={"sql"},
    )
    _maybe_add_proximity(
        touches, seen_intra, t,
        stage="transmit", family="http-out+near-pii-field",
        evidence="http-out within file window",
        file=file, line=line, by_file=by_file, probe_families={"http-out"},
    )
    _maybe_add_proximity(
        touches, seen_intra, t,
        stage="encrypt", family="crypto+near-pii-field",
        evidence="crypto op within file window",
        file=file, line=line, by_file=by_file,
        probe_families={"crypto-algorithm", "crypto-config"},
    )


def _collect_delete_hints(
    rid: str,
    data: dict[str, Any],
    by_file: dict[str, list[tuple[int, str, dict[str, Any]]]],
    touches: list[PiiTouchpoint],
) -> None:
    """Emit delete-stage touches where a delete/remove op sits near a PII field."""
    for node in iter_nodes(data):
        if node.get("family") not in ("pii-log", "pii-storage", "pii-field"):
            continue
        detail = node.get("detail") or {}
        field_name = detail.get("field_name")
        if not field_name:
            continue
        file = node.get("file", "")
        line = int(node.get("line") or 0)
        for ln, _, n in by_file.get(file, []):
            if abs(ln - line) > 30:
                continue
            raw = (n.get("detail") or {}).get("raw", "")
            if DELETE_RX.search(raw):
                t = touch_from_node(
                    rid,
                    "delete",
                    node,
                    evidence="delete/remove near field",
                )
                if t:
                    touches.append(t)
                break


def collect_pii_touchpoints(records: list[dict[str, Any]]) -> list[PiiTouchpoint]:
    """Collect all PII lifecycle touchpoints across the given repo records."""
    touches: list[PiiTouchpoint] = []
    for data in records:
        rid = repo_id(data)
        by_file = lines_by_file(data)
        seen_intra: set[tuple[str, str, str, int]] = set()

        for node in iter_nodes(data):
            family = node.get("family", "")
            if family == "pii-field":
                _collect_pii_field_node(rid, node, by_file, touches, seen_intra)
            elif family == "pii-storage":
                t = touch_from_node(rid, "store", node)
                if t:
                    touches.append(t)
            elif family == "pii-log":
                t = touch_from_node(rid, "log", node)
                if t:
                    touches.append(t)

        _collect_delete_hints(rid, data, by_file, touches)

    return touches
