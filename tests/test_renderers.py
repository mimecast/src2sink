"""Tests for the per-repo Markdown renderer (covers worker-path rendering)."""

from __future__ import annotations

from src2sink.renderers.markdown import auto_block, md_table, merge_with_manual, render_repo_md_v2
from src2sink.sanitize import UNTRUSTED_CONTENT_NOTICE
from src2sink.schema import FlowEdge, FlowNode, RepoSummaryV2


def _summary() -> RepoSummaryV2:
    nodes = [
        FlowNode("n1", "g/r", "A.java", 10, "java", "spring", "sink", "sql",
                 detail={"symbol": "executeQuery"}, confidence="high"),
        FlowNode("n2", "g/r", "A.java", 20, "java", None, "sink", "raw-code-payload",
                 detail={"endpoint_path": "/run", "sink_symbol": "query"}, confidence="high"),
    ]
    edges = [FlowEdge("n1", "n2", "intra-file", "same file")]
    return RepoSummaryV2(group="g", name="r", path="g/r", nodes=nodes, edges=edges,
                         primary_language="java", git_sha="abc")


def test_md_table_none_and_rows():
    assert "_(none detected)_" in md_table(["A"], [])
    t = md_table(["A", "B"], [["1", "2"]])
    assert "| A | B |" in t and "| 1 | 2 |" in t


def test_render_repo_md_v2_sections():
    md = render_repo_md_v2(_summary())
    assert "# g/r" in md
    assert UNTRUSTED_CONTENT_NOTICE.strip() in md
    assert "## Identity" in md
    assert "## Flow summary by family" in md
    assert "## Raw code payload endpoints" in md  # the raw-code-payload branch
    assert "executeQuery" in md


def test_merge_with_manual_replaces_blocks(tmp_path):
    generated = render_repo_md_v2(_summary())
    md_path = tmp_path / "r.md"
    # No existing file → returns generated unchanged.
    assert merge_with_manual(md_path, generated) == generated
    # With an existing file containing a manual section + an auto block, the auto
    # block is replaced and manual content preserved.
    md_path.write_text(
        "# g/r\n\n## Manual notes\nkeep me\n\n"
        + auto_block("identity", md_table(["Field", "Value"], [["Group", "OLD"]])),
        encoding="utf-8",
    )
    merged = merge_with_manual(md_path, generated)
    assert "keep me" in merged
    assert "OLD" not in merged  # stale auto block replaced
