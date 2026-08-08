"""Tree-sitter AST walking helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from .base import supported_languages
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
    args = _argument_list(node)
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


def _argument_list(node: Node) -> Node | None:
    """The node holding a call's arguments, however its grammar spells it."""
    for field in _ARGUMENT_FIELDS:
        args = node.child_by_field_name(field)
        if args is not None:
            return args
    # Kotlin names no field: a `call_expression` holds a `value_arguments` child.
    # Without this every Kotlin call recorded an empty argument list, so no
    # Kotlin hop could carry taint and step 4 found no Kotlin paths at all — a
    # clean-looking result for half the JVM fleet.
    return next((c for c in node.children if c.type == "value_arguments"), None)


# The declaration nodes each grammar uses for a class and for a callable. A path
# through a service is a chain of these, so they are what `OI-17` needs before
# anything can be resolved or traversed.
CLASS_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"class_declaration", "interface_declaration", "enum_declaration"}),
    "kotlin": frozenset({"class_declaration", "object_declaration"}),
    "python": frozenset({"class_definition"}),
    "javascript": frozenset({"class_declaration"}),
    # `interface_declaration` too (`OI-43` step 3). Without it a TypeScript
    # interface was never recorded as a type at all, so T2 had nothing to expand
    # even once supertypes existed — the declaration a call resolves *to* was
    # missing, not just the edge to it.
    "typescript": frozenset({"class_declaration", "interface_declaration"}),
    "tsx": frozenset({"class_declaration", "interface_declaration"}),
    # `type_spec`, not `type_declaration` (`OI-43` step 2). Go puts the name on
    # the spec, so asking the declaration for a `name` field returned None and
    # `iter_type_declarations` skipped **every Go type in the fleet** — present
    # in the table, so the structural half of the language gate passed, and
    # producing nothing. `OI-13`'s shape for the third time.
    #
    # It also fixes the grouped form for free: `type ( A struct{}; B interface{} )`
    # is one declaration holding several specs, so keying on the declaration
    # could at best have found the first.
    "go": frozenset({"type_spec", "type_alias"}),
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
    params = _parameter_list(node)
    if params is None:
        return []
    out: list[str] = []
    for child in params.children:
        if child.type not in PARAM_NODE_TYPES:
            continue
        name = _parameter_name(source, child)
        if name:
            out.append(name)
    # `self`/`this` is the receiver, not an input.
    return [p for p in out if p not in ("self", "this")]


def _parameter_list(node: Node) -> Node | None:
    """The node holding a callable's parameters, however its grammar spells it."""
    params = node.child_by_field_name("parameters")
    if params is not None:
        return params
    # Kotlin exposes no `parameters` field: a `fun` declaration holds a
    # `function_value_parameters` child instead. So every Kotlin method recorded
    # an *empty* parameter list from the moment `method-decl` shipped in 2.1.0 —
    # invisible until `OI-17` step 4 tried to taint one, because the step 1
    # parity test compared method names and not their parameters.
    return next(
        (c for c in node.children if c.type == "function_value_parameters"), None,
    )


def _parameter_name(source: bytes, child: Node) -> str | None:
    """The name one parameter declaration binds."""
    name = child.child_by_field_name("name")
    if name is not None:
        return node_text(source, name)
    if child.type == "identifier":
        return node_text(source, child)
    # Kotlin's `parameter` names the identifier positionally rather than by
    # field: `filter: String` is (identifier, ':', user_type).
    ident = next((c for c in child.children if c.type == "identifier"), None)
    return node_text(source, ident) if ident is not None else None


def _go_receiver_owner(source: bytes, node: Node) -> str | None:
    """The type a Go method hangs off, read from its receiver (`OI-43` step 2).

    Every other grammar here nests a method inside its class, so containment
    answers "what does this belong to". **Go does not** — `func (j *JdbcRepo)
    Find()` sits at file scope and names its owner in the receiver, so
    containment finds nothing and every Go method was recorded with no class at
    all. Types alone would not have fixed that: they would have been indexed with
    no method ever resolving to them.
    """
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    for child in receiver.children:
        if child.type != "parameter_declaration":
            continue
        declared = child.child_by_field_name("type")
        if declared is None:
            continue
        # `*JdbcRepo` and `JdbcRepo` name the same type for resolution.
        return node_text(source, declared).lstrip("*").strip()
    return None


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
        owner = _go_receiver_owner(source, node) if language == "go" else None
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
    # `OI-43` step 3. TypeScript declares a member outright and also declares one
    # as a constructor parameter property — `constructor(private dao: Dao)` — and
    # the second form is the Angular/NestJS injection shape, so missing it would
    # leave TypeScript resolvable only by accident, exactly as it would have for
    # Kotlin's `class_parameter`.
    "typescript": frozenset({"public_field_definition", "required_parameter"}),
    "tsx": frozenset({"public_field_definition", "required_parameter"}),
    # Go's struct members. An entry with no name is an *embedded* type, which is
    # a supertype rather than a field — see `_go_supertypes`.
    "go": frozenset({"field_declaration"}),
    # Only an *annotated* assignment. `plain = 1` states no type, so it says
    # nothing a call can be resolved against.
    "python": frozenset({"assignment"}),
}

