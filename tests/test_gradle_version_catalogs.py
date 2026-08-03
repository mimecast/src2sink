"""Regression tests for OI-3 — Gradle version catalogs must be parsed.

`_GRADLE_DEP_RX` only recognises an inline coordinate string. Version-catalog
usage is an accessor reference with no coordinate in the build file at all:

    implementation(libs.warehouseServiceClient)

and the file that *does* hold the coordinate — `gradle/libs.versions.toml`, or a
`library(...)` call in `settings.gradle.kts` — was never read. A repo whose
imports plainly show it consuming an internal client library therefore reported
`dependencies_internal: []`.

That empty list is the input to api-client discovery, so those repos contribute
no candidates at all. It is the same failure shape as the empty-bindings defect
fixed in 1.1.0: **a detection input degrading to empty without saying so**, which
is why the unresolved case now emits a repo note rather than silently returning
nothing.

Names follow the sanitised placeholder set used across the suite.
"""

from __future__ import annotations

import pytest

from src2sink import build_metabase_v2 as b
from src2sink.internal_groups import (
    DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS,
    configure_internal_group_patterns,
)

CATALOG_TOML = """
[versions]
warehouse = "3.0.2"

[libraries]
warehouse-service-client = { module = "com.example.commerce.warehouse:warehouse-service-client", version.ref = "warehouse" }
commons-lang = { module = "org.apache.commons:commons-lang3", version = "3.14.0" }
"""

SETTINGS_DSL = """
dependencyResolutionManagement {
    versionCatalogs {
        create("libs") {
            library("warehouseServiceClient", "com.example.commerce.warehouse", "warehouse-service-client")
                .version("3.0.2")
        }
    }
}
"""

BUILD_GRADLE_KTS = """
dependencies {
    implementation(libs.warehouseServiceClient)
    implementation(libs.commonsLang)
}
"""


@pytest.fixture
def internal_groups():
    """Treat com.example.commerce.* as internal for the duration of one test.

    Restores the defaults on teardown rather than clearing: an empty pattern set
    is rejected outright, since a scan with no internal-group rule silently
    classifies every dependency as external.
    """
    configure_internal_group_patterns([r"^com\.example\.commerce\."])
    yield
    configure_internal_group_patterns(DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS)


def _repo(tmp_path, files: dict[str, str]):
    """Materialise a repo with the given relative-path -> content mapping."""
    root = tmp_path / "repo"
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# Catalog parsing
# --------------------------------------------------------------------------

def test_parse_version_catalog_toml(tmp_path) -> None:
    """The `[libraries]` table maps an alias to its module coordinate."""
    root = _repo(tmp_path, {"gradle/libs.versions.toml": CATALOG_TOML})
    catalog = b._parse_version_catalog(root)
    assert catalog["warehouseserviceclient"] == (
        "com.example.commerce.warehouse", "warehouse-service-client",
    )
    assert catalog["commonslang"] == ("org.apache.commons", "commons-lang3")


def test_parse_version_catalog_settings_dsl(tmp_path) -> None:
    """A catalog declared inline in settings.gradle.kts resolves the same way."""
    root = _repo(tmp_path, {"settings.gradle.kts": SETTINGS_DSL})
    catalog = b._parse_version_catalog(root)
    assert catalog["warehouseserviceclient"] == (
        "com.example.commerce.warehouse", "warehouse-service-client",
    )


def test_parse_version_catalog_is_empty_without_a_catalog(tmp_path) -> None:
    """No catalog file means an empty map, not an error."""
    root = _repo(tmp_path, {"build.gradle.kts": BUILD_GRADLE_KTS})
    assert b._parse_version_catalog(root) == {}


@pytest.mark.parametrize(
    "alias",
    ["warehouse-service-client", "warehouse.service.client", "warehouseServiceClient",
     "warehouse_service_client", "WAREHOUSE-SERVICE-CLIENT"],
)
def test_normalise_alias_is_case_and_separator_insensitive(alias: str) -> None:
    """Gradle exposes `my-lib` / `my.lib` in the catalog as `libs.myLib`.

    The catalog alias and the accessor spelling differ by both case and
    separator, so the lookup key has to discard both or nothing resolves.
    """
    assert b._normalise_alias(alias) == "warehouseserviceclient"


# --------------------------------------------------------------------------
# End-to-end dependency collection
# --------------------------------------------------------------------------

