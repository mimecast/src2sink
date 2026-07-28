"""Data types for service-call graph edges."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CallEdge:
    """A cross-repo service-call edge from a source repo to a target route."""

    source_repo: str
    target_repo: str
    target_path: str
    confidence: str
    evidence: str
    refs: list[str] = field(default_factory=list)
