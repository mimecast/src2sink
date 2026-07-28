"""Tests for source-map resolution from on-disk project manifests.

Covers ``repo_utils._build_component_identity_index`` and
``aggregators.library_source_map.fix_flagged_mappings`` across every supported
ecosystem.

Safety: these tests deliberately use tiny ``tmp_path`` fixtures (well under the
4-repo threshold that would engage ``mp.Pool``) and never invoke the
multiprocessing extraction path. The autouse SIGALRM watchdog and cache reset
that guard against runaway regressions now live in ``conftest.py`` and apply to
every test.
"""

from __future__ import annotations

import json
from pathlib import Path

from src2sink import repo_utils
from src2sink.aggregators.library_source_map import fix_flagged_mappings


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repos(tmp_path: Path) -> Path:
    """Build a small multi-ecosystem repos tree at ``tmp_path/repos``."""
    repos = tmp_path / "repos"

    # Maven (own group + artifact)
    _write(
        repos / "platform" / "widget-service" / "pom.xml",
        "<project><groupId>com.acme</groupId>"
        "<artifactId>widget</artifactId></project>",
    )
    # npm scoped package
    _write(
        repos / "platform" / "ui-kit" / "package.json",
        json.dumps({"name": "@acme/ui-kit", "version": "1.0.0"}),
    )
    # Python (PEP 621)
    _write(
        repos / "data" / "pyproj-lib" / "pyproject.toml",
        '[project]\nname = "acme-pytools"\n',
    )
    # Python (poetry)
    _write(
        repos / "data" / "poetry-lib" / "pyproject.toml",
        '[tool.poetry]\nname = "acme-poetry"\n',
    )
    # Python (setup.cfg)
    _write(
        repos / "data" / "cfg-lib" / "setup.cfg",
        "[metadata]\nname = acme-cfg\n",
    )
    # Rust
    _write(
        repos / "systems" / "rustcrate" / "Cargo.toml",
        '[package]\nname = "acme-rust"\nversion = "0.1.0"\n',
    )
    # Go
    _write(
        repos / "systems" / "gomod" / "go.mod",
        "module github.com/acme/gothing\n\ngo 1.22\n",
    )
    # PHP composer (vendor/pkg)
    _write(
        repos / "web" / "phpcomp" / "composer.json",
        json.dumps({"name": "acme/php-lib"}),
    )
    # .NET
    _write(
        repos / "dotnet" / "svc" / "Svc.csproj",
        "<Project><PropertyGroup><PackageId>Acme.Net.Widget</PackageId>"
        "</PropertyGroup></Project>",
    )
    # Ruby gemspec
    _write(
        repos / "ruby" / "gemproj" / "acme_gem.gemspec",
        "Gem::Specification.new do |s|\n  s.name = 'acme_gem'\nend\n",
    )
    # Gradle single-module (no settings.gradle)
    _write(
        repos / "jvm" / "gradlelib" / "build.gradle",
        "group = 'com.acme'\n",
    )
    return repos


# ---------------------------------------------------------------------------
# _build_component_identity_index
# ---------------------------------------------------------------------------


def test_identity_index_covers_all_ecosystems(tmp_path: Path) -> None:
    repos = _make_repos(tmp_path)
    by_coord, by_name, by_full = repo_utils._build_component_identity_index(repos)

    assert by_coord[("com.acme", "widget")] == "platform/widget-service"
    assert by_full["@acme/ui-kit"] == "platform/ui-kit"
    assert by_coord[("", "acme-pytools")] == "data/pyproj-lib"
    assert by_coord[("", "acme-poetry")] == "data/poetry-lib"
    assert by_coord[("", "acme-cfg")] == "data/cfg-lib"
    assert by_coord[("", "acme-rust")] == "systems/rustcrate"
    assert by_full["github.com/acme/gothing"] == "systems/gomod"
    assert by_full["acme/php-lib"] == "web/phpcomp"
    assert by_coord[("", "Acme.Net.Widget")] == "dotnet/svc"
    assert by_coord[("", "acme_gem")] == "ruby/gemproj"
    assert by_coord[("com.acme", "gradlelib")] == "jvm/gradlelib"


def test_identity_index_is_cached(tmp_path: Path) -> None:
    repos = _make_repos(tmp_path)
    first = repo_utils._build_component_identity_index(repos)
    second = repo_utils._build_component_identity_index(repos)
    # Same cached tuple object returned, not rebuilt.
    assert first is second