def test_collect_dependencies_resolves_a_libs_reference(tmp_path, internal_groups) -> None:
    """OI-3: `implementation(libs.warehouseServiceClient)` becomes a real coordinate."""
    root = _repo(tmp_path, {
        "gradle/libs.versions.toml": CATALOG_TOML,
        "build.gradle.kts": BUILD_GRADLE_KTS,
    })
    deps, _notes = b._collect_dependencies(root)
    by_artifact = {d["artifactId"]: d for d in deps}
    assert "warehouse-service-client" in by_artifact, (
        f"catalog reference unresolved; got {sorted(by_artifact)}"
    )
    resolved = by_artifact["warehouse-service-client"]
    assert resolved["groupId"] == "com.example.commerce.warehouse"
    assert resolved["kind"] == "internal"


def test_catalog_resolution_applies_the_normal_internal_classification(
    tmp_path, internal_groups,
) -> None:
    """A resolved third-party coordinate is still external — the rule is unchanged."""
    root = _repo(tmp_path, {
        "gradle/libs.versions.toml": CATALOG_TOML,
        "build.gradle.kts": BUILD_GRADLE_KTS,
    })
    deps, _notes = b._collect_dependencies(root)
    external = {d["artifactId"] for d in deps if d["kind"] == "external"}
    assert "commons-lang3" in external


def test_inline_coordinates_still_parse_alongside_a_catalog(tmp_path, internal_groups) -> None:
    """Adding catalog support must not disturb the inline-coordinate path."""
    root = _repo(tmp_path, {
        "gradle/libs.versions.toml": CATALOG_TOML,
        "build.gradle.kts": BUILD_GRADLE_KTS + """
dependencies {
    implementation("com.example.commerce.stock:stock-client:1.2.3")
}
""",
    })
    deps, _notes = b._collect_dependencies(root)
    artifacts = {d["artifactId"] for d in deps}
    assert {"stock-client", "warehouse-service-client"} <= artifacts


# --------------------------------------------------------------------------
# The silent-empty case must announce itself (cross-cutting §6)
# --------------------------------------------------------------------------

def test_unresolved_catalog_reference_is_reported(tmp_path, internal_groups) -> None:
    """`libs.` references with no catalog to resolve them must not fail silently.

    This is the shape the 1.1.0 work set out to eliminate: a detection input
    degrading to empty with nothing in the output saying so. A count of zero is
    a finding; an absent field is not.
    """
    root = _repo(tmp_path, {"build.gradle.kts": BUILD_GRADLE_KTS})
    deps, notes = b._collect_dependencies(root)
    assert deps == []
    assert any("catalog" in n.lower() for n in notes), f"no note explaining the empty result: {notes}"
    assert any("2" in n for n in notes), f"note should count the unresolved references: {notes}"


def test_no_note_when_there_are_no_catalog_references(tmp_path, internal_groups) -> None:
    """A repo that never mentions `libs.` has nothing to explain."""
    root = _repo(tmp_path, {
        "build.gradle.kts": 'dependencies { implementation("g:a:1.0") }',
    })
    _deps, notes = b._collect_dependencies(root)
    assert notes == []


def test_note_reaches_the_repo_summary(tmp_path, internal_groups) -> None:
    """The note has to survive into the persisted record, not just the return value."""
    root = _repo(tmp_path, {"build.gradle.kts": BUILD_GRADLE_KTS})
    summary = b.analyse_repo_v2(root, "fulfilment", "catalog-consumer", "fulfilment/catalog-consumer")
    assert any("catalog" in n.lower() for n in summary.notes), summary.notes


def test_fixture_repo_reports_its_internal_dependency(internal_groups) -> None:
    """The committed fixture repo must resolve its catalog reference end to end.

    `dependencies_internal` being non-empty *is* the point of OI-3: that list is
    the sole input to api-client discovery, so a repo whose only dependency
    declaration is a catalog alias contributed no candidates at all.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent / "fixtures/synthetic-repos/fulfilment/catalog-consumer"
    summary = b.analyse_repo_v2(root, "fulfilment", "catalog-consumer", "fulfilment/catalog-consumer")
    internal = {d["artifactId"] for d in summary.dependencies_internal}
    assert "warehouse-service-client" in internal, summary.dependencies_internal
    assert not any("catalog" in n.lower() for n in summary.notes), (
        f"resolution succeeded, so there is nothing to warn about: {summary.notes}"
    )
