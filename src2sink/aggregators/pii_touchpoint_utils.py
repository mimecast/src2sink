"""Shared helpers for PII lifecycle touchpoint collection."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..models.pii_lifecycle import PiiTouchpoint, normalize_field_key

PROXIMITY_LINES = 80

DELETE_RX = re.compile(
    r"\b(delete|remove|erase|purge|forget)\w*\b",
    re.IGNORECASE,
)


def lines_by_file(data: dict[str, Any]) -> dict[str, list[tuple[int, str, dict[str, Any]]]]:
    """file -> [(line, family, node), ...] sorted by line."""
    by_file: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    for node in data.get("nodes", []):
        by_file[node.get("file", "")].append(
            (int(node.get("line") or 0), node.get("family", ""), node),
        )
    for entries in by_file.values():
        entries.sort(key=lambda x: x[0])
    return by_file


def nearby(
    by_file: dict[str, list[tuple[int, str, dict[str, Any]]]],
    file: str,
    line: int,
    families: set[str],
    *,
    window: int = PROXIMITY_LINES,
) -> bool:
    """Return True if any node of the given families sits within window lines."""
    for ln, fam, _ in by_file.get(file, []):
        if fam in families and abs(ln - line) <= window:
            return True
    return False


def touch_from_node(
    rid: str,
    stage: str,
    node: dict[str, Any],
    *,
    evidence: str = "",
) -> PiiTouchpoint | None:
    """Build a PiiTouchpoint from a flow node, or None if it carries no usable field."""
    detail = node.get("detail") or {}
    field_name = detail.get("field_name") or ""
    if not field_name and stage not in ("encrypt", "delete"):
        if node.get("family") not in ("pii-log", "pii-storage"):
            return None
    field_key = normalize_field_key(field_name or "unknown")
    pii_c = node.get("pii_classification") or "unknown"
    return PiiTouchpoint(
        repo=rid,
        stage=stage,
        family=node.get("family", ""),
        field_key=field_key,
        field_name=field_name or field_key,
        pii_classification=pii_c,
        data_class=node.get("data_class"),
        file=node.get("file", ""),
        line=int(node.get("line") or 0),
        confidence=node.get("confidence", "medium"),
        evidence=evidence,
    )


def proximity_touch(
    base: PiiTouchpoint,
    *,
    stage: str,
    family: str,
    evidence: str,
) -> PiiTouchpoint:
    """Clone a collect-stage touch with a proximity-derived stage."""
    return PiiTouchpoint(
        repo=base.repo,
        stage=stage,
        family=family,
        field_key=base.field_key,
        field_name=base.field_name,
        pii_classification=base.pii_classification,
        data_class=base.data_class,
        file=base.file,
        line=base.line,
        confidence="low",
        evidence=evidence,
    )
