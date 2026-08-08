"""OI-17, step two: the type facts a call has to be resolved against.

Step one gave every node its enclosing method. Resolving `stockService.process()`
to the method it actually invokes needs two more facts, and neither was
extracted: what type the receiver was declared as, and which classes implement a
given interface.

Both are a plain syntactic read — `private final StockService stockService`
states the type outright — which is what makes offline resolution possible at
all. A prototype resolved the canonical controller→service→DAO chain from these
facts alone, with no compiler and no type inference.

The interface case is the one that matters most, and it is why the answer can
never be "unreachable": a call on an interface-typed field resolves to a method
with **no body**, so a resolver that stops at the declared type reports a dead
end rather than a weak answer. Constructor-injected interface fields are the
standard Spring shape, so that is the default case in the fleet, not a corner.

Supertypes are recorded as one list rather than split into extends/implements.
Kotlin gives both as `delegation_specifier` and separates them only by whether a
constructor call follows, and for resolution the question is "where else might
this method be declared" — which a superclass and an interface answer alike.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file

_JAVA = """
public class StockController implements Auditable, Traceable {
    private final StockService stockService;
    private JdbcTemplate jdbcTemplate;

    void submit() { }
}

interface StockService {
    Result process(String filter);
}

class StockServiceImpl extends BaseService implements StockService {
    public Result process(String filter) { return null; }
}
"""

_KOTLIN = """
class StockController(private val stockService: StockService) : Auditable {
    private val jdbcTemplate: JdbcTemplate = tpl

    fun submit() { }
}

class StockServiceImpl : BaseService(), StockService {
    fun process(filter: String): Result? = null
}
"""


def _types(source: str, language: str, rel_path: str):
    nodes = extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source
    )[0]
    return {n.detail["class"]: n.detail for n in nodes if n.family == "type-decl"}


def test_field_types_are_recorded():
    """`private final StockService stockService` says what the receiver is.

    This is the fact that makes `stockService.process()` resolvable without a
    compiler, and it was not extracted.
    """
    types = _types(_JAVA, "java", "src/StockController.java")
    assert types["StockController"]["fields"] == {
        "stockService": "StockService",
        "jdbcTemplate": "JdbcTemplate",
    }


def test_supertypes_are_recorded_as_one_list():
    """A superclass and an interface answer the same question for resolution."""
    types = _types(_JAVA, "java", "src/StockController.java")
    assert sorted(types["StockController"]["supertypes"]) == ["Auditable", "Traceable"]
    assert sorted(types["StockServiceImpl"]["supertypes"]) == [
        "BaseService",
        "StockService",
    ]


def test_an_interface_is_distinguishable_from_a_class():
    """A call resolving to an interface method finds no body, and must know it."""
    types = _types(_JAVA, "java", "src/StockController.java")
    assert types["StockService"]["is_interface"] is True
    assert types["StockServiceImpl"]["is_interface"] is False


def test_kotlin_constructor_properties_are_fields():
    """The Spring shape in Kotlin declares its collaborators in the constructor.

    `class StockController(private val stockService: StockService)` is the same
    fact as a Java field, written differently — and missing it would leave Kotlin
    resolvable only where a property happens to be declared in the body.
    """
    types = _types(_KOTLIN, "kotlin", "src/StockController.kt")
    assert types["StockController"]["fields"] == {
        "stockService": "StockService",
        "jdbcTemplate": "JdbcTemplate",
    }


def test_kotlin_supertypes_include_both_forms():
    """`: BaseService(), StockService` is a superclass and an interface, undifferentiated."""
    types = _types(_KOTLIN, "kotlin", "src/StockController.kt")
    assert sorted(types["StockServiceImpl"]["supertypes"]) == [
        "BaseService",
        "StockService",
    ]


@pytest.mark.parametrize(
    ("source", "language", "rel_path"),
    [
        (_JAVA, "java", "src/StockController.java"),
        (_KOTLIN, "kotlin", "src/StockController.kt"),
    ],
)
def test_java_and_kotlin_agree_on_the_controller(source, language, rel_path):
    """Parity, since half the JVM fleet is Kotlin and `OI-13` only just unlocked it."""
    types = _types(source, language, rel_path)
    assert types["StockController"]["fields"]["stockService"] == "StockService"
    assert "Auditable" in types["StockController"]["supertypes"]


def test_a_type_with_no_fields_or_supertypes_is_still_recorded():
    """Absence must be recorded, or a resolver cannot tell 'none' from 'unparsed'."""
    types = _types("public class Plain { void go() { } }", "java", "src/Plain.java")
    assert types["Plain"]["fields"] == {}
    assert types["Plain"]["supertypes"] == []


def test_type_declarations_are_observations():
    """They record what the file declares, never that anything is wrong."""
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/StockController.java", language="java",
        source=_JAVA,
    )[0]
    assert all(n.kind == "reference" for n in nodes if n.family == "type-decl")


def test_an_unsupported_language_yields_no_declarations():
    """The pass must decline cleanly rather than guessing at a grammar it lacks."""
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/thing.rb", language="ruby",
        source="class Foo; end",
    )[0]
    assert [n for n in nodes if n.family in ("type-decl", "method-decl")] == []


def test_unparsable_source_yields_no_declarations():
    """Scanned source is untrusted and often partial; a bad parse must not raise."""
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/Broken.java", language="java",
        source="public class { { { unterminated",
    )[0]
    assert all(n.family != "type-decl" or "class" in n.detail for n in nodes)


def test_a_language_without_field_syntax_still_records_its_types():
    """Python declares no field types, so `fields` is empty rather than absent."""
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/dao.py", language="python",
        source="class StockDao:\n    def find(self):\n        pass\n",
    )[0]
    types = {n.detail["class"]: n.detail for n in nodes if n.family == "type-decl"}
    assert types["StockDao"]["fields"] == {}
    assert types["StockDao"]["supertypes"] == []


def test_a_kotlin_interface_is_recognised_as_one():
    """Kotlin has no `interface_declaration` node, and this was silently wrong.

    `interface Foo { }` parses as a `class_declaration` whose first child is the
    `interface` keyword, so testing the node type marked every Kotlin interface
    as a class. Shipped that way in 2.1.0 and invisible until `OI-17` step 3
    tried to resolve through one: a call on an interface-typed field bound to the
    bodiless interface method and the chain stopped, reporting a dead end for the
    standard Spring shape across half the JVM fleet.

    The Java case passed throughout, which is exactly the failure `OI-13` exists
    to prevent — an answer that looks clean because one language was invisible.
    """
    types = _types(
        "interface StockService {\n    fun process(f: String): Result\n}\n",
        "kotlin", "src/StockService.kt",
    )
    assert types["StockService"]["is_interface"] is True


def test_a_kotlin_class_is_not_mistaken_for_an_interface():
    """The other direction, so the fix cannot be 'return True for Kotlin'."""
    types = _types(
        "class StockServiceImpl : StockService {\n    fun process(f: String): Result? = null\n}\n",
        "kotlin", "src/StockServiceImpl.kt",
    )
    assert types["StockServiceImpl"]["is_interface"] is False


# --- Go (`OI-43` step 2) ------------------------------------------------------

_GO = """
type StockService interface {
	Process(filter string) Result
}

