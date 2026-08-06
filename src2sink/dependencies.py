"""Declared dependencies, per ecosystem, resolved as far as offline data allows.

Three rules run through everything here, and they are the corrections `OI-18` and
`OI-19` were raised for.

**Lockfile before manifest.** A committed lockfile *is* the effective
resolution — exact, offline, no registry. Maven needs inheritance chasing only
because it has no lockfile convention; treating it as the representative case is
what left Go and Python unparsed while the hard ecosystem got the attention.

**A range is not a version.** `^1.4.2` names a set. Where no lockfile exists the
honest record is the constraint, marked as one, so nothing downstream compares it
as though it were a point.

**An absent parse is not an empty result.** A repo in an unparsed ecosystem must
say so, or `dependencies_internal: []` reads as "no internal dependencies".
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .repo_utils import is_internal_coordinate, safe_read_text

# Ecosystems recognised for *identity* but not for dependencies. Listing them
# lets a scan say so, rather than emitting an empty list that reads as a result.
UNPARSED_MANIFESTS = {
    "Cargo.toml": "rust",
    "composer.json": "php",
    "Gemfile": "ruby",
}

_GOMOD_REQUIRE_BLOCK = re.compile(r"require\s*\(([^)]{0,20000})\)", re.S)
_GOMOD_REQUIRE_LINE = re.compile(
    r"^\s*(?:require\s+)?([A-Za-z0-9._~\-]+(?:\.[A-Za-z]{2,}|)"
    r"(?:/[A-Za-z0-9._~\-]+){1,10})\s+(v[0-9][A-Za-z0-9.+\-]{0,60})",
    re.M,
)
_PY_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]{0,120})\s*([<>=!~^][^;#]{0,120})?"
)


def _dep(name: str, version: str, kind_of_version: str, *, group: str = "") -> dict[str, str]:
    """Build one dependency record, classified internal or external."""
    return {
        "groupId": group,
        "artifactId": name,
        "version": version,
        "version_kind": kind_of_version,
        "kind": "internal" if is_internal_coordinate(group or name, name) else "external",
    }


def parse_go_mod(path: Path) -> list[dict[str, str]]:
    """Parse ``require`` entries from a ``go.mod``.

    The easiest ecosystem in the set and the one that was missing: Go states
    exact versions with no ranges, no properties and no inheritance, because
    minimal version selection resolves at build time from these values alone.
    Indirect requirements are included — a transitive internal dependency is
    still a dependency on an internal component.
    """
    text = safe_read_text(path)
    if not text:
        return []
    # `require (...)` blocks and single-line `require x v1` both appear.
    bodies = _GOMOD_REQUIRE_BLOCK.findall(text)
    bodies.extend(
        line for line in text.splitlines() if line.strip().startswith("require ")
    )
    deps: list[dict[str, str]] = []
    for body in bodies:
        for module, version in _GOMOD_REQUIRE_LINE.findall(body):
            deps.append(_dep(module, version, "resolved"))
    return deps


def _python_lock_versions(repo_root: Path) -> dict[str, str]:
    """Map distribution name to resolved version from any committed Python lockfile."""
    for name in ("uv.lock", "poetry.lock"):
        text = safe_read_text(repo_root / name)
        if not text:
            continue
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            continue
        return {
            str(pkg.get("name", "")): str(pkg.get("version", ""))
            for pkg in data.get("package", [])
            if pkg.get("name")
        }
    return {}


def parse_python_dependencies(repo_root: Path) -> list[dict[str, str]]:
    """Parse Python dependencies, preferring a lockfile's resolved versions.

    ``pyproject.toml`` states ranges; the lockfile states what those ranges
    became. Where both exist the lockfile wins, and where only the manifest
    exists the range is recorded as a range.
    """
    text = safe_read_text(repo_root / "pyproject.toml")
    if not text:
        return []
    try:
        project = tomllib.loads(text).get("project") or {}
    except tomllib.TOMLDecodeError:
        return []

    locked = _python_lock_versions(repo_root)
    deps: list[dict[str, str]] = []
    for requirement in project.get("dependencies") or []:
        m = _PY_REQUIREMENT.match(str(requirement))
        if not m:
            continue
        name, spec = m.group(1), (m.group(2) or "").strip()
        if name in locked:
            deps.append(_dep(name, locked[name], "resolved"))
        else:
            deps.append(_dep(name, spec, "range" if spec else "unresolved"))
    return deps


def _npm_lock_versions(repo_root: Path) -> dict[str, str]:
    """Map package name to resolved version from ``package-lock.json``.

    Only the npm lockfile is parsed. ``yarn.lock`` and ``pnpm-lock.yaml`` use
    bespoke formats; a repo carrying one falls back to the manifest range, which
    is recorded honestly as a range rather than guessed at.
    """
    text = safe_read_text(repo_root / "package-lock.json")
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out = _npm_lock_packages(data.get("packages") or {})
    for name, entry in (data.get("dependencies") or {}).items():
        version = (entry or {}).get("version")
        if version:
            out.setdefault(name, str(version))
    return out


def _npm_lock_packages(packages: dict[str, Any]) -> dict[str, str]:
    """Read resolved versions from a lockfileVersion 2/3 ``packages`` map."""
    out: dict[str, str] = {}
    for path_key, entry in packages.items():
        if not path_key.startswith("node_modules/"):
            continue
        name = path_key.split("node_modules/", 1)[1]
        version = (entry or {}).get("version")
        if name and version:
            out[name] = str(version)
    return out


def parse_npm_dependencies(repo_root: Path, package_json: Path) -> list[dict[str, str]]:
    """Parse npm dependencies, preferring the lockfile's resolved versions."""
    text = safe_read_text(package_json)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    locked = _npm_lock_versions(repo_root)
    deps: list[dict[str, str]] = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            if name in locked:
                deps.append(_dep(name, locked[name], "resolved"))
            else:
                deps.append(_dep(name, str(spec), "range"))
    return deps


def unparsed_ecosystem_notes(repo_root: Path) -> list[str]:
    """Note any manifest whose ecosystem is recognised but not parsed.

    Without this, `dependencies_internal: []` means both "no internal
    dependencies" and "we cannot read this repo's manifest", and a reviewer has
    no way to tell which — the failure §6 of the open-issues document describes.
    """
    notes: list[str] = []
    for manifest, ecosystem in sorted(UNPARSED_MANIFESTS.items()):
        if (repo_root / manifest).is_file():
            notes.append(
                f"{manifest} present but {ecosystem} dependencies are not parsed; "
                "dependencies_internal is incomplete for this repo, not empty"
            )
    return notes


def dependency_summary(deps: list[dict[str, Any]]) -> dict[str, int]:
    """Count dependencies by how well their version is known.

    Surfaces the `OI-18` question at fleet scale: how much of the dependency data
    is a version, how much a constraint, and how much could not be worked out.
    """
    counts = {"resolved": 0, "range": 0, "unresolved": 0}
    for d in deps:
        counts[str(d.get("version_kind", "unresolved"))] = counts.get(
            str(d.get("version_kind", "unresolved")), 0
        ) + 1
    return counts
