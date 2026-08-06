"""Shared repo-inspection utilities used by both v1 and v2 extractors."""

from __future__ import annotations

import configparser
import json
import re
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException

from . import internal_groups as _internal_groups
# Re-exported explicitly: build_metabase_v2 rebinds ``repo_utils.MAX_FILE_BYTES``
# at startup so a --max-file-bytes override applies to the manifest read paths.
from .constants import MAX_FILE_BYTES as MAX_FILE_BYTES
from .checkout_scan import paths_by_name
from .constants import SKIP_DIRS
from .safe_paths import resolve_within

# Untrusted manifests (pom.xml, *.csproj) are parsed with defusedxml to block
# entity-expansion ("billion laughs") and external-entity attacks. ``DET`` also
# re-exports ``ParseError`` (it *is* the stdlib type), so merely malformed XML is
# caught without importing ``xml.etree`` at all.

# Callback shapes used by the identity/artifact index passes below.
ArtifactRegister = Callable[[str, str, str], None]


class IdentityRegister(Protocol):
    """The callback shape the identity scanners are handed to report what they find.

    ``_build_component_identity_index`` owns three dicts and passes a closure over
    them down to half a dozen ``_index_*`` scanners. The scanners never see the
    dicts and the index never does any scanning; this Protocol is the seam, and
    it lets ``mypy --strict`` check that both ends agree on the call shape.

    A ``Protocol`` with ``__call__`` rather than a ``Callable[...]`` alias like
    :data:`ArtifactRegister` above, for one reason: ``Callable`` can only express
    *positional* parameters, and these scanners call
    ``register(group, name, path, full=...)``. The optional keyword argument is
    the whole difference between the two declarations.

    Structural, not nominal — no implementation declares that it satisfies this,
    it simply has to match — and checked statically only: without
    ``@runtime_checkable`` an ``isinstance`` test raises, so nothing enforces it
    at run time.
    """

    def __call__(
        self, group: str, name: str, clone_path: str | None, full: str | None = None
    ) -> None:
        """Record one identity, subject to the rules every implementation must keep.

        * A call with an empty ``name`` or ``clone_path`` is **ignored**, not an
          error. The callers scan manifests that are routinely incomplete, and
          making each of them pre-filter would spread the same check across a
          dozen call sites.
        * When one ``(group, name)`` is seen twice, the **shortest**
          ``clone_path`` wins: the shallowest checkout is taken as canonical, so
          a vendored or nested copy cannot displace the real repository.
        * One ``name`` may map to several paths — the by-name index accumulates
          rather than replaces, because an ambiguous name is a fact worth keeping
          rather than a collision to resolve arbitrarily.
        * ``full`` is optional and populates a *separate* index, so a lookup by
          full coordinate never silently falls back to a looser name match.
        """

# ---------------------------------------------------------------------------
# External-coordinate fast-reject prefixes
# ---------------------------------------------------------------------------

DEFINITELY_EXTERNAL_PREFIXES = (
    "org.springframework", "org.apache", "org.jetbrains", "io.netty",
    "io.micronaut", "io.quarkus", "com.fasterxml", "com.google",
    "javax.", "jakarta.", "junit", "org.junit", "io.projectreactor",
    "org.slf4j", "ch.qos", "org.testcontainers", "org.mockito",
    "io.swagger", "io.opentelemetry", "io.dropwizard", "io.vertx",
    "org.eclipse", "redis.clients",
)

# ---------------------------------------------------------------------------
# Framework / dependency classification table
# ---------------------------------------------------------------------------

