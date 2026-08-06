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


def call_name_java(source: bytes, node: Node) -> str | None:
    """Return the invoked method name for a Java call node, or None."""
    if node.type != "method_invocation":
        return None
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return node_text(source, name_node)
    for child in reversed(node.children):
        if child.type in ("identifier", "field_identifier"):
            return node_text(source, child)
    return None


def call_name_kotlin(source: bytes, node: Node) -> str | None:
    """Return the invoked function name for a Kotlin ``call_expression``, or None.

    Kotlin's grammar has no ``method_invocation``. A call is a
    ``call_expression`` whose first child is either a bare ``identifier`` —
    ``execute(sql)`` — or a ``navigation_expression`` whose final ``identifier``
    is the name: ``jdbcTemplate.query`` and, nested, ``this.dao.findMatching``.

    Routing Kotlin to the Java walker meant every call returned None while
    ``CALL_NODE_TYPES`` still listed the language, so ``iter_calls`` found the
    nodes, asked for their names, and yielded nothing (OI-13).
    """
    if node.type != "call_expression":
        return None
    target = node.children[0] if node.children else None
    if target is None:
        return None
    if target.type == "identifier":
        return node_text(source, target)
    if target.type == "navigation_expression":
        for child in reversed(target.children):
            if child.type in ("identifier", "simple_identifier"):
                return node_text(source, child)
    return None


def call_receiver_kotlin(source: bytes, node: Node) -> str | None:
    """Return the receiver a Kotlin call is made on, or None for a bare call.

    ``jdbcTemplate.query(...)`` -> ``"jdbcTemplate"``; ``this.dao.find(...)`` ->
    ``"this.dao"``; ``execute(...)`` -> None. The receiver is what separates
    ``jdbcTemplate.execute`` from ``httpClient.execute`` (`OI-7`), and without it
    a Kotlin classification has only file-scoped evidence to go on — which
    `OI-26` showed is too coarse to decide a call.
    """
    if node.type != "call_expression" or not node.children:
        return None
    target = node.children[0]
    if target.type != "navigation_expression" or not target.children:
        return None
    return node_text(source, target.children[0])


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
    if language == "java":
        return call_name_java(source, node)
    if language == "kotlin":
        return call_name_kotlin(source, node)
    if language == "python":
        return call_name_python(source, node)
    return call_name_js_go(source, node, language)


def extract_call_receiver(source: bytes, node: Node, language: str) -> str | None:
    """Return the receiver expression a call is made on, or None for a bare call.

    ``jdbcTemplate.query(...)`` -> ``"jdbcTemplate"``; ``this.dao.find(...)`` ->
    ``"this.dao"``; ``query(...)`` -> ``None``.

    The receiver is what distinguishes ``jdbcTemplate.execute`` from
    ``httpClient.execute`` — the method name alone does not (OI-7). Every grammar
    already exposes it; it was simply never read.
    """
    if language == "kotlin":
        # Kotlin has no `object` field: the receiver is the first child of the
        # `navigation_expression` the call wraps (OI-13).
        return call_receiver_kotlin(source, node)
    if language == "java":
        # Java `method_invocation` carries the receiver on its `object` field.
        obj = node.child_by_field_name("object")
        return node_text(source, obj) if obj is not None else None

    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    # Python `attribute` and JS/TS `member_expression` both name it `object`;
    # Go's `selector_expression` calls the same thing `operand`.
    for field in ("object", "operand"):
        recv = fn.child_by_field_name(field)
        if recv is not None:
            return node_text(source, recv)
    return None


CALL_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"method_invocation"}),
    # `navigation_expression` is a property access, not a call. Listing it made
    # `jdbcTemplate.query(...)` arrive twice — once as the call and once as the
    # navigation beneath it — which would double every Kotlin finding (OI-13).
    #
    # Belt and braces with `call_name_kotlin`, which rejects any node that is not
    # a `call_expression` independently. A mutant restoring the wider set could
    # not be killed for exactly that reason, and was dropped rather than the
    # second defence being removed to make it killable.
    "kotlin": frozenset({"call_expression"}),
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