# Java names the two relations; Kotlin gives both as `delegation_specifier` and
# separates them only by a trailing constructor call. Recorded as one list,
# because for resolution a superclass and an interface answer the same question:
# where else might this method be declared.
SUPERTYPE_NODE_TYPES: dict[str, frozenset[str]] = {
    "java": frozenset({"superclass", "super_interfaces"}),
    "kotlin": frozenset({"delegation_specifier"}),
    # `OI-43` step 3. `class_heritage` holds both the extends and implements
    # clauses; an interface says `extends` in its own node.
    "typescript": frozenset({"class_heritage", "extends_type_clause"}),
    "tsx": frozenset({"class_heritage", "extends_type_clause"}),
    "javascript": frozenset({"class_heritage"}),
    # Both of these are read positionally rather than by walking for the node
    # type — see `_supertypes`. Python's bases are an `argument_list`, which is
    # also every call's argument list, and Go's embedding is a `field_declaration`
    # with no name, which is also every named field. Walking for either would
    # collect nonsense, so the entry declares coverage and the reader is precise.
    "python": frozenset({"argument_list"}),
    "go": frozenset({"field_declaration", "type_elem"}),
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


def _annotation_text(source: bytes, node: Node | None) -> str:
    """A `type_annotation` reads `: Repo`; the colon is punctuation, not a type."""
    if node is None:
        return ""
    return node_text(source, node).lstrip(":").strip()


def _ts_fields(source: bytes, node: Node) -> dict[str, str]:
    """Return {member: declared type} for a TypeScript class (`OI-43` step 3).

    Two forms, and the second is the one that matters. `private repo: Repo` is a
    plain member; `constructor(private dao: Dao)` is a *parameter property*, which
    declares a member and injects it in one line. That is the Angular and NestJS
    shape, so reading only the first would leave TypeScript resolvable by
    accident — the same trap `class_parameter` was for Kotlin.

    A constructor parameter with no accessibility modifier is an ordinary
    argument and declares nothing, so it is skipped.
    """
    out: dict[str, str] = {}
    for child in walk(node):
        pair = _ts_member(source, child)
        if pair is not None:
            out[pair[0]] = pair[1]
    return out


def _ts_member(source: bytes, node: Node) -> tuple[str, str] | None:
    """Read one TypeScript member, from either declaration form."""
    if node.type == "public_field_definition":
        name = node.child_by_field_name("name")
    elif node.type == "required_parameter" and _ts_parameter_property(node):
        name = node.child_by_field_name("pattern")
    else:
        return None
    declared = _annotation_text(source, node.child_by_field_name("type"))
    if name is None or not declared:
        return None
    return node_text(source, name), declared


def _ts_parameter_property(node: Node) -> bool:
    """Whether a constructor parameter also declares a member.

    `private dao: Dao` does; `plain: string` does not. The accessibility modifier
    is the whole difference, and without this check every method's arguments
    would be recorded as fields of its class.
    """
    return any(c.type == "accessibility_modifier" for c in node.children)


def _go_fields(source: bytes, node: Node) -> dict[str, str]:
    """Return {field: declared type} for a Go struct (`OI-43` step 3).

    A `field_declaration` with no name is an *embedded* type, not a field —
    `_go_supertypes` reads those, because embedding is how Go says "this has
    everything that one has", which is the question a supertype answers.
    """
    out: dict[str, str] = {}
    for child in walk(node):
        if child.type != "field_declaration":
            continue
        name = child.child_by_field_name("name")
        declared = child.child_by_field_name("type")
        if name is not None and declared is not None:
            out[node_text(source, name)] = node_text(source, declared)
    return out


def _python_fields(source: bytes, node: Node) -> dict[str, str]:
    """Return {attribute: annotated type} for a Python class (`OI-43` step 3).

    Only annotated assignments. `repo: Repo` states a type a call can be resolved
    against; `plain = 1` states none, and recording it as a field with no type
    would be worse than not recording it — the caller cannot tell "untyped" from
    "typed as nothing".
    """
    out: dict[str, str] = {}
    for child in walk(node):
        if child.type != "assignment":
            continue
        name = child.child_by_field_name("left")
        declared = child.child_by_field_name("type")
        if name is not None and declared is not None:
            out[node_text(source, name)] = node_text(source, declared)
    return out


_FIELD_READERS: dict[str, Callable[[bytes, Node], dict[str, str]]] = {
    "kotlin": _kotlin_fields,
    "typescript": _ts_fields,
    "tsx": _ts_fields,
    "go": _go_fields,
    "python": _python_fields,
}


def _python_supertypes(source: bytes, node: Node) -> list[str]:
    """Python's bases, read from the `superclasses` field rather than by walking.

    `class Svc(Repo, Base)` is an `argument_list` — and so is `helper(1, 2)`
    inside a method. Walking for the node type would record every call's
    arguments as supertypes, so the field is the only safe way in.
    """
    bases = node.child_by_field_name("superclasses")
    if bases is None:
        return []
    return [
        node_text(source, child)
        for child in bases.children
        if child.type in ("identifier", "attribute")
    ]


def _go_supertypes(source: bytes, node: Node) -> list[str]:
    """Go's embedded types, which are how it says "has everything that one has".

    A struct embeds by declaring a `field_declaration` with a type and **no
    name**; an interface embeds with a bare `type_elem`. Both promote the
    embedded type's methods onto the embedder, which is exactly the question
    `OI-17`'s T2 asks — where else might this method be declared.
    """
    out: list[str] = []
    for child in walk(node):
        if child.type == "field_declaration" and child.child_by_field_name("name") is None:
            declared = child.child_by_field_name("type")
            if declared is not None:
                out.append(node_text(source, declared))
        elif child.type == "type_elem":
            out.append(node_text(source, child))
    return [n for n in dict.fromkeys(out) if n]


def _supertypes(source: bytes, node: Node, language: str) -> list[str]:
    """Return the type names this declaration extends or implements."""
    wanted = SUPERTYPE_NODE_TYPES.get(language, frozenset())
    if not wanted:
        return []
    if language == "python":
        return _python_supertypes(source, node)
    if language == "go":
        return _go_supertypes(source, node)
    out: list[str] = []
    for child in walk(node):
        if child.type not in wanted:
            continue
        text = node_text(source, child)
        for name in _TYPE_NAME_RX.findall(text):
            if name not in ("extends", "implements") and name not in out:
                out.append(name)
    return out


def coverage_gaps(language: str) -> tuple[str, ...]:
    """What this language cannot contribute, derived from the tables themselves.

    `OI-43` step 4. The gaps are *computed*, never restated: a hand-written list
    of "languages we do not fully support" is the thing that rotted into `OI-43`
    in the first place, and would go stale the moment a table gained an entry.
    Reading the tables means the note a repo carries and the code's actual
    behaviour cannot disagree.

    Empty means full coverage — the note is only emitted when there is something
    to say, so a Java repo stays quiet.
    """
    if language not in supported_languages():
        return ("no tree-sitter grammar, so no calls, declarations or types",)
    missing: list[str] = []
    for what, table in (
        ("type declarations", CLASS_NODE_TYPES),
        ("method declarations", METHOD_NODE_TYPES),
        ("call sites", CALL_NODE_TYPES),
        ("declared field types, so T1 resolution cannot fire", FIELD_NODE_TYPES),
        ("supertypes, so T2 resolution cannot fire", SUPERTYPE_NODE_TYPES),
    ):
        if language not in table:
            missing.append(what)
    return tuple(missing)


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
        else:
            reader = _FIELD_READERS.get(language)
            fields = reader(source, node) if reader is not None else {}
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
    if language == "go":
        # Go says it in the spec's `type` child: `type Repo interface { ... }`
        # is a `type_spec` whose type is an `interface_type`.
        declared = node.child_by_field_name("type")
        return declared is not None and declared.type == "interface_type"
    if language != "kotlin":
        return False
    return any(child.type == "interface" for child in node.children)