DEPENDENCY_TO_FRAMEWORK = [
    ("spring-boot", "spring-boot"),
    ("spring-security", "spring-security"),
    ("spring-webmvc", "spring-mvc"),
    ("spring-webflux", "spring-webflux"),
    ("micronaut", "micronaut"),
    ("quarkus", "quarkus"),
    ("dropwizard", "dropwizard"),
    ("vertx", "vertx"),
    ("jersey", "jax-rs"),
    ("resteasy", "jax-rs"),
    ("jaxrs", "jax-rs"),
    ("helidon", "helidon"),
    ("play2", "play"),
    ("netty", "netty"),
    ("apollo-server", "apollo-server"),
    ("graphql-yoga", "graphql-yoga"),
    ("nestjs", "nestjs"),
    ("@nestjs/", "nestjs"),
    ("express", "express"),
    ("koa", "koa"),
    ("fastify", "fastify"),
    ("hapi", "hapi"),
    ("next", "next.js"),
    ("react", "react"),
    ("angular", "angular"),
    ("vue", "vue"),
    ("flask", "flask"),
    ("fastapi", "fastapi"),
    ("django", "django"),
    ("aiohttp", "aiohttp"),
    ("gin-gonic", "gin"),
    ("labstack/echo", "echo"),
    ("go-chi", "chi"),
    ("gofiber", "fiber"),
    ("net/http", "go-net-http"),
]

# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------


def safe_read_text(path: Path) -> str | None:
    """Read a text file, returning None if it is oversized or unreadable.

    Bounds untrusted scanned-repo files to ``MAX_FILE_BYTES`` before reading
    (0 disables the cap; the value is overridable via ``--max-file-bytes``).
    """
    try:
        if MAX_FILE_BYTES and path.stat().st_size > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Coordinate classification
# ---------------------------------------------------------------------------


def is_internal_coordinate(group: str | None, name: str | None) -> bool:
    """Return True if a Maven/Gradle/npm coordinate is organisation-internal."""
    if not group:
        candidates = [name or ""]
    else:
        if any(group.startswith(prefix) for prefix in DEFINITELY_EXTERNAL_PREFIXES):
            return False
        candidates = [group, name or ""]
    for cand in candidates:
        if not cand:
            continue
        for pat in _internal_groups.INTERNAL_GROUP_PATTERNS:
            if pat.match(cand):
                return True
    return False


# ---------------------------------------------------------------------------
# Dependency parsing
# ---------------------------------------------------------------------------


def parse_package_json_dependencies(pkg_path: Path) -> list[dict[str, str]]:
    """Parse dependency coordinates from an npm ``package.json``."""
    text = safe_read_text(pkg_path)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps: list[dict[str, str]] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(section, {})
        if not isinstance(block, dict):
            continue
        for name, version in block.items():
            kind = "internal" if is_internal_coordinate(name, name) else "external"
            deps.append({
                "groupId": name.split("/")[0] if name.startswith("@") else "",
                "artifactId": name,
                "version": str(version),
                "kind": kind,
                "section": section,
            })
    return deps


# ---------------------------------------------------------------------------
# Framework classification
# ---------------------------------------------------------------------------


def classify_frameworks(coords: list[dict[str, str]]) -> list[str]:
    """Return the sorted framework labels implied by dependency coordinates."""
    seen: set[str] = set()
    for d in coords:
        coord = (d.get("groupId", "") + ":" + d.get("artifactId", "")).lower()
        for token, label in DEPENDENCY_TO_FRAMEWORK:
            if token in coord:
                seen.add(label)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Build system detection
# ---------------------------------------------------------------------------


def detect_build_systems(repo_root: Path) -> list[str]:
    """Return the sorted build systems detected from marker files in a repo."""
    found: set[str] = set()
    if (repo_root / "pom.xml").is_file():
        found.add("maven")
    if (repo_root / "build.gradle").is_file() or (repo_root / "settings.gradle").is_file():
        found.add("gradle")
    if (repo_root / "build.gradle.kts").is_file() or (repo_root / "settings.gradle.kts").is_file():
        found.add("gradle-kts")
    if (repo_root / "package.json").is_file():
        found.add("npm")
    if (repo_root / "yarn.lock").is_file():
        found.add("yarn")
    if (repo_root / "pnpm-lock.yaml").is_file():
        found.add("pnpm")
    if (repo_root / "requirements.txt").is_file():
        found.add("pip")
    if (repo_root / "pyproject.toml").is_file():
        found.add("poetry")
    if (repo_root / "Pipfile").is_file():
        found.add("pipenv")
    if (repo_root / "go.mod").is_file():
        found.add("go-modules")
    if (repo_root / "Cargo.toml").is_file():
        found.add("cargo")
    return sorted(found)


