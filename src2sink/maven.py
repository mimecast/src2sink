"""Resolve Maven dependency versions offline, as far as the fleet allows.

Maven takes a version from four places and text extraction sees one, so most
declared versions were recorded as a `${property}` string or an empty string
presented as a version (`OI-18`).

Resolution here is **tiered, offline, and labelled**:

    literal | property | parent-in-repo | parent-in-fleet | unresolved

The tier that makes this work without `mvn`, a registry, or downloading a single
binary is `parent-in-fleet`. **The fleet checkout is the artifact repository, for
the coordinates we care about**: every internal repo is already cloned, and the
identity index already maps a coordinate to its clone path, so a parent POM in a
different repository is a file read.

An external parent — `spring-boot-starter-parent` — is deliberately *not*
resolved. It governs external dependency versions, which we do not track, so the
one tier that would need the network is the one tier we do not need.

**The imprecision is labelled, not hidden.** A parent POM read from a sibling
repository is that repo at HEAD, not at the version the consumer pins. If its
properties have moved since, the resolved value is wrong — so the record says
``parent_resolved_at: head`` rather than presenting it as certain.
"""

from __future__ import annotations

import re
from pathlib import Path

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException

from .repo_utils import is_internal_coordinate, safe_read_text

# A property may refer to another. Real projects chain two or three deep; this
# bounds it so a cycle terminates rather than spinning (`${a}` -> `${b}` -> `${a}`).
_MAX_PROPERTY_DEPTH = 8

# How far up a parent chain to walk. Deeper than any real project, and bounded so
# a cyclic `<relativePath>` cannot loop.
_MAX_PARENT_DEPTH = 6

_PROPERTY_RX = re.compile(r"^\$\{([A-Za-z0-9._\-]{1,120})\}$")


def _parse(pom_path: Path) -> object | None:
    """Parse a POM, or None if it is unreadable.

    Namespaces are *matched* rather than stripped. Stripping the declarations
    with a regex left prefix **uses** behind — the standard Maven root element
    carries `xsi:schemaLocation`, so removing `xmlns:xsi="..."` made `xsi:` an
    unbound prefix and the document failed to parse. Every POM an IDE or
    archetype emits looks like that, and the `except ParseError: return []`
    below swallowed it, so Maven dependencies silently came back empty for most
    real repositories.

    Reported from the field against 2.0.0. It survived a test suite because every
    fixture used a bare ``<project>`` element with no namespace at all — the one
    shape that made the bug invisible.
    """
    text = safe_read_text(pom_path)
    if not text:
        return None
    try:
        return DET.fromstring(text)
    except (DET.ParseError, DefusedXmlException):
        return None


def _properties(root: object) -> dict[str, str]:
    """Return the `<properties>` block as a mapping."""
    block = root.find("{*}properties")  # type: ignore[attr-defined]
    if block is None:
        return {}
    return {
        child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
        for child in block
    }


def _managed_versions(root: object) -> dict[str, str]:
    """Return `<dependencyManagement>` versions keyed by ``group:artifact``.

    These constrain a version; they do not declare a dependency. Reading the
    block as `<dependencies>` is what emitted the BOM itself as an edge.
    """
    out: dict[str, str] = {}
    block = root.find("{*}dependencyManagement")  # type: ignore[attr-defined]
    if block is None:
        return out
    for dep in block.findall(".//{*}dependency"):
        gid = (dep.findtext("{*}groupId") or "").strip()
        aid = (dep.findtext("{*}artifactId") or "").strip()
        ver = (dep.findtext("{*}version") or "").strip()
        if gid and aid and ver:
            out[f"{gid}:{aid}"] = ver
    return out


def _declared_dependencies(root: object) -> list[tuple[str, str, str]]:
    """Return (group, artifact, raw version) for real `<dependencies>` entries only."""
    managed = root.find("{*}dependencyManagement")  # type: ignore[attr-defined]
    managed_ids = {id(d) for d in managed.findall(".//{*}dependency")} if managed is not None else set()
    out: list[tuple[str, str, str]] = []
    for dep in root.findall(".//{*}dependency"):  # type: ignore[attr-defined]
        if id(dep) in managed_ids:
            continue
        out.append((
            (dep.findtext("{*}groupId") or "").strip(),
            (dep.findtext("{*}artifactId") or "").strip(),
            (dep.findtext("{*}version") or "").strip(),
        ))
    return out


