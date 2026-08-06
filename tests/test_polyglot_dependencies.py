"""OI-19: dependency parsing beyond Java, and lockfiles before manifests.

The tool recognises nine ecosystems for *identity* and parses dependencies for
two. So `dependencies_internal: []` on a Go or Python repo means "not
implemented", and is indistinguishable from "no internal dependencies" — the
silent-failure shape §6 of the open-issues document names.

Two assumptions were wrong. That **Maven is representative**: it is the hardest
ecosystem, needing the inheritance chasing of `OI-18`, while Go states exact
versions outright and npm/Python commit a lockfile that holds the resolved
answer. And that **a manifest states a version**: npm and Python manifests state
*ranges*, so `^1.4.2` names a set, not a version.

The rule is therefore lockfile first, manifest second, and a range recorded *as*
a range rather than as though it were a version — which would repeat `OI-18` in
a new ecosystem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src2sink.build_metabase_v2 import _collect_dependencies
from src2sink.internal_groups import configure_internal_group_patterns


@pytest.fixture(autouse=True)
def _internal_patterns():
    """Treat the fixture organisation's coordinates as internal."""
    configure_internal_group_patterns([
        r"^com\.example(\..+)?$",
        r"^github\.com/example(/.+)?$",
        r"^@example(/.+)?$",
        r"^example-.+$",
    ])
    yield
    configure_internal_group_patterns([r"^com\.example(\..+)?$"])


def _deps(tmp_path: Path, files: dict[str, str]) -> list[dict[str, str]]:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return _collect_dependencies(tmp_path)[0]


def test_go_module_requirements_are_parsed(tmp_path):
    """`go.mod` states exact versions with no ranges and no inheritance.

    The cheapest coverage available, and unparsed until now — a Go service with a
    dozen internal dependencies reported the same empty list as one with none.
    """
    deps = _deps(tmp_path, {"go.mod": """
module github.com/example/fulfilment

go 1.22

require (
	github.com/example/warehouse-client v1.4.2
	github.com/spf13/cobra v1.8.0
)

require github.com/example/audit-lib v0.9.1 // indirect
"""})
    found = {(d["artifactId"], d["version"]) for d in deps}
    assert ("github.com/example/warehouse-client", "v1.4.2") in found
    assert ("github.com/example/audit-lib", "v0.9.1") in found
    assert ("github.com/spf13/cobra", "v1.8.0") in found


def test_go_dependencies_are_classified_internal_or_external(tmp_path):
    """The internal test must reach a Go module path, not only a Maven coordinate."""
    deps = _deps(tmp_path, {"go.mod": """
module github.com/example/svc
require (
	github.com/example/warehouse-client v1.4.2
	github.com/spf13/cobra v1.8.0
)
"""})
    kinds = {d["artifactId"]: d["kind"] for d in deps}
    assert kinds["github.com/example/warehouse-client"] == "internal"
    assert kinds["github.com/spf13/cobra"] == "external"


def test_a_python_lockfile_gives_the_resolved_version(tmp_path):
    """The lockfile is the effective resolution, committed and exact."""
    deps = _deps(tmp_path, {
        "pyproject.toml": """
[project]
name = "fulfilment"
dependencies = ["example-warehouse-client>=1.4,<2", "requests>=2"]
""",
        "uv.lock": """
version = 1

[[package]]
name = "example-warehouse-client"
version = "1.4.2"

[[package]]
name = "requests"
version = "2.31.0"
""",
    })
    found = {d["artifactId"]: d for d in deps}
    assert found["example-warehouse-client"]["version"] == "1.4.2"
    assert found["example-warehouse-client"]["version_kind"] == "resolved"


def test_a_python_manifest_without_a_lockfile_records_a_range(tmp_path):
    """A range is not a version, and must not be recorded as though it were."""
    deps = _deps(tmp_path, {"pyproject.toml": """
[project]
name = "fulfilment"
dependencies = ["example-warehouse-client>=1.4,<2"]
"""})
    dep = next(d for d in deps if d["artifactId"] == "example-warehouse-client")
    assert dep["version_kind"] == "range"
    assert dep["version"] == ">=1.4,<2"


def test_an_npm_lockfile_beats_the_manifest_range(tmp_path):
    """`package.json` states `^1.4.2`; the lockfile says which version that became."""
    deps = _deps(tmp_path, {
        "package.json": """
{"name": "fulfilment", "dependencies": {"@example/warehouse-client": "^1.4.2"}}
""",
        "package-lock.json": """
{"name": "fulfilment", "lockfileVersion": 3, "packages": {
  "node_modules/@example/warehouse-client": {"version": "1.4.7"}
}}
""",
    })
    dep = next(d for d in deps if d["artifactId"] == "@example/warehouse-client")
    assert dep["version"] == "1.4.7"
    assert dep["version_kind"] == "resolved"


def test_an_npm_manifest_alone_records_the_range(tmp_path):
    """Without a lockfile the honest record is the constraint, marked as one."""
    deps = _deps(tmp_path, {"package.json": """
{"name": "fulfilment", "dependencies": {"@example/warehouse-client": "^1.4.2"}}
"""})
    dep = next(d for d in deps if d["artifactId"] == "@example/warehouse-client")
    assert dep["version_kind"] == "range"
    assert dep["version"] == "^1.4.2"


def test_an_unparsed_ecosystem_is_distinguishable_from_an_empty_one(tmp_path):
    """`[]` must not mean both "no dependencies" and "we cannot read this".

    Rust, PHP, .NET and Ruby remain identity-only. That is a deliberate stop, but
    the record has to say so — otherwise a reviewer reads a clean result.
    """
    _, notes = _collect_dependencies(tmp_path)
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "svc"\n\n[dependencies]\nserde = "1"\n', encoding="utf-8"
    )
    _, notes_rust = _collect_dependencies(tmp_path)
    assert notes == []
    assert any("Cargo.toml" in n or "rust" in n.lower() for n in notes_rust)