# ---------------------------------------------------------------------------
# Git SHA detection
# ---------------------------------------------------------------------------


_GIT_SHA_RX = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")


def detect_git_sha(repo_root: Path) -> str | None:
    """Return the committed git SHA of a scanned repo, or None.

    Security: ``.git/HEAD`` is attacker-controlled untrusted input; the resolved
    ref target is contained within ``.git`` (via ``resolve_within``) so a crafted
    symbolic ref cannot escape into an arbitrary-file read (path traversal).
    """
    git_dir = repo_root / ".git"
    head = git_dir / "HEAD"
    if not head.is_file():
        return None
    ref = safe_read_text(head)
    if ref is None:
        return None
    ref = ref.strip()
    if ref.startswith("ref: "):
        # ``ref`` is attacker-controlled (it comes from a scanned repo's HEAD).
        # Contain the resolved target inside .git so a crafted
        # "ref: ../../../etc/passwd" cannot turn this into an arbitrary read.
        target = resolve_within(git_dir / ref[5:], git_dir)
        if target is None or not target.is_file():
            return None
        value = safe_read_text(target)
        if value is None:
            return None
        value = value.strip()
        return value if _GIT_SHA_RX.match(value) else None
    return ref if _GIT_SHA_RX.match(ref) else None


# ---------------------------------------------------------------------------
# Gradle / Maven parsing helpers (used by library-locator index below)
# ---------------------------------------------------------------------------


