"""Unit tests for repo_utils parsing/detection helpers."""

from __future__ import annotations

import json

import pytest

from src2sink import internal_groups, repo_utils as ru


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_safe_read_text_size_and_missing(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    assert ru.safe_read_text(f) == "hello"
    assert ru.safe_read_text(tmp_path / "missing.txt") is None
    monkeypatch.setattr(ru, "MAX_FILE_BYTES", 2)
    assert ru.safe_read_text(f) is None  # over cap


def test_oversized_reads_are_size_gated(tmp_path, monkeypatch):
    """TA-006 / SAST finding 3 (SEC-1/D-4): manifest/git reads must honour MAX_FILE_BYTES.

    A hostile repo can ship a huge .git/HEAD, gradle file, or package.json; these
    read paths previously bypassed the cap that safe_read_text applies elsewhere.
    """
    monkeypatch.setattr(ru, "MAX_FILE_BYTES", 64)
    big = "x" * 5000

    # detect_git_sha: an oversized .git/HEAD is skipped, not read into memory.
    _w(tmp_path / ".git" / "HEAD", big)
    assert ru.detect_git_sha(tmp_path) is None

    # gradle settings / properties / build script all skip oversized files.
    _w(tmp_path / "settings.gradle", f'rootProject.name = "x"\n{big}')
    assert ru._read_gradle_settings(tmp_path / "settings.gradle") == (None, [])
    _w(tmp_path / "gradle.properties", f"group=com.x\n{big}")
    assert ru._read_gradle_group(tmp_path) is None

    # _index_npm skips an oversized package.json rather than json.loads-ing it.
    npm: dict[str, str] = {}
    _w(tmp_path / "a" / "b" / "package.json", '{"name": "pkg"}' + " " + big)
    ru._index_npm(tmp_path, npm)
    assert "pkg" not in npm


def test_is_internal_coordinate():
    internal_groups.configure_internal_group_patterns([r"^com\.acme(\..+)?$", r"^@acme(/.+)?$"])
    try:
        assert ru.is_internal_coordinate("com.acme.foo", "bar")
        assert ru.is_internal_coordinate(None, "@acme/x")
        assert not ru.is_internal_coordinate("org.other", "lib")
    finally:
        internal_groups.INTERNAL_GROUP_PATTERNS = []  # reset (empty rejected by configure)


def test_parse_pom_dependencies(tmp_path):
    _w(tmp_path / "pom.xml",
       "<project xmlns=\"http://maven\"><dependencies><dependency>"
       "<groupId>g</groupId><artifactId>a</artifactId><version>1</version>"
       "</dependency></dependencies></project>")
    deps = ru.parse_pom_dependencies(tmp_path / "pom.xml")
    assert deps and deps[0]["artifactId"] == "a"
    assert ru.parse_pom_dependencies(tmp_path / "nope.xml") == []


def test_parse_package_json_dependencies(tmp_path):
    _w(tmp_path / "package.json", json.dumps({
        "dependencies": {"@acme/x": "1.0.0", "left-pad": "^2"},
        "devDependencies": {"jest": "1"},
    }))
    deps = ru.parse_package_json_dependencies(tmp_path / "package.json")
    arts = {d["artifactId"] for d in deps}
    assert "@acme/x" in arts and "left-pad" in arts
    assert ru.parse_package_json_dependencies(tmp_path / "bad.json") == []


def test_classify_frameworks():
    coords = [
        {"groupId": "org.springframework.boot", "artifactId": "spring-boot-starter"},
        {"groupId": "", "artifactId": "react"},
    ]
    fw = ru.classify_frameworks(coords)
    assert isinstance(fw, list)


def test_detect_build_systems(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("", encoding="utf-8")
    systems = ru.detect_build_systems(tmp_path)
    assert "maven" in systems and "npm" in systems and "gradle" in systems
    assert ru.detect_build_systems(tmp_path / "empty") == []


def test_gradle_settings_and_group(tmp_path):
    _w(tmp_path / "settings.gradle",
       "rootProject.name = 'my-root'\ninclude 'mod-a', 'mod-b'\n")
    _w(tmp_path / "build.gradle", "group = 'com.acme'\n// comment\n")
    name, includes = ru._read_gradle_settings(tmp_path / "settings.gradle")
    assert name == "my-root"
    assert "mod-a" in includes and "mod-b" in includes
    assert ru._read_gradle_group(tmp_path) == "com.acme"


def test_strip_gradle_comments_and_xmlns():
    assert "secret" not in ru._strip_gradle_comments("code // secret\n/* block */rest")
    assert ru._strip_xmlns("{ns}tag") == "tag"
    assert ru._strip_xmlns("plain") == "plain"


@pytest.mark.parametrize(
    ("filename", "text", "reader", "expected_name"),
    [
        ("pyproject.toml", '[project]\nname = "acme-py"\n', "_read_pyproject_identity", "acme-py"),
        ("pyproject.toml", '[tool.poetry]\nname = "acme-poetry"\n', "_read_pyproject_identity", "acme-poetry"),
        ("setup.cfg", "[metadata]\nname = acme-cfg\n", "_read_setup_cfg_identity", "acme-cfg"),
        ("Cargo.toml", '[package]\nname = "acme-rs"\n', "_read_cargo_identity", "acme-rs"),
        ("composer.json", '{"name": "vendor/pkg"}', "_read_composer_identity", "pkg"),
        ("go.mod", "module github.com/acme/thing\n", "_read_gomod_identity", "thing"),
        ("Svc.csproj", "<Project><PropertyGroup><PackageId>Acme.Svc</PackageId></PropertyGroup></Project>", "_read_dotnet_project_identity", "Acme.Svc"),
        ("acme.gemspec", "Gem::Specification.new do |s|\n s.name = 'acme_gem'\nend", "_read_gemspec_identity", "acme_gem"),
    ],
)
def test_identity_readers(tmp_path, filename, text, reader, expected_name):
    p = tmp_path / filename
    p.write_text(text, encoding="utf-8")
    ident = getattr(ru, reader)(p)
    assert ident is not None and ident[1] == expected_name


def test_identity_readers_bad_input(tmp_path):
    (tmp_path / "pyproject.toml").write_text("not = = toml [[[", encoding="utf-8")
    assert ru._read_pyproject_identity(tmp_path / "pyproject.toml") is None
    (tmp_path / "composer.json").write_text("{bad", encoding="utf-8")
    assert ru._read_composer_identity(tmp_path / "composer.json") is None
    assert ru._read_gomod_identity(tmp_path / "missing.mod") is None