type StockServiceImpl struct {
	jdbc *JdbcTemplate
}

func (s *StockServiceImpl) Process(filter string) Result { return nil }

func Free() {}

type (
	Grouped struct{ x int }
	AlsoGrouped interface{ Go() }
)
"""


def _go_types():
    return _types(_GO, "go", "src/service.go")


def test_go_declares_types_at_all():
    """`OI-43` step 2: every Go type in the fleet was discarded, silently.

    `type_declaration` was in `CLASS_NODE_TYPES`, so it looked configured — but
    Go puts the name on the child `type_spec`, and `_declaration_name` asks the
    node itself. It got `None` and skipped. `OI-13`'s shape for the third time:
    routed to a walker that needs a node the grammar never produces.
    """
    assert sorted(_go_types()) == [
        "AlsoGrouped", "Grouped", "StockService", "StockServiceImpl",
    ]


def test_a_grouped_declaration_yields_every_type():
    """`type ( A struct{}; B interface{} )` is one declaration holding many specs.

    Keying on the declaration could at best have found the first, so reading the
    spec fixes a second defect the original could not have expressed.
    """
    types = _go_types()
    assert "Grouped" in types and "AlsoGrouped" in types


def test_a_go_interface_is_recognised():
    """Go says it in the spec's `type` child, not in the node type."""
    assert _go_types()["StockService"]["is_interface"] is True


def test_a_go_struct_is_not_mistaken_for_an_interface():
    """The other direction, so the fix cannot be 'return True for Go'."""
    assert _go_types()["StockServiceImpl"]["is_interface"] is False


def test_a_go_method_knows_the_type_it_hangs_off():
    """Containment cannot answer this: Go declares methods outside the type.

    Types alone would have been inert — indexed, with no method ever resolving to
    them — so the receiver is read to find the owner.
    """
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/service.go", language="go", source=_GO,
    )[0]
    owners = {
        n.detail["method"]: n.detail.get("class")
        for n in nodes if n.family == "method-decl"
    }
    assert owners["Process"] == "StockServiceImpl", (
        "a pointer receiver names the same type as a value receiver"
    )


def test_a_package_level_go_function_has_no_owner():
    """`func Free()` hangs off nothing, and must not be attributed to a neighbour."""
    nodes = extract_from_file(
        repo_id="g/r", rel_path="src/service.go", language="go", source=_GO,
    )[0]
    owners = {
        n.detail["method"]: n.detail.get("class")
        for n in nodes if n.family == "method-decl"
    }
    assert owners["Free"] is None
