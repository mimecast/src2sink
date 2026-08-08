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

from .constants import NOTE_UNPARSED_MANIFEST
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
# What the reader loses, spelled out rather than left as "parse error". A note
# saying only that something failed still leaves `dependencies_internal: []`
# ambiguous, which is the whole complaint.
_MANIFEST_CONSEQUENCE = (
    "dependencies_internal is incomplete for this repo, not empty"
)
_LOCK_CONSEQUENCE = (
    "versions fall back to the manifest's ranges, so `resolved` counts "
    "understate what is actually pinned"
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


def _unreadable(name: str, consequence: str) -> str:
    """The note for a manifest that is present but could not be read at all."""
    return (
        f"{name} is present but {NOTE_UNPARSED_MANIFEST} (unreadable or "
        f"oversized); {consequence}"
    )


def _malformed(name: str, exc: Exception, consequence: str) -> str:
    """The note for a manifest that is present, readable and not valid."""
    return (
        f"{name} is present but {NOTE_UNPARSED_MANIFEST} "
        f"({type(exc).__name__}); {consequence}"
    )


def _python_lock_versions(repo_root: Path) -> tuple[dict[str, str], list[str]]:
    """Map distribution name to resolved version from any committed Python lockfile.

    Returns the notes alongside, because a lockfile that will not parse silently
    demoted every dependency from `resolved` to `range` and nothing said so.
    """
    notes: list[str] = []
    for name in ("uv.lock", "poetry.lock"):
        path = repo_root / name
        text = safe_read_text(path)
        if not text:
            if path.is_file():
                notes.append(_unreadable(name, _LOCK_CONSEQUENCE))
            continue
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            notes.append(_malformed(name, exc, _LOCK_CONSEQUENCE))
            continue
        return {
            str(pkg.get("name", "")): str(pkg.get("version", ""))
            for pkg in data.get("package", [])
            if pkg.get("name")
        }, notes
    return {}, notes


def _read_pyproject(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Return the `[project]` table, or None with a note saying why not.

    Split out of `parse_python_dependencies` because reading-and-reporting is a
    separate concern from resolving versions, and holding both put the function
    over the complexity ratchet. `None` and `{}` are different answers: `{}` is a
    manifest with no project table, `None` is a manifest nobody could read.
    """
    notes: list[str] = []
    text = safe_read_text(path)
    if not text:
        if path.is_file():
            notes.append(_unreadable(path.name, _MANIFEST_CONSEQUENCE))
        return None, notes
    try:
        return tomllib.loads(text).get("project") or {}, notes
    except tomllib.TOMLDecodeError as exc:
        notes.append(_malformed(path.name, exc, _MANIFEST_CONSEQUENCE))
        return None, notes


def parse_python_dependencies(repo_root: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Parse Python dependencies, preferring a lockfile's resolved versions.

    ``pyproject.toml`` states ranges; the lockfile states what those ranges
    became. Where both exist the lockfile wins, and where only the manifest
    exists the range is recorded as a range.

    A malformed manifest used to return `[]`, which reads as "this repo declares
    no Python dependencies" — indistinguishable from the truth and wrong
    (`OI-18` in another place, `OI-36` in general).
    """
    project, notes = _read_pyproject(repo_root / "pyproject.toml")
    if project is None:
        return [], notes

    locked, lock_notes = _python_lock_versions(repo_root)
    notes.extend(lock_notes)
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
    return deps, notes


def _npm_lock_versions(repo_root: Path) -> tuple[dict[str, str], list[str]]:
    """Map package name to resolved version from ``package-lock.json``.

    Only the npm lockfile is parsed. ``yarn.lock`` and ``pnpm-lock.yaml`` use
    bespoke formats; a repo carrying one falls back to the manifest range, which
    is recorded honestly as a range rather than guessed at.
    """
    notes: list[str] = []
    path = repo_root / "package-lock.json"
    text = safe_read_text(path)
    if not text:
        if path.is_file():
            notes.append(_unreadable("package-lock.json", _LOCK_CONSEQUENCE))
        return {}, notes
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        notes.append(_malformed("package-lock.json", exc, _LOCK_CONSEQUENCE))
        return {}, notes
    out = _npm_lock_packages(data.get("packages") or {})
    for name, entry in (data.get("dependencies") or {}).items():
        version = (entry or {}).get("version")
        if version:
            out.setdefault(name, str(version))
    return out, notes


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


def parse_npm_dependencies(
    repo_root: Path, package_json: Path
) -> tuple[list[dict[str, str]], list[str]]:
    """Parse npm dependencies, preferring the lockfile's resolved versions.

    A malformed `package.json` returned `[]`, which is the same answer as a repo
    that genuinely declares nothing (`OI-36`).
    """
    notes: list[str] = []
    text = safe_read_text(package_json)
    if not text:
        if package_json.is_file():
            notes.append(_unreadable(package_json.name, _MANIFEST_CONSEQUENCE))
        return [], notes
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        notes.append(_malformed(package_json.name, exc, _MANIFEST_CONSEQUENCE))
        return [], notes

    locked, lock_notes = _npm_lock_versions(repo_root)
    notes.extend(lock_notes)
    deps: list[dict[str, str]] = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            if name in locked:
                deps.append(_dep(name, locked[name], "resolved"))
            else:
                deps.append(_dep(name, str(spec), "range"))
    return deps, notes


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
