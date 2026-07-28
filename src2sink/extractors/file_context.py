"""Per-file extraction state passed between regex and tree-sitter passes."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import FlowEdge, FlowNode


@dataclass
class FileExtractionContext:
    """Mutable accumulator while scanning one source file."""

    repo_id: str
    rel_path: str
    language: str
    source: str
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    # Cross-pass links for raw-code-payload (endpoint + sql field + execution sink).
    http_sources: list[FlowNode] = field(default_factory=list)
    sql_execution_sinks: list[FlowNode] = field(default_factory=list)
    raw_sql_field_lines: list[int] = field(default_factory=list)

    def line_number(self, pos: int) -> int:
        """Return the 1-based line number of byte offset ``pos`` in the source."""
        return self.source.count("\n", 0, pos) + 1

    def line_text_at(self, pos: int) -> str:
        """Full source line containing byte offset ``pos``."""
        start = self.source.rfind("\n", 0, pos) + 1
        end = self.source.find("\n", pos)
        if end == -1:
            end = len(self.source)
        return self.source[start:end]
