"""OI-18: Maven dependency versions, resolved offline as far as the fleet allows.

`parse_pom_dependencies` read `<version>` as text. Maven resolves it from four
places and the parser knew one, so most recorded versions were a `${property}`
string or an empty string presented as a version. It also read
`<dependencyManagement>` as though it were `<dependencies>`, emitting the BOM
itself as a dependency the code never calls.

Resolution is offline and tiered, and the tier is recorded rather than assumed:

    literal | property | parent-in-repo | parent-in-fleet | unresolved

The fleet checkout *is* the artifact repository for internal coordinates — every
internal repo is already cloned, and the identity index already maps a coordinate
to its clone path — so a parent POM in a *different repository* is a file read.
No `mvn`, no registry, no downloaded binaries.

Two rules matter more than the tiers. An unresolved version is recorded as
unresolved, never as `${...}` or `""`. And `<dependencyManagement>` constrains
versions without declaring a dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src2sink.internal_groups import configure_internal_group_patterns
from src2sink.maven import resolve_pom_dependencies

# Every fixture below is *namespaced*, because bare `<project>` is the one shape
# that hides the defect a 2.0.0 user reported: stripping `xmlns:xsi="..."` with a
# regex left `xsi:schemaLocation` behind as an unbound prefix, so every POM an IDE
# or archetype emits failed to parse and returned zero dependencies. The original
# fixtures were all bare, so the suite was green while the parser was blind.
_NS = (
    ' xmlns="http://maven.apache.org/POM/4.0.0"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xsi:schemaLocation="http://maven.apache.org/POM/4.0.0'
    ' http://maven.apache.org/xsd/maven-4.0.0.xsd"'
)

_CONSUMER_LITERAL = f"""<project{_NS}>
  <groupId>com.example</groupId><artifactId>consumer</artifactId><version>1.0.0</version>
  <dependencies><dependency>
    <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
    <version>1.4.2</version>
  </dependency></dependencies>
</project>"""

_CONSUMER_PROPERTY = f"""<project{_NS}>
  <groupId>com.example</groupId><artifactId>consumer</artifactId><version>1.0.0</version>
  <properties><warehouse.version>1.4.2</warehouse.version></properties>
  <dependencies><dependency>
    <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
    <version>${{warehouse.version}}</version>
  </dependency></dependencies>
</project>"""

_CONSUMER_BOM = f"""<project{_NS}>
  <groupId>com.example</groupId><artifactId>consumer</artifactId><version>1.0.0</version>
  <dependencyManagement><dependencies>
    <dependency><groupId>com.example</groupId><artifactId>platform-bom</artifactId>
      <version>7.2.0</version><type>pom</type><scope>import</scope></dependency>
    <dependency><groupId>com.example.commerce</groupId>
      <artifactId>warehouse-client</artifactId><version>1.4.2</version></dependency>
  </dependencies></dependencyManagement>
  <dependencies><dependency>
    <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
  </dependency></dependencies>
