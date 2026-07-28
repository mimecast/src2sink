"""Phase 0 exit gate: tree-sitter parses minimal source for each supported language."""

from __future__ import annotations

from pathlib import Path

import pytest

from src2sink.extractors.base import parse_source, supported_languages
from src2sink.schema import SCHEMA_VERSION, FlowEdge, FlowNode, RepoSummaryV2

# Minimal valid snippets per grammar (no repo checkout required).
_SNIPPETS: dict[str, str] = {
    "java": "public class Smoke { void m() {} }",
    "python": "def smoke() -> None:\n    pass\n",
    "javascript": "function smoke() { return 1; }",
    "typescript": "const smoke: number = 1;",
    "tsx": "export const Smoke = () => <span>ok</span>;",
    "go": "package smoke\nfunc Smoke() int { return 0 }\n",
    "kotlin": "fun smoke(): Unit = Unit",
}

_ROOT_TYPES: dict[str, set[str]] = {
    "java": {"program"},
    "python": {"module"},
    "javascript": {"program"},
    "typescript": {"program"},
    "tsx": {"program"},
    "go": {"source_file"},
    "kotlin": {"source_file"},
}


@pytest.mark.parametrize("language_id", sorted(_SNIPPETS))
def test_tree_sitter_parses_minimal_snippet(language_id: str) -> None:
    assert language_id in supported_languages()
    tree = parse_source(language_id, _SNIPPETS[language_id])
    root_type = tree.root_node.type
    assert root_type in _ROOT_TYPES[language_id], (
        f"{language_id}: unexpected root {root_type!r}"
    )
    assert tree.root_node.has_error is False


def test_schema_v2_dataclasses_instantiate() -> None:
    node = FlowNode(
        id="repo:file:1:sql",
        repo="group/name",
        file="src/Foo.java",
        line=1,
        language="java",
        framework="spring",
        kind="sink",
        family="sql",
        pii_classification=None,
        data_class="raw-sql-payload",
    )
    edge = FlowEdge(
        src_id="a",
        dst_id="b",
        kind="intra-file",
        evidence="variable flows to executeQuery",
    )
    summary = RepoSummaryV2(
        group="group",
        name="name",
        path="repos/group/name",
        nodes=[node],
        edges=[edge],
    )
    assert summary.schema_version == SCHEMA_VERSION
    assert summary.nodes[0].data_class == "raw-sql-payload"


def test_optional_parse_real_java_from_repos() -> None:
    """When repos/ is present, parse one Java file end-to-end."""
    root = Path(__file__).resolve().parents[2]
    candidates = list(root.glob("repos/**/*.java"))
    if not candidates:
        pytest.skip("repos/ not populated")
    path = candidates[0]
    tree = parse_source("java", path.read_bytes())
    assert tree.root_node.child_count >= 1
