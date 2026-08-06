"""Security attack tests for untrusted-repo input handling (Phase B).

Covers the design-flaw fixes from docs/threat-model.md:

* TA-002 — path containment: a crafted ``.git/HEAD`` symref and escaping file
  symlinks must not read content from outside the repository (T-1, T-2).
* TA-004 — hardened XML: an XML entity-expansion ("billion laughs") manifest
  must not expand, hang, or crash the parser (D-3).

Plus unit coverage for the ``safe_paths`` containment helpers.
"""

from __future__ import annotations

import sys

import pytest

from src2sink import repo_utils
from src2sink.build_metabase_v2 import analyse_repo_v2, iter_repo_files
from src2sink.maven import resolve_pom_dependencies
from src2sink.repo_utils import _read_pom_identity, detect_git_sha
from src2sink.safe_paths import is_escaping_symlink, is_within, resolve_within

VALID_SHA = "a" * 40  # 40-hex: a plausible git SHA used as the "secret" payload

_BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<project><dependencies><dependency>
 <groupId>&lol4;</groupId><artifactId>x</artifactId>
</dependency></dependencies></project>"""

_NORMAL_POM = (
    "<project><groupId>com.acme</groupId><artifactId>widget</artifactId>"
    "<dependencies><dependency><groupId>org.dep</groupId>"
    "<artifactId>lib</artifactId><version>1.0</version></dependency>"
    "</dependencies></project>"
)


# ---------------------------------------------------------------------------
# safe_paths unit tests
# ---------------------------------------------------------------------------


def test_resolve_within_accepts_descendant(tmp_path):
    root = tmp_path / "root"
    child = root / "a" / "b.txt"
    child.parent.mkdir(parents=True)
    child.write_text("x")
    assert resolve_within(child, root) == child.resolve()
    assert is_within(child, root)


def test_resolve_within_rejects_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    assert resolve_within(root / ".." / "outside.txt", root) is None
    assert not is_within(outside, root)


def test_resolve_within_handles_oserror(tmp_path, monkeypatch):
    from pathlib import Path

    def _boom(self, *a, **k):
        raise OSError("resolve failed")

    monkeypatch.setattr(Path, "resolve", _boom)
    assert resolve_within(tmp_path / "x", tmp_path) is None


def test_is_escaping_symlink_fails_safe_on_oserror(tmp_path, monkeypatch):
    from pathlib import Path

    def _boom(self, *a, **k):
        raise OSError("stat failed")

    monkeypatch.setattr(Path, "is_symlink", _boom)
    # An unreadable path is treated as escaping (fail-safe: skip it).
    assert is_escaping_symlink(tmp_path / "x", tmp_path) is True


def test_is_escaping_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    plain = root / "plain.txt"
    plain.write_text("ok")
    escaping = root / "evil.txt"
    escaping.symlink_to(outside)
    internal = root / "good.txt"
    internal.symlink_to(plain)
    assert is_escaping_symlink(escaping, root) is True
    assert is_escaping_symlink(plain, root) is False  # not a symlink
    assert is_escaping_symlink(internal, root) is False  # in-tree symlink


# ---------------------------------------------------------------------------
# TA-002: git-HEAD symref containment (T-1)
# ---------------------------------------------------------------------------


def _make_git(repo: repo_utils.Path, head: str) -> None:
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "HEAD").write_text(head, encoding="utf-8")


def test_detect_git_sha_blocks_traversal_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # A valid-looking SHA placed OUTSIDE the repo; only containment (not the
    # hex check) can prevent it leaking into the metabase.
    (tmp_path / "outside_sha.txt").write_text(VALID_SHA, encoding="utf-8")
    _make_git(repo, "ref: ../../outside_sha.txt")
    assert detect_git_sha(repo) is None


def test_detect_git_sha_valid_symref(tmp_path):
    repo = tmp_path / "repo"
    _make_git(repo, "ref: refs/heads/main")
    ref = repo / ".git" / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(VALID_SHA + "\n", encoding="utf-8")
    assert detect_git_sha(repo) == VALID_SHA


def test_detect_git_sha_detached_hex(tmp_path):
    repo = tmp_path / "repo"
    _make_git(repo, VALID_SHA + "\n")
    assert detect_git_sha(repo) == VALID_SHA


def test_detect_git_sha_rejects_non_hex(tmp_path):
    repo = tmp_path / "repo"
    _make_git(repo, "not-a-sha-just-text")
    assert detect_git_sha(repo) is None


# ---------------------------------------------------------------------------
# TA-002: escaping file symlinks are not scanned (T-2)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks unreliable on Windows")
def test_iter_repo_files_skips_escaping_symlink(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    real = repo / "src" / "Real.java"
    real.write_text("class Real {}", encoding="utf-8")
    secret = tmp_path / "secret.env"
    secret.write_text("PASSWORD=hunter2", encoding="utf-8")
    (repo / "src" / "Evil.java").symlink_to(secret)

    files = set(iter_repo_files(repo))
    assert real in files
    assert (repo / "src" / "Evil.java") not in files


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks unreliable on Windows")
def test_analyse_repo_does_not_ingest_symlinked_secret(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    secret = tmp_path / "creds.py"
    secret.write_text('API_TOKEN = "SUPERSECRETVALUE12345"', encoding="utf-8")
    (repo / "src" / "leak.py").symlink_to(secret)

    summary = analyse_repo_v2(repo, "grp", "repo", "grp/repo")
    blob = repr([n.detail for n in summary.nodes])
    assert "SUPERSECRETVALUE12345" not in blob


# ---------------------------------------------------------------------------
# TA-004: XML billion-laughs is neutralised (D-3)
# ---------------------------------------------------------------------------


def test_resolve_pom_dependencies_blocks_billion_laughs(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(_BILLION_LAUGHS, encoding="utf-8")
    # Must return quickly with no expansion (watchdog would fire on a hang).
    assert resolve_pom_dependencies(pom, tmp_path) == []


def test_read_pom_identity_blocks_billion_laughs(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(_BILLION_LAUGHS, encoding="utf-8")
    assert _read_pom_identity(pom) is None


def test_normal_pom_still_parses(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(_NORMAL_POM, encoding="utf-8")
    deps = resolve_pom_dependencies(pom, tmp_path)
    assert any(
        d["groupId"] == "org.dep" and d["artifactId"] == "lib" and d["version"] == "1.0"
        for d in deps
    )
    assert _read_pom_identity(pom) == ("com.acme", "widget")