</project>"""


@pytest.fixture(autouse=True)
def _internal():
    configure_internal_group_patterns([r"^com\.example(\..+)?$"])
    yield
    configure_internal_group_patterns([r"^com\.example(\..+)?$"])


def _pom(tmp_path: Path, body: str, name: str = "pom.xml") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _client(deps: list[dict[str, str]]) -> dict[str, str]:
    return next(d for d in deps if d["artifactId"] == "warehouse-client")


def test_a_literal_version_resolves_as_literal(tmp_path):
    """The one case that already worked, kept working and now labelled."""
    deps = resolve_pom_dependencies(_pom(tmp_path, _CONSUMER_LITERAL), tmp_path)
    dep = _client(deps)
    assert dep["version"] == "1.4.2"
    assert dep["version_kind"] == "resolved"
    assert dep["resolution"] == "literal"


def test_a_property_in_the_same_file_resolves(tmp_path):
    """`${{warehouse.version}}` was recorded verbatim as though it were a version."""
    deps = resolve_pom_dependencies(_pom(tmp_path, _CONSUMER_PROPERTY), tmp_path)
    dep = _client(deps)
    assert dep["version"] == "1.4.2"
    assert dep["resolution"] == "property"


def test_a_managed_version_resolves_and_the_bom_is_not_a_dependency(tmp_path):
    """`<dependencyManagement>` constrains versions; it does not declare a dependency.

    Reading it as `<dependencies>` emitted `platform-bom` as an edge to an
    artefact the code never calls — a false cross-repo relationship, which is the
    class of error `OI-1` and `OI-7` were about.
    """
    deps = resolve_pom_dependencies(_pom(tmp_path, _CONSUMER_BOM), tmp_path)
    assert [d["artifactId"] for d in deps] == ["warehouse-client"]
    assert _client(deps)["version"] == "1.4.2"


def test_a_parent_in_the_same_repo_resolves(tmp_path):
    """Multi-module projects keep the parent alongside; a plain file read."""
    _pom(tmp_path, f"""<project{_NS}>
      <groupId>com.example</groupId><artifactId>platform-parent</artifactId>
      <version>7.2.0</version>
      <properties><warehouse.version>1.4.2</warehouse.version></properties>
    </project>""")
    child = _pom(tmp_path, f"""<project{_NS}>
      <parent><groupId>com.example</groupId><artifactId>platform-parent</artifactId>
        <version>7.2.0</version></parent>
      <artifactId>consumer</artifactId>
      <dependencies><dependency>
        <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
        <version>${{warehouse.version}}</version>
      </dependency></dependencies>
    </project>""", name="module/pom.xml")

    dep = _client(resolve_pom_dependencies(child, tmp_path))
    assert dep["version"] == "1.4.2"
    assert dep["resolution"] == "parent-in-repo"


def test_a_parent_in_another_scanned_repo_resolves(tmp_path):
    """The fleet is the artifact repository, for the coordinates we care about.

    No `mvn`, no registry, no downloads — the parent is a file in a repo we have
    already cloned, and the identity index already knows where.
    """
    repos = tmp_path / "repos"
    _pom(repos, f"""<project{_NS}>
      <groupId>com.example</groupId><artifactId>platform-parent</artifactId>
      <version>7.2.0</version>
      <properties><warehouse.version>1.4.2</warehouse.version></properties>
    </project>""", name="platform/platform-parent/pom.xml")
    consumer_root = repos / "fulfilment" / "fulfilment-commons"
    child = _pom(repos, f"""<project{_NS}>
      <parent><groupId>com.example</groupId><artifactId>platform-parent</artifactId>
        <version>7.2.0</version></parent>
      <artifactId>fulfilment-commons</artifactId>
      <dependencies><dependency>
        <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
        <version>${{warehouse.version}}</version>
      </dependency></dependencies>
    </project>""", name="fulfilment/fulfilment-commons/pom.xml")

    dep = _client(resolve_pom_dependencies(child, consumer_root, fleet_root=repos))
    assert dep["version"] == "1.4.2"
    assert dep["resolution"] == "parent-in-fleet"
    # The imprecision is labelled, not hidden: the sibling repo is at HEAD, not
    # necessarily at the version this consumer pins.
    assert dep["parent_resolved_at"] == "head"


def test_an_external_parent_is_unresolved_not_guessed(tmp_path):
    """`spring-boot-starter-parent` is not in the fleet, and we do not invent it."""
    child = _pom(tmp_path, f"""<project{_NS}>
      <parent><groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId><version>3.2.0</version></parent>
      <artifactId>consumer</artifactId>
      <dependencies><dependency>
        <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
        <version>${{warehouse.version}}</version>
      </dependency></dependencies>
    </project>""")
    dep = _client(resolve_pom_dependencies(child, tmp_path))
    assert dep["version_kind"] == "unresolved"
    assert dep["resolution"] == "unresolved"
    assert "${" not in dep["version"]


def test_an_unresolved_version_is_never_recorded_as_a_placeholder(tmp_path):
    """A consumer must tell "1.4.2" from "we could not work it out"."""
    child = _pom(tmp_path, f"""<project{_NS}>
      <groupId>com.example</groupId><artifactId>consumer</artifactId><version>1.0.0</version>
      <dependencies><dependency>
        <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
        <version>${{nowhere.defined}}</version>
      </dependency></dependencies>
    </project>""")
    dep = _client(resolve_pom_dependencies(child, tmp_path))
    assert dep["version"] == ""
    assert dep["version_kind"] == "unresolved"


def test_a_property_cycle_terminates(tmp_path):
    """`${a}` -> `${b}` -> `${a}` must stop rather than spin."""
    child = _pom(tmp_path, f"""<project{_NS}>
      <groupId>com.example</groupId><artifactId>consumer</artifactId><version>1.0.0</version>
      <properties><a>${{b}}</a><b>${{a}}</b></properties>
      <dependencies><dependency>
        <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
        <version>${{a}}</version>
      </dependency></dependencies>
    </project>""")
    dep = _client(resolve_pom_dependencies(child, tmp_path))
    assert dep["version_kind"] == "unresolved"


def test_a_property_chain_resolves(tmp_path):
    """`${a}` -> `${b}` -> literal is ordinary in real projects."""
    child = _pom(tmp_path, f"""<project{_NS}>
      <groupId>com.example</groupId><artifactId>consumer</artifactId><version>1.0.0</version>
      <properties><a>${{b}}</a><b>1.4.2</b></properties>
      <dependencies><dependency>
        <groupId>com.example.commerce</groupId><artifactId>warehouse-client</artifactId>
        <version>${{a}}</version>
      </dependency></dependencies>
    </project>""")
    assert _client(resolve_pom_dependencies(child, tmp_path))["version"] == "1.4.2"


def test_a_bare_project_element_still_parses():
    """Namespaces are matched, not required — a POM without them must still work."""
    import tempfile

    root = Path(tempfile.mkdtemp())
    pom = _pom(root, _CONSUMER_LITERAL.replace(_NS, ""))
    assert _client(resolve_pom_dependencies(pom, root))["version"] == "1.4.2"
