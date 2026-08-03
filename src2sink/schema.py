"""Metabase v2 schema — source / propagator / sink / store flow graph."""

from __future__ import annotations

import dataclasses
from typing import Any

SCHEMA_VERSION = 2


@dataclasses.dataclass
class FlowNode:
    """One point of interest in a repo's data flow — a source, sink or propagator.

    The unit every extractor emits and every aggregator consumes. ``family`` names
    what kind of thing it is (``http-in``, ``sql``, ``sql-payload-out``, …) and
    ``detail`` carries the per-family payload; see SCHEMA.md, which documents both
    and must be updated alongside this class.
    """

    id: str
    repo: str
    file: str
    line: int
    language: str
    framework: str | None
    kind: str  # source | propagator | sink | store | reference
    family: str
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)
    pii_classification: str | None = None
    data_class: str | None = None
    confidence: str = "medium"


@dataclasses.dataclass
class FlowEdge:
    """A directed link between two :class:`FlowNode` ids.

    ``kind`` records how far the link reaches — within a file, within a repo, or
    across repos — because a cross-repo edge is the claim the tool exists to make
    and is held to a higher evidence bar than the other two.
    """

    src_id: str
    dst_id: str
    kind: str  # intra-file | intra-repo | cross-repo
    evidence: str
    confidence: str = "medium"


@dataclasses.dataclass
class RepoSummaryV2:
    """Per-repo metabase record (v2). Phase 1+ populates nodes and edges."""

    schema_version: int = SCHEMA_VERSION
    group: str = ""
    name: str = ""
    path: str = ""
    git_sha: str | None = None
    analysed_at: str = ""
    primary_language: str = "unknown"
    language_breakdown: dict[str, int] = dataclasses.field(default_factory=dict)
    build_systems: list[str] = dataclasses.field(default_factory=list)
    frameworks: list[str] = dataclasses.field(default_factory=list)
    dependencies_internal: list[dict[str, str]] = dataclasses.field(
        default_factory=list
    )
    dependencies_external_count: int = 0
    nodes: list[FlowNode] = dataclasses.field(default_factory=list)
    edges: list[FlowEdge] = dataclasses.field(default_factory=list)
    file_counts: dict[str, int] = dataclasses.field(default_factory=dict)
    notes: list[str] = dataclasses.field(default_factory=list)
