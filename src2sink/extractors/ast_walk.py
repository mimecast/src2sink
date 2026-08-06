"""Tree-sitter AST walking helpers."""

from __future__ import annotations

import re
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


# The field naming the argument list, per grammar. Java and Kotlin agree on
# `arguments`; the C-family grammars all use it too, so the exceptions are what
# is listed rather than the rule.
_ARGUMENT_FIELDS = ("arguments", "argument_list")

# Wrapper nodes that hold the arguments themselves. Yielding these instead of
# the real arguments would make every call look like it took one argument
# spelled "(a, b)".
_ARGUMENT_NOISE = frozenset({"(", ")", ",", "value_argument", "argument"})


def extract_call_arguments(source: bytes, node: Node, language: str) -> list[str]:
    """Return the argument expressions a call passes, as source text.

    `stockService.process(req.getFilter())` -> `["req.getFilter()"]`.

    Text rather than a resolved value, because that is all a syntactic read can
    honestly give: `OI-17` step 4 asks whether the argument at a call site
    carries something a parameter upstream received, which is a comparison of
    expressions, not an evaluation of them.

    Captured now rather than with step 4 deliberately. Recording it changes what
    a record contains, so it costs a `DETECTION_VERSION` bump and a fleet rescan
    — and step 3's widening already forces one. Two rescans for work that fits
    in one is the expensive way round.
    """
    args = None
    for field in _ARGUMENT_FIELDS:
        args = node.child_by_field_name(field)
        if args is not None:
            break
    if args is None:
        return []

    out: list[str] = []
    for child in args.named_children:
        if child.type in _ARGUMENT_NOISE:
            # Kotlin wraps each argument in a `value_argument`; unwrap to the
            # expression so Java and Kotlin record the same text (OI-13 parity).
            inner = child.named_children
            if inner:
                out.append(node_text(source, inner[0]))
            continue
        out.append(node_text(source, child))
    return [a for a in (t.strip() for t in out) if a]


# The declaration nodes each grammar uses for a class and for a callable. A path
# through a service is a chain of these, so they are what `OI-17` needs before
# anything can be resolved or traversed.
CLASS_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"class_declaration", "interface_declaration", "enum_declaration"}),
    "kotlin": frozenset({"class_declaration", "object_declaration"}),
    "python": frozenset({"class_definition"}),
    "javascript": frozenset({"class_declaration"}),
    "typescript": frozenset({"class_declaration"}),
    "tsx": frozenset({"class_declaration"}),
    "go": frozenset({"type_declaration"}),
}

METHOD_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"method_declaration", "constructor_declaration"}),
    "kotlin": frozenset({"function_declaration"}),
    "python": frozenset({"function_definition"}),
    "javascript": frozenset({"function_declaration", "method_definition"}),
    "typescript": frozenset({"function_declaration", "method_definition"}),
    "tsx": frozenset({"function_declaration", "method_definition"}),
    "go": frozenset({"function_declaration", "method_declaration"}),
}

PARAM_NODE_TYPES = frozenset({
    "formal_parameter", "spread_parameter",      # java
    "parameter",                                  # kotlin, python, ts
    "identifier",                                 # python bare params
    "required_parameter", "optional_parameter",   # typescript
    "parameter_declaration",                      # go
})

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


def _declaration_name(source: bytes, node: Node) -> str | None:
    """Return a declaration's name, or None if the grammar gives it none."""
    name = node.child_by_field_name("name")
    return node_text(source, name) if name is not None else None


def _parameter_names(source: bytes, node: Node, language: str) -> list[str]:
    """Return the parameter names a callable declares.

    Names rather than types: what matters for `OI-17` is whether an argument
    reaching this method carries a tainted value, and the parameter name is what
    the body refers to it by.
    """
    params = node.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[str] = []
    for child in params.children:
        if child.type not in PARAM_NODE_TYPES:
            continue
        name = child.child_by_field_name("name")
        if name is not None:
            out.append(node_text(source, name))
        elif child.type == "identifier":
            out.append(node_text(source, child))
    # `self`/`this` is the receiver, not an input.
    return [p for p in out if p not in ("self", "this")]


def iter_method_declarations(
    source: bytes, root: Node, language: str
) -> Iterator[tuple[str | None, str, list[str], int, int]]:
    """Yield (class, method, params, start line, end line) for each callable.

    The enclosing class is resolved by containment rather than by walking parents,
    because the grammars disagree about how a method hangs off its class and
    containment is the same question in all of them.
    """
    classes = [
        (n, _declaration_name(source, n))
        for n in walk(root)
        if n.type in CLASS_NODE_TYPES.get(language, frozenset())
    ]
    for node in walk(root):
        if node.type not in METHOD_NODE_TYPES.get(language, frozenset()):
            continue
        name = _declaration_name(source, node)
        if name is None:
            continue
        owner = None
        for cls_node, cls_name in classes:
            if cls_node.start_byte <= node.start_byte and node.end_byte <= cls_node.end_byte:
                owner = cls_name
        yield (
            owner,
            name,
            _parameter_names(source, node, language),
            node.start_point[0] + 1,
            node.end_point[0] + 1,
        )