def _strip_xmlns(tag: str) -> str:
    """Return an ET tag with any ``{namespace}`` prefix stripped."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


_GRADLE_LINE_COMMENT_RX = re.compile(r"//[^\n]*")
_GRADLE_BLOCK_COMMENT_RX = re.compile(r"/\*.*?\*/", re.DOTALL)

_GRADLE_GROUP_PATTERNS = (
    re.compile(r"^\s*group\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE),
    re.compile(r"^\s*group\s+['\"]([^'\"]+)['\"]", re.MULTILINE),
    re.compile(r"^\s*group\(\s*['\"]([^'\"]+)['\"]\s*\)", re.MULTILINE),
)
_GRADLE_BLOCK_GROUP_RX = re.compile(
    r"(?:allprojects|subprojects)\s*\{[^}]*?group\s*=\s*['\"]([^'\"]+)['\"]",
    re.DOTALL,
)


def _strip_gradle_comments(text: str) -> str:
    """Remove line and block comments from Gradle build-script text."""
    return _GRADLE_BLOCK_COMMENT_RX.sub("", _GRADLE_LINE_COMMENT_RX.sub("", text))


def _read_gradle_settings(settings_path: Path) -> tuple[str | None, list[str]]:
    """Parse ``rootProject.name`` and ``include`` directives."""
    text = safe_read_text(settings_path)
    if text is None:
        return None, []
    text = _strip_gradle_comments(text)

    root_name: str | None = None
    m = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", text)
    if m:
        root_name = m.group(1)

    includes: list[str] = []
    for line in text.splitlines():
        if "include" not in line:
            continue
        if not re.match(r"\s*include[\s(]", line):
            continue
        for tok in re.findall(r"['\"]([^'\"]+)['\"]", line):
            mod = tok.lstrip(":").replace(":", "/").strip("/")
            if mod:
                includes.append(mod)
    return root_name, includes


def _read_gradle_group(root_dir: Path) -> str | None:
    """Best-effort Gradle ``group`` detection."""
    gp = root_dir / "gradle.properties"
    if gp.is_file():
        gp_text = safe_read_text(gp)
        if gp_text is not None:
            for line in gp_text.splitlines():
                m = re.match(r"^\s*group\s*=\s*(.+?)\s*$", line)
                if m:
                    return m.group(1).strip().strip("'\"")
    for fname in ("build.gradle.kts", "build.gradle"):
        bg = root_dir / fname
        if not bg.is_file():
            continue
        bg_text = safe_read_text(bg)
        if bg_text is None:
            continue
        text = _strip_gradle_comments(bg_text)
        for pat in _GRADLE_GROUP_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(1)
        m = _GRADLE_BLOCK_GROUP_RX.search(text)
        if m:
            return m.group(1)
    return None


def _read_pom_identity(pom_path: Path) -> tuple[str, str] | None:
    """Return (groupId, artifactId) declared by ``pom_path``."""
    try:
        if MAX_FILE_BYTES and pom_path.stat().st_size > MAX_FILE_BYTES:
            return None
        root = DET.parse(pom_path).getroot()
    except (DET.ParseError, DefusedXmlException, OSError):
        return None
    if root is None:
        return None
    own_group: str | None = None
    own_artifact: str | None = None
    parent_group: str | None = None
    for child in root:
        tag = _strip_xmlns(child.tag)
        if tag == "groupId" and child.text:
            own_group = child.text.strip()
        elif tag == "artifactId" and child.text:
            own_artifact = child.text.strip()
        elif tag == "parent":
            for sub in child:
                if _strip_xmlns(sub.tag) == "groupId" and sub.text:
                    parent_group = sub.text.strip()
    if not own_artifact:
        return None
    return (own_group or parent_group or "", own_artifact)


# ---------------------------------------------------------------------------
# Component-identity readers for non-Maven ecosystems
#
# Each reader returns ``(group, name, full)`` where ``group`` is the namespace
# (may be ""), ``name`` the bare component name, and ``full`` the canonical
# coordinate string as it would appear as a dependency (e.g. an npm
# ``@scope/pkg`` or a Composer ``vendor/pkg`` or a Go module path), or ``None``
# when it is just the bare name. Returns ``None`` when no identity is declared.
# ---------------------------------------------------------------------------


def _read_pyproject_identity(path: Path) -> tuple[str, str, str | None] | None:
    """Read the distribution name from a ``pyproject.toml`` (PEP 621 or poetry)."""
    text = safe_read_text(path)
    if not text:
        return None
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    proj = data.get("project")
    if isinstance(proj, dict):
        name = proj.get("name")
        if isinstance(name, str) and name.strip():
            return ("", name.strip(), None)
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            name = poetry.get("name")
            if isinstance(name, str) and name.strip():
                return ("", name.strip(), None)
    return None


def _read_setup_cfg_identity(path: Path) -> tuple[str, str, str | None] | None:
    """Read ``[metadata] name`` from a ``setup.cfg``."""
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error, UnicodeDecodeError):
        return None
    if parser.has_option("metadata", "name"):
        name = parser.get("metadata", "name").strip()
        if name:
            return ("", name, None)
    return None


def _read_cargo_identity(path: Path) -> tuple[str, str, str | None] | None:
    """Read ``[package] name`` from a Rust ``Cargo.toml``."""
    text = safe_read_text(path)
    if not text:
        return None
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    pkg = data.get("package")
    if isinstance(pkg, dict):
        name = pkg.get("name")
        if isinstance(name, str) and name.strip():
            return ("", name.strip(), None)
    return None


def _read_composer_identity(path: Path) -> tuple[str, str, str | None] | None:
    """Read the ``name`` (``vendor/package``) from a PHP ``composer.json``."""
    text = safe_read_text(path)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    if "/" in name:
        vendor, pkg = name.split("/", 1)
        return (vendor, pkg, name)
    return ("", name, name)


_GOMOD_MODULE_RX = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


def _read_gomod_identity(path: Path) -> tuple[str, str, str | None] | None:
    """Read the ``module`` path from a Go ``go.mod``."""
    text = safe_read_text(path)
    if not text:
        return None
    m = _GOMOD_MODULE_RX.search(text)
    if not m:
        return None
    module = m.group(1).strip().strip('"')
    if not module:
        return None
    name = module.rsplit("/", 1)[-1]
    return (module.rsplit("/", 1)[0] if "/" in module else "", name, module)


def _read_dotnet_project_identity(path: Path) -> tuple[str, str, str | None] | None:
    """Read the package/assembly name from a .NET ``*.csproj``/``*.fsproj``/``*.vbproj``."""
    try:
        if MAX_FILE_BYTES and path.stat().st_size > MAX_FILE_BYTES:
            return None
        root = DET.parse(path).getroot()
    except (DET.ParseError, DefusedXmlException, OSError):
        return None
    if root is None:
        return None
    found: dict[str, str] = {}
    for el in root.iter():
        tag = _strip_xmlns(el.tag)
        if tag in ("PackageId", "AssemblyName", "RootNamespace") and el.text:
            found.setdefault(tag, el.text.strip())
    for tag in ("PackageId", "AssemblyName", "RootNamespace"):
        if found.get(tag):
            return ("", found[tag], None)
    # SDK-style projects often omit these and default to the file stem.
    return ("", path.stem, None) if path.stem else None


_GEMSPEC_NAME_RX = re.compile(r"\.name\s*=\s*['\"]([^'\"]+)['\"]")


def _read_gemspec_identity(path: Path) -> tuple[str, str, str | None] | None:
    """Read the gem ``name`` from a Ruby ``*.gemspec``."""
    text = safe_read_text(path)
    if not text:
        return None
    m = _GEMSPEC_NAME_RX.search(text)
    if m and m.group(1).strip():
        return ("", m.group(1).strip(), None)
    # Gem filenames conventionally match the gem name.
    return ("", path.stem, None) if path.stem else None


# ---------------------------------------------------------------------------
# Library-source locator
# ---------------------------------------------------------------------------

_repo_artifact_index_cache: dict[
    Path,
    tuple[dict[tuple[str, str], str], dict[str, list[str]], dict[str, str]],
] | None = None


def _rel_to_parent(path: Path, repos_root: Path) -> str | None:
    """Path of ``path``'s directory relative to ``repos_root``'s parent (or None)."""
    try:
        return str(path.parent.relative_to(repos_root.parent))
    except ValueError:
        return None


def _index_poms(repos_root: Path, register: ArtifactRegister) -> None:
    """Register (groupId, artifactId, path) identity for every pom.xml found."""
    for path in _iter_manifests(repos_root, "pom.xml"):
        ident = _read_pom_identity(path)
        rel = _rel_to_parent(path, repos_root)
        if ident and rel is not None:
            register(ident[0], ident[1], rel)


def _index_gradle(repos_root: Path, register: ArtifactRegister) -> None:
    """Register identity for Gradle root projects and their included modules."""
    for settings_name in ("settings.gradle", "settings.gradle.kts"):
        for path in _iter_manifests(repos_root, settings_name):
            root_dir = path.parent
            root_name, includes = _read_gradle_settings(path)
            group = _read_gradle_group(root_dir) or ""
            rel_root = _rel_to_parent(path, repos_root)
            if rel_root is None:
                continue
            register(group, root_name or root_dir.name, rel_root)
            for inc in includes:
                inc_dir = root_dir / inc
                if not inc_dir.is_dir():
                    continue
                try:
                    rel_inc = str(inc_dir.relative_to(repos_root.parent))
                except ValueError:
                    continue
                register(group, inc.rsplit("/", 1)[-1], rel_inc)


def _index_npm(repos_root: Path, npm_by_name: dict[str, str]) -> None:
    """Map each package.json ``name`` to its shortest relative clone path."""
    for path in _iter_manifests(repos_root, "package.json"):
        text = safe_read_text(path)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        name = data.get("name")
        rel = _rel_to_parent(path, repos_root)
        if not isinstance(name, str) or not name or rel is None:
            continue
        existing = npm_by_name.get(name)
        if existing is None or len(rel) < len(existing):
            npm_by_name[name] = rel


def _build_repo_artifact_index(repos_root: Path) -> tuple[
    dict[tuple[str, str], str],
    dict[str, list[str]],
    dict[str, str],
]:
    """Walk every ``pom.xml`` and ``package.json`` under ``repos_root``.

    Returns three lookup tables:
    * ``pom_by_coord``   — (groupId, artifactId) → relative path
    * ``pom_by_artifact`` — artifactId → list of relative paths
    * ``npm_by_name``    — npm ``name`` field → relative path
    """
    global _repo_artifact_index_cache  # noqa: PLW0603
    if _repo_artifact_index_cache is not None and repos_root in _repo_artifact_index_cache:
        return _repo_artifact_index_cache[repos_root]

    pom_by_coord: dict[tuple[str, str], str] = {}
    pom_by_artifact: dict[str, list[str]] = defaultdict(list)
    npm_by_name: dict[str, str] = {}

    def _register(group: str, artifact: str, rel_str: str) -> None:
        """Record ``(group, artifact)`` → shortest ``rel_str``, tracking all paths seen."""
        existing = pom_by_coord.get((group, artifact))
        if existing is None or len(rel_str) < len(existing):
            pom_by_coord[(group, artifact)] = rel_str
        bucket = pom_by_artifact[artifact]
        if rel_str not in bucket:
            bucket.append(rel_str)

    _index_poms(repos_root, _register)
    _index_gradle(repos_root, _register)
    _index_npm(repos_root, npm_by_name)

    if _repo_artifact_index_cache is None:
        _repo_artifact_index_cache = {}
    _repo_artifact_index_cache[repos_root] = (pom_by_coord, dict(pom_by_artifact), npm_by_name)
    return _repo_artifact_index_cache[repos_root]


# ---------------------------------------------------------------------------
# Component-identity index (all ecosystems) — used to resolve source-map paths
# ---------------------------------------------------------------------------

# Maps a manifest filename to its identity reader. Gradle and the Maven/npm
# manifests are handled separately (via _build_repo_artifact_index and the
# gradle single-module pass below) because they need multi-file logic.
_SIMPLE_IDENTITY_READERS = {
    "pyproject.toml": _read_pyproject_identity,
    "setup.cfg": _read_setup_cfg_identity,
    "Cargo.toml": _read_cargo_identity,
    "composer.json": _read_composer_identity,
    "go.mod": _read_gomod_identity,
}

# Glob patterns for ecosystems whose manifest name varies per project.
_GLOB_IDENTITY_READERS = (
    ("*.csproj", _read_dotnet_project_identity),
    ("*.fsproj", _read_dotnet_project_identity),
    ("*.vbproj", _read_dotnet_project_identity),
    ("*.gemspec", _read_gemspec_identity),
)

_component_identity_index_cache: dict[
    Path, tuple[dict[tuple[str, str], str], dict[str, list[str]], dict[str, str]]
] | None = None


def is_skipped_path(path: Path, root: Path) -> bool:
    """True if a path segment *below* ``root`` names an excluded directory.

    Only the segments under ``root`` are considered. The absolute prefix is the
    operator's filesystem layout, not the scanned tree: a repos root under
    ``/tmp/repos`` or ``~/build/repos`` is perfectly legitimate, and matching
    ``SKIP_DIRS`` against it would silently exclude everything beneath it.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = path
    return any(part in SKIP_DIRS for part in rel.parts)


