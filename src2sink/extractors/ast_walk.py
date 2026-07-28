"""Tree-sitter AST walking helpers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


def node_text(source: bytes, node: Node) -> str:
    """Decode the source bytes spanned by ``node`` to text (errors replaced)."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def line_number(source: bytes, node: Node) -> int:
    """Return the 1-based line number where ``node`` starts."""
    return node.start_point[0] + 1


def walk(node: Node) -> Iterator[Node]:
    """Yield ``node`` and every descendant via an iterative pre-order traversal."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in reversed(current.children):
            stack.append(child)


def call_name_java_kotlin(source: bytes, node: Node) -> str | None:
    """Return the invoked method name for a Java/Kotlin call node, or None."""
    if node.type != "method_invocation":
        return None
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return node_text(source, name_node)
    for child in reversed(node.children):
        if child.type in ("identifier", "field_identifier"):
            return node_text(source, child)
    return None


def call_name_python(source: bytes, node: Node) -> str | None:
    """Return the called function/attribute name for a Python call node, or None."""
    if node.type != "call":
        return None
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return node_text(source, fn)
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        if attr is not None:
            return node_text(source, attr)
    return None


def call_name_js_go(source: bytes, node: Node, language: str) -> str | None:
    """Return the called function/member name for a JS/TS/Go call node, or None."""
    if language in ("javascript", "typescript", "tsx"):
        if node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            return node_text(source, fn)
        if fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            if prop is not None:
                return node_text(source, prop)
    if language == "go":
        if node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "selector_expression":
            field = fn.child_by_field_name("field")
            if field is not None:
                return node_text(source, field)
        if fn.type == "identifier":
            return node_text(source, fn)
    return None


def extract_call_name(source: bytes, node: Node, language: str) -> str | None:
    """Dispatch to the language-specific call-name extractor for ``node``."""
    if language in ("java", "kotlin"):
        return call_name_java_kotlin(source, node)
    if language == "python":
        return call_name_python(source, node)
    return call_name_js_go(source, node, language)


CALL_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"method_invocation"}),
    "kotlin": frozenset({"call_expression", "navigation_expression"}),
    "python": frozenset({"call"}),
    "javascript": frozenset({"call_expression"}),
    "typescript": frozenset({"call_expression"}),
    "tsx": frozenset({"call_expression"}),
    "go": frozenset({"call_expression"}),
}


def iter_calls(
    source: bytes,
    root: Node,
    language: str,
    *,
    types: frozenset[str] | None = None,
) -> Iterator[tuple[Node, str]]:
    """Yield (call node, call name) for every call-expression node in the subtree.

    Args:
        source: Raw source bytes (untrusted scanned code) backing the tree.
        root: Subtree root to walk.
        language: Language id used to pick call node types and name extraction.
        types: Optional override of node types treated as calls.
    """
    types = types or CALL_NODE_TYPES.get(language, frozenset())
    for node in walk(root):
        if node.type not in types:
            continue
        name = extract_call_name(source, node, language)
        if name:
            yield node, name
