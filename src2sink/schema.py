"""Metabase v2 schema — source / propagator / sink / store flow graph."""

from __future__ import annotations

import dataclasses
from typing import Any

SCHEMA_VERSION = 2

# What *produced* a record, as distinct from what a record is shaped like.
#
# Bump this whenever a change could alter extraction output — a new family, a
# changed guard, an adjusted pattern or vocabulary. It is part of the incremental
# scan's cache key, so bumping it is what makes a detection fix reach repositories
# that have not themselves changed. Before it existed, the skip was keyed on the
# repo sha alone and every fix stopped at the repos that happened to have
# committed since (OI-16).
#
# Deliberately not the package version: a docs-only release would invalidate the
# whole fleet for nothing. Deliberately not a hash of the extractor sources
# either, which would fire on a comment. `scripts/detection_version_check.py`
# holds the honesty line — it fails the build when a detection input changes
# without a bump here.
#
# 14 is a deliberate *false* positive of that gate, paid rather than argued away.
# The phase-0 timing change edited `build_metabase_v2.py` — a fingerprinted file
# — but only its imports, `main`, `_run_aggregate_only`, a new `_aggregate_all`
# helper and the manifest writer; no record-producing function was touched, so
# nothing a record says can differ. Precedent existed for re-freezing without a
# bump (`ccdb358`, `OI-31`), and the call was made the other way: one rescan
# costs ~14 minutes once, and a gate with a growing list of judgement calls
# stops being a gate. Records built by 13 and 14 are byte-identical.
#
# 15 is the opposite: a real detection change. The `OI-36` sweep (4.0 phase
# 1) makes a repo record say when a source file would not parse and when a
# manifest could not be read, so `notes` genuinely differs. If 14's rescan
# has not been run yet, 15 subsumes it — one rescan covers both.
#
# 16 is `OI-43` step 2: Go type declarations were discarded outright, so every
# Go repo gains `type-decl` nodes and its `method-decl` nodes gain the type
# they hang off. 17 is `OI-43` step 4, which adds one note per repo per
# language whose extraction is limited. 3.1.0 shipped detection 13, so 14
# through 17 all land in the same unreleased window — one rescan covers the
# lot.
#
# 18 is `OI-43` step 3: declared field types and supertypes for TypeScript,
# Go and Python, plus TypeScript interfaces, which were never recorded as
# types at all. Every `type-decl` in those languages gains content it never
# carried, and TypeScript and Python calls resolve at a stronger tier.
DETECTION_VERSION = 18



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
    detection_version: int = DETECTION_VERSION
    # Set by the builder from src2sink.derive, which is where the constant
    # lives: a version must sit inside the fingerprint scope it governs, or
    # bumping it trips the *other* gate and forces the rescan it exists to
    # avoid.
    derivation_version: int = 0
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