def all_manifest_patterns() -> frozenset[str]:
    """Every manifest pattern any indexer asks for.

    Passed as one set so the whole tree is walked once rather than once per
    pattern. Built from the readers themselves, so adding an ecosystem cannot
    leave its pattern out of the shared walk and silently fall back to finding
    nothing.
    """
    return frozenset({
        "pom.xml",
        "settings.gradle", "settings.gradle.kts",
        "build.gradle", "build.gradle.kts",
        "package.json",
        *_SIMPLE_IDENTITY_READERS,
        *(pattern for pattern, _reader in _GLOB_IDENTITY_READERS),
    })


def _iter_manifests(repos_root: Path, pattern: str) -> Iterator[Path]:
    """Yield manifest paths matching ``pattern`` under ``repos_root``.

    Applies the same depth window (2..6) and ``SKIP_DIRS`` filtering used by
    :func:`_build_repo_artifact_index`.

    Backed by a single shared walk. `Path.rglob(pattern)` traverses the whole
    tree and filters by name, so calling this once per pattern cost **fifteen**
    full traversals of a 34 GB checkout per run — the same defect `OI-30` fixed
    in the producer scan, in a second place.
    """
    repos_root_resolved = repos_root.resolve()
    max_extra_depth = 6
    for path in paths_by_name(repos_root, all_manifest_patterns())[pattern]:
        try:
            depth = len(path.parent.resolve().relative_to(repos_root_resolved).parts)
        except ValueError:
            continue
        if depth > max_extra_depth or depth < 2:
            continue
        if is_skipped_path(path, repos_root):
            continue
        yield path