def _parent_coordinate(root: object) -> tuple[str, str, str] | None:
    """Return the `<parent>` coordinate, if this POM declares one."""
    parent = root.find("{*}parent")  # type: ignore[attr-defined]
    if parent is None:
        return None
    return (
        (parent.findtext("{*}groupId") or "").strip(),
        (parent.findtext("{*}artifactId") or "").strip(),
        (parent.findtext("{*}relativePath") or "").strip(),
    )


def _find_parent_pom(
    pom_path: Path, repo_root: Path, coord: tuple[str, str, str], fleet_root: Path | None
) -> tuple[Path, str] | None:
    """Locate a parent POM on disk, returning (path, tier).

    Looks in the repo first — a multi-module project keeps the parent alongside —
    then across the fleet, where an internal parent lives in its own repository.
    """
    group, artifact, relative = coord
    in_repo = _find_parent_in_repo(pom_path, repo_root, artifact, relative)
    if in_repo is not None:
        return in_repo, "parent-in-repo"
    if fleet_root is None or not is_internal_coordinate(group, artifact):
        return None
    in_fleet = _find_parent_in_fleet(fleet_root, artifact)
    return (in_fleet, "parent-in-fleet") if in_fleet is not None else None


def _declares_artifact(pom: Path, artifact: str) -> bool:
    """True if ``pom`` declares this artifactId — the check that a candidate is the parent."""
    root = _parse(pom)
    return root is not None and (root.findtext("{*}artifactId") or "").strip() == artifact  # type: ignore[attr-defined]


def _find_parent_in_repo(
    pom_path: Path, repo_root: Path, artifact: str, relative: str
) -> Path | None:
    """Look for the parent alongside, as a multi-module project keeps it."""
    for candidate in (pom_path.parent / (relative or "../pom.xml"), repo_root / "pom.xml"):
        resolved = candidate if candidate.name.endswith(".xml") else candidate / "pom.xml"
        if not resolved.is_file() or resolved.resolve() == pom_path.resolve():
            continue
        if _declares_artifact(resolved, artifact):
            return resolved
    return None


def _find_parent_in_fleet(fleet_root: Path, artifact: str) -> Path | None:
    """Look for the parent in another cloned repository.

    This is the tier that makes offline resolution work: an internal parent lives
    in a repo we have already scanned, so it is a file read rather than a
    registry lookup.
    """
    for candidate in sorted(fleet_root.glob("*/*/pom.xml")):
        if _declares_artifact(candidate, artifact):
            return candidate
    return None


def _inherited_context(
    pom_path: Path, repo_root: Path, fleet_root: Path | None
) -> tuple[dict[str, str], dict[str, str], str | None]:
    """Walk the parent chain, returning (properties, managed versions, tier).

    Tier is the *furthest* source consulted, because that is what governs how much
    the answer can be trusted: a value from a sibling repo at HEAD is weaker than
    one from the file in hand.
    """
    properties: dict[str, str] = {}
    managed: dict[str, str] = {}
    tier: str | None = None

    current, current_root = pom_path, _parse(pom_path)
    for _ in range(_MAX_PARENT_DEPTH):
        if current_root is None:
            break
        coord = _parent_coordinate(current_root)
        if coord is None:
            break
        found = _find_parent_pom(current, repo_root, coord, fleet_root)
        if found is None:
            break
        parent_path, parent_tier = found
        parent_root = _parse(parent_path)
        if parent_root is None:
            break
        # A nearer definition wins, so only fill gaps as we walk outward.
        for key, value in _properties(parent_root).items():
            properties.setdefault(key, value)
        for key, value in _managed_versions(parent_root).items():
            managed.setdefault(key, value)
        tier = parent_tier if tier is None else tier
        current, current_root = parent_path, parent_root
    return properties, managed, tier