# Where each grammar declares a field's type. Java puts it on `field_declaration`
# with a separate declarator; Kotlin uses `property_declaration` in the body and
# `class_parameter` in the constructor — and the constructor form is the standard
# Spring shape, so missing it would leave Kotlin resolvable only by accident.
FIELD_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"field_declaration"}),
    "kotlin": frozenset({"property_declaration", "class_parameter"}),
}

# Java names the two relations; Kotlin gives both as `delegation_specifier` and
# separates them only by a trailing constructor call. Recorded as one list,
# because for resolution a superclass and an interface answer the same question:
# where else might this method be declared.
SUPERTYPE_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"superclass", "super_interfaces"}),
    "kotlin": frozenset({"delegation_specifier"}),
}

# Bounded like every pattern that reads scanned source (TA-005). An identifier
# longer than this is not a type name, and leaving the run open invites the
# harvest gate to fail for a reason nobody has to think about.
_TYPE_NAME_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,200}")


def _java_fields(source: bytes, body: Node) -> dict[str, str]:
    """Return {field name: declared type} for a Java class body."""
    out: dict[str, str] = {}
    for node in walk(body):
        if node.type != "field_declaration":
            continue
        type_node = node.child_by_field_name("type")
        if type_node is None:
            continue
        declared = node_text(source, type_node)
        for declarator in node.children_by_field_name("declarator"):
            name = declarator.child_by_field_name("name")
            if name is not None:
                out[node_text(source, name)] = declared
    return out


def _kotlin_named_type(source: bytes, node: Node) -> tuple[str, str] | None:
    """Read a Kotlin `name: Type` pair positionally.

    The grammar exposes no `name` or `type` fields here — a `class_parameter` and
    a `variable_declaration` are both an `identifier`, a `:` and a `user_type` in
    sequence, so they are read by position rather than by field.
    """
    name = next((c for c in node.children if c.type == "identifier"), None)
    declared = next((c for c in node.children if c.type == "user_type"), None)
    if name is None or declared is None:
        return None
    return node_text(source, name), node_text(source, declared)


def _kotlin_fields(source: bytes, node: Node) -> dict[str, str]:
    """Return {field name: declared type} for a Kotlin class.

    Covers both the constructor form — `class C(private val svc: Service)`, which
    is the standard Spring shape — and a property declared in the body. A local
    variable inside a function body is *not* a field, so only the
    `variable_declaration` directly beneath a `property_declaration` counts.
    """
    out: dict[str, str] = {}
    for child in walk(node):
        if child.type == "class_parameter":
            pair = _kotlin_named_type(source, child)
        elif child.type == "property_declaration":
            decl = next(
                (c for c in child.children if c.type == "variable_declaration"), None
            )
            pair = _kotlin_named_type(source, decl) if decl is not None else None
        else:
            continue
        if pair is not None:
            out[pair[0]] = pair[1]
    return out


def _supertypes(source: bytes, node: Node, language: str) -> list[str]:
    """Return the type names this declaration extends or implements."""
    wanted = SUPERTYPE_NODE_TYPES.get(language, frozenset())
    if not wanted:
        return []
    out: list[str] = []
    for child in walk(node):
        if child.type not in wanted:
            continue
        text = node_text(source, child)
        for name in _TYPE_NAME_RX.findall(text):
            if name not in ("extends", "implements") and name not in out:
                out.append(name)
    return out


def iter_type_declarations(
    source: bytes, root: Node, language: str
) -> Iterator[tuple[str, dict[str, str], list[str], bool, int]]:
    """Yield (class, fields, supertypes, is_interface, line) for each type declared.

    The facts a call is resolved against (`OI-17`). ``fields`` is what makes
    ``stockService.process()`` resolvable without a compiler; ``supertypes`` is
    what lets a call on an interface reach the implementations that have a body,
    which is the difference between a weak answer and a dead end.
    """
    for node in walk(root):
        if node.type not in CLASS_NODE_TYPES.get(language, frozenset()):
            continue
        name = _declaration_name(source, node)
        if name is None:
            continue
        body = node.child_by_field_name("body")
        if language == "java":
            fields = _java_fields(source, body) if body is not None else {}
        elif language == "kotlin":
            fields = _kotlin_fields(source, node)
        else:
            fields = {}
        yield (
            name,
            fields,
            _supertypes(source, node, language),
            _is_interface(node, language),
            node.start_point[0] + 1,
        )


def _is_interface(node: Node, language: str) -> bool:
    """Whether a type declaration declares an interface rather than a class.

    Java gets its own node type. **Kotlin does not** — `interface Foo { }` parses
    as a `class_declaration` whose first child is the `interface` keyword, so
    testing the node type marked every Kotlin interface as a class.

    That was silently wrong from the moment `type-decl` shipped, and it only
    surfaced when `OI-17` step 3 tried to resolve through one: a Kotlin call on
    an interface-typed field bound to the interface's own bodiless method and
    the chain stopped there, reporting a dead end for the standard Spring shape
    across half the JVM fleet. Exactly the failure `OI-13` exists to prevent —
    an answer that looks clean because a language was invisible.
    """
    if node.type == "interface_declaration":
        return True
    if language != "kotlin":
        return False
    return any(child.type == "interface" for child in node.children)