def _rel_to_root(path: Path, repos_root: Path) -> str | None:
    """Path of ``path``'s directory relative to ``repos_root`` (or None)."""
    try:
        return str(path.parent.relative_to(repos_root))
    except ValueError:
        return None


def _strip_repos_prefix(rel_str: str, repos_prefix: str) -> str:
    """Drop a leading repos-dir component from a relative path string."""
    parts = Path(rel_str).parts
    if parts and parts[0] == repos_prefix:
        parts = parts[1:]
    return "/".join(parts)


def _seed_identity_from_artifact_index(
    repos_root: Path, register: IdentityRegister, by_name: dict[str, list[str]]
) -> None:
    """Pass 1: seed identity tables from the shared Maven/Gradle/npm index.

    Paths there are relative to ``repos_root.parent``, so strip the leading
    repos-dir name to make them relative to ``repos_root``.
    """
    repos_prefix = repos_root.name
    pom_by_coord, pom_by_artifact, npm_by_name = _build_repo_artifact_index(repos_root)
    for (group, artifact), rel in pom_by_coord.items():
        register(group, artifact, _strip_repos_prefix(rel, repos_prefix))
    for artifact, rels in pom_by_artifact.items():
        for rel in rels:
            cp = _strip_repos_prefix(rel, repos_prefix)
            if cp and cp not in by_name[artifact]:
                by_name[artifact].append(cp)
    for name, rel in npm_by_name.items():
        scope = name.split("/", 1)[0] if name.startswith("@") else ""
        register(scope, name, _strip_repos_prefix(rel, repos_prefix), full=name)


