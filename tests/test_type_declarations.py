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
