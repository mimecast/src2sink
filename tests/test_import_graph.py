"""The package import graph must stay acyclic, and bindings must have one owner.

`known_api_clients` and `extractors.http_out` used to import each other: the
registry pushed a compiled projection of the bindings into the extractor, so it
had to know its own consumer. That forced a deferred import to keep the package
importable, and it meant the binding configuration existed in two places that
could disagree.

The fix inverts the push into a lazy pull, so these tests guard two things: the
edge is gone (structural), and configuring the registry alone is sufficient
(behavioural). The second is the one that matters day to day — it is the
property the old design needed a "single entry point" docstring to promise.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src2sink import known_api_clients as kac
from src2sink.extractors.http_out import get_binding_call_patterns
from src2sink.known_api_clients import ApiClientBinding

_SRC = Path(__file__).resolve().parent.parent / "src2sink"

BINDING = ApiClientBinding(
    target_repo="commerce/warehouse-service",
    maven_artifact="warehouse-service-client",
    import_prefix="com.example.commerce.warehouse.client",
    service_aliases=("warehouse-service",),
    paths=("/stock",),
    class_patterns=("WarehouseServiceClient",),
)


def _module_name(path: Path) -> str:
    """Return the dotted module name for a file under the package root."""
    rel = path.relative_to(_SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _first_party_imports(path: Path, mod: str) -> set[str]:
    """Return the first-party modules ``path`` imports at any level."""
    is_pkg = path.name == "__init__.py"
    package = mod if is_pkg else mod.rsplit(".", 1)[0]
    pkg_parts = package.split(".")
    deps: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(pkg_parts[: len(pkg_parts) - (node.level - 1)])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if target.startswith("src2sink"):
                deps.add(target)
        elif isinstance(node, ast.Import):
            deps.update(a.name for a in node.names if a.name.startswith("src2sink"))
    return deps


def _import_graph() -> dict[str, set[str]]:
    """Build the first-party import graph for the whole package."""
    graph: dict[str, set[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        mod = _module_name(path)
        graph[mod] = _first_party_imports(path, mod)
    return graph


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return one representative path per distinct import cycle."""
    cycles: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def walk(node: str, stack: list[str]) -> None:
        if node in stack:
            cycle = stack[stack.index(node):] + [node]
            key = tuple(sorted(set(cycle)))
            if key not in seen:
                seen.add(key)
                cycles.append(cycle)
            return
        for dep in graph.get(node, ()):
            if dep in graph:
                walk(dep, [*stack, node])

    for mod in graph:
        walk(mod, [])
    return cycles


def test_package_import_graph_is_acyclic():
    """No first-party module may take part in an import cycle.

    A cycle is not merely a style problem: it makes the package importable only
    in some orders, and the usual workaround — deferring one import into a
    function body — hides the coupling instead of removing it.
    """
    cycles = _find_cycles(_import_graph())
    rendered = "\n  ".join(" -> ".join(c) for c in cycles)
    assert not cycles, f"{len(cycles)} import cycle(s):\n  {rendered}"


def test_the_binding_registry_does_not_import_its_consumer():
    """`known_api_clients` must not depend on any extractor.

    Named separately from the cycle test because this is the *direction* that
    matters: an extractor consulting the registry is sensible, the registry
    reaching into an extractor to refresh a second copy of its own state is not.
    """
    deps = _first_party_imports(_SRC / "known_api_clients.py", "src2sink.known_api_clients")
    offenders = sorted(d for d in deps if ".extractors" in d)
    assert not offenders, f"registry imports its consumers: {offenders}"


@pytest.fixture
def _reset_bindings():
    """Leave the binding registry empty however the test exits."""
    yield
    kac.configure_api_client_bindings(())


def test_configuring_the_registry_alone_reaches_the_extractor(_reset_bindings):
    """One call must be enough — there is no second place to configure.

    Previously `configure_api_client_bindings` set the registry and a *separate*
    `configure_http_out_client_patterns` set the extractor's compiled copy, so
    any caller doing only the first silently disabled every `class_patterns`
    binding.
    """
    assert get_binding_call_patterns() == []
    kac.configure_api_client_bindings((BINDING,))

    patterns = get_binding_call_patterns()
    assert [p.target_repo for p in patterns] == ["commerce/warehouse-service"]
    assert patterns[0].pattern.search("WarehouseServiceClient.post(body)")


def test_binding_patterns_follow_a_reconfigure(_reset_bindings):
    """Derived patterns must track the registry, not a stale snapshot."""
    kac.configure_api_client_bindings((BINDING,))
    assert get_binding_call_patterns()

    kac.configure_api_client_bindings(())
    assert get_binding_call_patterns() == []


def test_derived_patterns_are_cached_per_binding_set(_reset_bindings):
    """Deriving on every call would recompile a regex per call site scanned."""
    kac.configure_api_client_bindings((BINDING,))
    assert get_binding_call_patterns() is get_binding_call_patterns()