def _index_identity_readers(repos_root: Path, register: IdentityRegister) -> None:
    """Pass 2: walk the per-ecosystem manifest readers (simple + glob)."""
    readers = [
        (filename, reader) for filename, reader in _SIMPLE_IDENTITY_READERS.items()
    ] + list(_GLOB_IDENTITY_READERS)
    for pattern, reader in readers:
        for path in _iter_manifests(repos_root, pattern):
            ident = reader(path)
            if ident:
                register(ident[0], ident[1], _rel_to_root(path, repos_root), full=ident[2])


def _index_gradle_single_module(repos_root: Path, register: IdentityRegister) -> None:
    """Pass 3: Gradle single-module repos (build.gradle, no settings.gradle)."""
    for gname in ("build.gradle", "build.gradle.kts"):
        for path in _iter_manifests(repos_root, gname):
            root_dir = path.parent
            if (root_dir / "settings.gradle").is_file() or (
                root_dir / "settings.gradle.kts"
            ).is_file():
                continue  # handled by the settings.gradle pass in the shared index
            group = _read_gradle_group(root_dir) or ""
            register(group, root_dir.name, _rel_to_root(path, repos_root))


def _build_component_identity_index(repos_root: Path) -> tuple[
    dict[tuple[str, str], str],
    dict[str, list[str]],
    dict[str, str],
]:
    """Index the declared identity of every project under ``repos_root``.

    Scans Maven, Gradle, npm, Python (pyproject/setup.cfg), Rust, Go, PHP,
    .NET, and Ruby manifests. Returns three lookup tables whose paths are
    relative to ``repos_root`` (i.e. ``group/repo``):

    * ``by_coord`` — ``(group, name)`` → clone path
    * ``by_name``  — bare ``name`` → list of clone paths (for unique fallback)
    * ``by_full``  — canonical coordinate string → clone path
      (npm ``@scope/pkg``, Composer ``vendor/pkg``, Go module path, bare names)
    """
    global _component_identity_index_cache  # noqa: PLW0603
    if (
        _component_identity_index_cache is not None
        and repos_root in _component_identity_index_cache
    ):
        return _component_identity_index_cache[repos_root]

    by_coord: dict[tuple[str, str], str] = {}
    by_name: dict[str, list[str]] = defaultdict(list)
    by_full: dict[str, str] = {}

    def register(group: str, name: str, clone_path: str | None, full: str | None = None) -> None:
        """Record identity ``(group, name)`` (and optional ``full`` coordinate) → clone path."""
        if not name or not clone_path:
            return
        key = (group or "", name)
        cur = by_coord.get(key)
        if cur is None or len(clone_path) < len(cur):
            by_coord[key] = clone_path
        bucket = by_name[name]
        if clone_path not in bucket:
            bucket.append(clone_path)
        if full:
            curf = by_full.get(full)
            if curf is None or len(clone_path) < len(curf):
                by_full[full] = clone_path

    _seed_identity_from_artifact_index(repos_root, register, by_name)
    _index_identity_readers(repos_root, register)
    _index_gradle_single_module(repos_root, register)

    result = (by_coord, dict(by_name), by_full)
    if _component_identity_index_cache is None:
        _component_identity_index_cache = {}
    _component_identity_index_cache[repos_root] = result
    return result


