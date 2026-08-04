"""TA-011 — dependency pin/hash + audit tests (threat-model SC-1).

`extractors/base.py` loads tree-sitter grammars as native code; a compromised
or vulnerable transitive dependency is a supply-chain path into every worker
process. The two controls are: (1) a hash-pinned lockfile, so installs are
reproducible and cannot silently pull a tampered package, and (2) a
vulnerability audit (`pip-audit`) run against that lockfile.

The gap analysis scopes TA-011 as a *CI* check ("CI runs pip-audit; lockfile
hashes present"), not a pure unit test — auditing needs a live query against
an advisory index. This module verifies the parts that are checkable without
a network call unconditionally (the lockfile itself, and that `pip-audit` is
wired up as an available dev tool), and additionally *runs* pip-audit when
network egress to the advisory index is available, skipping cleanly rather
than failing when it is not (e.g. behind a restrictive corporate proxy).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "uv.lock"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _lockfile_digest() -> str:
    """Return a digest of the lockfile, for detecting an accidental rewrite."""
    return hashlib.sha256(LOCKFILE.read_bytes()).hexdigest()


def _load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_lockfile_exists():
    assert LOCKFILE.is_file(), "uv.lock is required for reproducible, hash-pinned installs"


def _package_is_hash_pinned(package: dict) -> bool:
    """True if a locked package's sdist/wheel entries all carry a hash."""
    source = package.get("source", {})
    sdist = package.get("sdist")
    wheels = package.get("wheels") or []
    if not sdist and not wheels:
        # A local/editable/virtual/git source has nothing external to pin.
        return bool({"editable", "virtual", "directory", "git"} & source.keys())
    if sdist and "hash" not in sdist:
        return False
    return all("hash" in wheel for wheel in wheels)


def test_every_locked_package_is_hash_pinned():
    lock = _load_toml(LOCKFILE)
    packages = lock.get("package", [])
    assert packages, "uv.lock has no [[package]] entries"
    unpinned = [p["name"] for p in packages if not _package_is_hash_pinned(p)]
    assert not unpinned, f"packages locked without a hash (supply-chain risk): {unpinned}"


def test_pip_audit_declared_as_dev_dependency():
    """pip-audit must be installable so CI can actually run the TA-011 gate."""
    proj = _load_toml(PYPROJECT)
    dev_deps = proj.get("dependency-groups", {}).get("dev", [])
    assert any(dep.split(">")[0].split("=")[0].strip() == "pip-audit" for dep in dev_deps), (
        "pip-audit is not declared under [dependency-groups.dev] in pyproject.toml"
    )


@pytest.mark.no_watchdog  # a live advisory-index query can legitimately run past the default budget
def test_pip_audit_reports_no_known_vulnerabilities():
    """Run pip-audit against this project's locked dependencies.

    Skips (rather than fails) when the advisory index is unreachable — this
    sandbox sits behind a proxy that cannot verify pypi.org's certificate — so
    the check is a hard gate wherever it *can* run (a real CI runner with
    normal internet egress) without making the offline dev loop flaky.

    ``uv run`` is invoked with ``--frozen``: without it, uv re-resolves the
    project first and rewrites every entry in ``uv.lock`` to whatever index the
    developer's ``UV_INDEX`` / ``UV_DEFAULT_INDEX`` points at. On a machine
    configured for an internal mirror that silently replaced all 900+
    ``pypi.org`` URLs with internal hostnames — a dirty working tree after a
    read-only test run, and internal infrastructure names staged into a public
    repository. The hashes are preserved either way, so the substitution is easy
    to miss in review. ``--frozen`` also means the audit is run against the
    lockfile as committed, which is what TA-011 is actually asserting.
    """
    if shutil.which("uv") is not None:
        cmd = ["uv", "run", "--frozen", "pip-audit", "--progress-spinner=off"]
    elif shutil.which("pip-audit") is not None:
        cmd = ["pip-audit", "--progress-spinner=off"]
    else:
        pytest.skip("neither uv nor pip-audit is available on PATH")
        return

    before = _lockfile_digest()
    try:
        result = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("pip-audit did not complete within 120s (no network egress?)")
        return
    finally:
        # Guard the invariant rather than trusting the flag to keep working: an
        # auditing test must never mutate the thing it audits.
        assert _lockfile_digest() == before, (
            f"{LOCKFILE.name} was rewritten by the audit subprocess — check that "
            "'uv run --frozen' is still being used; a re-resolve rewrites every "
            "package URL to the locally configured index"
        )

    network_error_markers = (
        "SSLError", "ConnectionError", "Max retries exceeded",
        "NameResolutionError", "ConnectTimeout",
    )
    if result.returncode != 0 and any(m in result.stderr for m in network_error_markers):
        pytest.skip(
            "pip-audit could not reach the advisory index in this environment: "
            f"{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'unknown error'}"
        )
        return

    assert result.returncode == 0, f"pip-audit found issues:\n{result.stdout}\n{result.stderr}"


# --------------------------------------------------------------------------
# OI-12 — every runtime dependency must actually be used
# --------------------------------------------------------------------------

# Distribution name -> the module name it provides, where the two differ.
_DIST_TO_MODULE = {
    "tree-sitter": "tree_sitter",
    "tree-sitter-go": "tree_sitter_go",
    "tree-sitter-java": "tree_sitter_java",
    "tree-sitter-javascript": "tree_sitter_javascript",
    "tree-sitter-kotlin": "tree_sitter_kotlin",
    "tree-sitter-python": "tree_sitter_python",
    "tree-sitter-typescript": "tree_sitter_typescript",
}


def _declared_runtime_distributions() -> list[str]:
    """Return the distribution names in [project.dependencies], without version specs."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    out = []
    for spec in data["project"]["dependencies"]:
        name = spec.split(">=")[0].split("==")[0].split("[")[0].strip()
        out.append(name)
    return out


def test_every_runtime_dependency_is_imported_somewhere() -> None:
    """A declared runtime dependency that nothing imports is gratuitous surface.

    `src2sink` is installed into other people's environments, so each runtime
    dependency is audited supply-chain surface (SC-1 / TA-011) and licence
    surface for every consumer. Four unused ones — ipykernel, jupyterlab,
    openpyxl and pandas — pulled the installed closure from 8 packages to 68,
    and were the sole source of every non-permissive licence in the tree
    (OI-12). This asserts they cannot come back unnoticed.
    """
    # This test reads the source tree rather than behaviour, so it needs the
    # *whole* tree. A scoped mutation run copies only the subtree it mutates,
    # and every import outside it then looks missing. Detect that and skip
    # rather than report a dependency as unused.
    if not (REPO_ROOT / "src2sink" / "repo_utils.py").is_file():
        pytest.skip("partial source tree (e.g. a scoped mutation run)")

    sources = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (REPO_ROOT / "src2sink").rglob("*.py")
    )
    unused = [
        dist
        for dist in _declared_runtime_distributions()
        if _DIST_TO_MODULE.get(dist, dist.replace("-", "_")) not in sources
    ]
    assert not unused, (
        f"declared as runtime dependencies but never imported under src2sink/: {unused}. "
        "Move them to [dependency-groups] dev, or to an optional extra if a "
        "feature needs them — the default install must stay minimal."
    )