def test_identity_index_empty_tree(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    repos.mkdir()
    by_coord, by_name, by_full = repo_utils._build_component_identity_index(repos)
    assert by_coord == {}
    assert by_name == {}
    assert by_full == {}


def test_identity_index_ignores_malformed_manifests(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    _write(repos / "grp" / "bad-toml" / "pyproject.toml", "this is not toml = [[[")
    _write(repos / "grp" / "bad-json" / "composer.json", "{not json")
    _write(repos / "grp" / "bad-xml" / "Bad.csproj", "<Project><oops")
    # Must return without raising and without those bad entries.
    by_coord, by_name, by_full = repo_utils._build_component_identity_index(repos)
    assert ("", "acme-pytools") not in by_coord


# ---------------------------------------------------------------------------
# fix_flagged_mappings
# ---------------------------------------------------------------------------


def _mapping(coord: str, status: str, **extra) -> dict:
    return {"coordinate": coord, "status": status, **extra}


def _run_fix(tmp_path: Path, mappings: list[dict]) -> tuple[int, list[dict]]:
    repos = _make_repos(tmp_path)
    metabase = tmp_path / "metabase"
    metabase.mkdir()
    map_path = metabase / "library-source-map.json"
    map_path.write_text(json.dumps({"mappings": mappings}), encoding="utf-8")
    fixed = fix_flagged_mappings(metabase, repos)
    result = json.loads(map_path.read_text(encoding="utf-8"))["mappings"]
    return fixed, result


def test_fix_resolves_flagged_entries(tmp_path: Path) -> None:
    fixed, result = _run_fix(
        tmp_path,
        [
            _mapping("com.acme:widget", "pending"),
            _mapping("@acme/ui-kit", "ambiguous"),
            _mapping("github.com/acme/gothing", "pending"),
            _mapping("acme/php-lib", "pending"),
            _mapping("acme-pytools", "pending"),
        ],
    )
    assert fixed == 5
    by_coord = {m["coordinate"]: m for m in result}
    assert by_coord["com.acme:widget"]["clone_path"] == "platform/widget-service"
    assert by_coord["com.acme:widget"]["status"] == "cloned"
    assert by_coord["@acme/ui-kit"]["clone_path"] == "platform/ui-kit"
    assert by_coord["github.com/acme/gothing"]["clone_path"] == "systems/gomod"
    assert by_coord["acme/php-lib"]["clone_path"] == "web/phpcomp"
    assert by_coord["acme-pytools"]["clone_path"] == "data/pyproj-lib"


def test_fix_leaves_excluded_and_resolved_untouched(tmp_path: Path) -> None:
    fixed, result = _run_fix(
        tmp_path,
        [
            _mapping("com.acme:widget", "excluded"),
            _mapping("acme-rust", "cloned", clone_path="already/here"),
            _mapping("no.such:coord", "pending"),
        ],
    )
    assert fixed == 0
    by_coord = {m["coordinate"]: m for m in result}
    assert by_coord["com.acme:widget"]["status"] == "excluded"
    assert "clone_path" not in by_coord["com.acme:widget"]
    assert by_coord["acme-rust"]["clone_path"] == "already/here"
    assert by_coord["no.such:coord"]["status"] == "pending"


def test_fix_bare_name_unique_match(tmp_path: Path) -> None:
    fixed, result = _run_fix(tmp_path, [_mapping("acme-rust", "pending")])
    assert fixed == 1
    assert result[0]["clone_path"] == "systems/rustcrate"


def test_fix_no_map_file_returns_zero(tmp_path: Path) -> None:
    repos = _make_repos(tmp_path)
    metabase = tmp_path / "metabase"
    metabase.mkdir()
    assert fix_flagged_mappings(metabase, repos) == 0


def test_fix_ambiguous_bare_name_not_resolved(tmp_path: Path) -> None:
    repos = _make_repos(tmp_path)
    # Two repos both declaring the bare artifact "dup" → not uniquely resolvable.
    _write(
        repos / "a" / "one" / "pom.xml",
        "<project><groupId>com.x</groupId><artifactId>dup</artifactId></project>",
    )
    _write(
        repos / "b" / "two" / "pom.xml",
        "<project><groupId>com.y</groupId><artifactId>dup</artifactId></project>",
    )
    metabase = tmp_path / "metabase"
    metabase.mkdir()
    map_path = metabase / "library-source-map.json"
    map_path.write_text(
        json.dumps({"mappings": [_mapping("dup", "pending")]}), encoding="utf-8"
    )
    assert fix_flagged_mappings(metabase, repos) == 0
    assert json.loads(map_path.read_text())["mappings"][0]["status"] == "pending"