def _source_map_lookup(
    coord: str, source_map: dict[str, Any] | None
) -> tuple[bool, str | None]:
    """Resolve ``coord`` via an explicit source map.

    Returns ``(handled, value)``: ``handled`` is True when the source map is
    authoritative for this coordinate (excluded → ``(True, None)``; mapped →
    ``(True, "repos/<path>")``); ``(False, None)`` means fall through to
    heuristics.
    """
    entry = source_map.get(coord) if source_map else None
    if not entry:
        return (False, None)
    if entry.get("status") == "excluded":
        return (True, None)
    clone_path = entry.get("clone_path")
    if clone_path:
        return (True, f"repos/{clone_path}")
    return (False, None)


def _locate_via_artifact_index(
    coord: str,
    pom_by_coord: dict[tuple[str, str], str],
    pom_by_artifact: dict[str, list[str]],
    npm_by_name: dict[str, str],
) -> str | None:
    """Resolve ``coord`` against the Maven/npm artifact index."""
    if ":" in coord:
        group, artifact = coord.split(":", 1)
        exact = pom_by_coord.get((group, artifact))
        if exact:
            return exact
        candidates = pom_by_artifact.get(artifact, [])
        if len(candidates) == 1:
            return candidates[0]
    npm_name = coord.split(":", 1)[1] if ":" in coord else coord
    if npm_name in npm_by_name:
        return npm_by_name[npm_name]
    if coord in npm_by_name:
        return npm_by_name[coord]
    return None


def _glob_library_dir(repos_root: Path, coord: str) -> str | None:
    """Best-effort filesystem glob for a directory matching the coordinate."""
    artifact = coord.split(":")[-1].lstrip("@").replace("/", "-")
    if not artifact:
        return None
    for pattern in (f"*/{artifact}", f"*/*{artifact}*"):
        for cand in repos_root.glob(pattern):
            if cand.is_dir():
                return str(cand.relative_to(repos_root.parent))
    return None


def _locate_library_source(
    repos_root: Path, coord: str, *, source_map: dict[str, Any] | None = None
) -> str | None:
    """Find an internal-library coordinate's source directory under repos_root."""
    if not coord:
        return None
    handled, value = _source_map_lookup(coord, source_map)
    if handled:
        return value
    index = _build_repo_artifact_index(repos_root)
    return _locate_via_artifact_index(coord, *index) or _glob_library_dir(repos_root, coord)