def _expand(value: str, properties: dict[str, str]) -> str | None:
    """Follow a `${property}` chain to a literal, or None if it does not terminate.

    The depth bound is the only termination check needed. A cycle-detecting `seen`
    set was written first and the mutation gate showed it unreachable: `${a}` ->
    `${b}` -> `${a}` exhausts the bound and returns None either way, so the set
    changed how quickly the answer arrived and never what it was.
    """
    for _ in range(_MAX_PROPERTY_DEPTH):
        match = _PROPERTY_RX.match(value)
        if match is None:
            return value
        key = match.group(1)
        if key not in properties:
            return None
        value = properties[key]
    return None


def resolve_pom_dependencies(
    pom_path: Path, repo_root: Path, fleet_root: Path | None = None
) -> list[dict[str, str]]:
    """Parse a POM's dependencies with versions resolved offline where possible.

    Every entry carries ``resolution`` naming the tier that answered, so a
    consumer can weigh a version from the file in hand differently from one
    inherited across the fleet. ``version_kind`` stays comparable with the other
    ecosystems: ``resolved`` or ``unresolved``.
    """
    root = _parse(pom_path)
    if root is None:
        return []

    own_properties = _properties(root)
    own_managed = _managed_versions(root)
    inherited_props, inherited_managed, parent_tier = _inherited_context(
        pom_path, repo_root, fleet_root
    )
    properties = {**inherited_props, **own_properties}
    managed = {**inherited_managed, **own_managed}

    deps: list[dict[str, str]] = []
    for group, artifact, raw in _declared_dependencies(root):
        if not artifact:
            continue
        version, tier = _resolve_one(
            raw, group, artifact, own_properties, properties, own_managed, managed, parent_tier
        )
        entry: dict[str, str] = {
            "groupId": group,
            "artifactId": artifact,
            "version": version,
            "version_kind": "resolved" if version else "unresolved",
            "resolution": tier,
            "kind": "internal" if is_internal_coordinate(group, artifact) else "external",
        }
        if tier == "parent-in-fleet":
            # The sibling repo is at HEAD, which may not be the version this
            # consumer pins. Say so rather than presenting it as certain.
            entry["parent_resolved_at"] = "head"
        deps.append(entry)
    return deps


def _resolve_one(
    raw: str,
    group: str,
    artifact: str,
    own_properties: dict[str, str],
    properties: dict[str, str],
    own_managed: dict[str, str],
    managed: dict[str, str],
    parent_tier: str | None,
) -> tuple[str, str]:
    """Resolve one dependency's version, returning (version, tier).

    Ordered nearest-first, because the tier records how far the answer came from:
    the literal in the element, a property in this file, a managed version here,
    then anything inherited.
    """
    if raw and not raw.startswith("${"):
        return raw, "literal"
    if raw:
        return _resolve_property(raw, own_properties, properties, parent_tier)
    return _resolve_managed(
        f"{group}:{artifact}", own_managed, managed, properties, parent_tier
    )


def _resolve_property(
    raw: str,
    own_properties: dict[str, str],
    properties: dict[str, str],
    parent_tier: str | None,
) -> tuple[str, str]:
    """Expand a `${property}` reference, nearest definition first."""
    local = _expand(raw, own_properties)
    if local and not local.startswith("${"):
        return local, "property"
    inherited = _expand(raw, properties)
    if inherited and not inherited.startswith("${"):
        return inherited, parent_tier or "property"
    return "", "unresolved"


def _resolve_managed(
    key: str,
    own_managed: dict[str, str],
    managed: dict[str, str],
    properties: dict[str, str],
    parent_tier: str | None,
) -> tuple[str, str]:
    """Take the version from `<dependencyManagement>` when the element omits one."""
    for source, tier in ((own_managed, "property"), (managed, parent_tier or "property")):
        if key in source:
            resolved = _expand(source[key], properties)
            return (resolved, tier) if resolved else ("", "unresolved")
    return "", "unresolved"
