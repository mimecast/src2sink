"""Discover OpenAPI specs and Helm ingress hosts under repos/."""

from __future__ import annotations

import re
from pathlib import Path

from ..checkout_scan import paths_by_name
from ..graph_common import normalize_path_template as norm_path

from .openapi_models import OpenApiSpec

OPENAPI_GLOBS = ("openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml")
PATH_LINE_RX = re.compile(
    r"^\s{2,}(/[/\w{}.:$-]+)\s*:\s*$",
    re.MULTILINE,
)
SERVERS_URL_RX = re.compile(
    r"^\s*-\s*url:\s*['\"]?(https?://[^'\"\s]+)",
    re.MULTILINE | re.IGNORECASE,
)
HELM_VALUES_NAMES = ("values.yaml", "values.yml")
HELM_HOST_RX = re.compile(
    r"^\s*(?:host|hostname|externalHost|ingress\.host)\s*:\s*['\"]?([^\s#'\"]+)",
    re.MULTILINE | re.IGNORECASE,
)

_SKIP_DIR_PARTS = frozenset({".git", "node_modules", "target", "build", "charts"})


def repo_from_under_repos(spec_path: Path, repos_root: Path) -> str | None:
    """Return the ``org/repo`` id for a path under repos_root, or None."""
    try:
        rel = spec_path.relative_to(repos_root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _should_skip_path(path: Path) -> bool:
    """True if the path lies within a skipped directory (.git, node_modules, ...)."""
    return any(p in path.parts for p in _SKIP_DIR_PARTS)


def _walk_once(repos_root: Path, names: tuple[str, ...]) -> list[Path]:
    """Every file under the checkout carrying one of ``names``, from a single walk.

    `Path.rglob(name)` traverses the whole tree and filters by name, so the
    per-pattern loop this replaces cost one full traversal of a 34 GB checkout
    *per filename* — four for the OpenAPI globs, two for Helm, and again for
    every call site. The same defect as `OI-30` in the producer scan: the loop
    over what to look for sat outside the loop over where to look.
    """
    found = paths_by_name(repos_root, frozenset(names))
    return [path for name in names for path in found[name]]


def discover_openapi_specs(repos_root: Path) -> list[OpenApiSpec]:
    """Scan repos_root for OpenAPI/Swagger specs and return parsed OpenApiSpec rows."""
    if not repos_root.is_dir():
        return []
    specs: list[OpenApiSpec] = []
    seen: set[tuple[str, str]] = set()
    for path in _walk_once(repos_root, OPENAPI_GLOBS):
        if _should_skip_path(path):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        target = repo_from_under_repos(path, repos_root)
        if not target:
            continue
        key = (target, str(path))
        if key in seen:
            continue
        seen.add(key)
        paths = sorted({norm_path(m.group(1)) for m in PATH_LINE_RX.finditer(text)})
        servers = [m.group(1) for m in SERVERS_URL_RX.finditer(text)]
        specs.append(OpenApiSpec(
            target_repo=target,
            spec_path=str(path.relative_to(repos_root)),
            paths=[p for p in paths if p and p != "/"],
            servers=servers[:5],
        ))
    return specs


def discover_helm_hosts(repos_root: Path) -> list[dict[str, str]]:
    """Scan Helm values for declared ingress/service hostnames."""
    if not repos_root.is_dir():
        return []
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in _walk_once(repos_root, HELM_VALUES_NAMES):
        if _should_skip_path(path):
            continue
        try:
            if path.stat().st_size > 500_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        target = repo_from_under_repos(path, repos_root)
        if not target:
            continue
        for m in HELM_HOST_RX.finditer(text):
            host = m.group(1).strip().strip("'\"")
            if not host or host.startswith("{{"):
                continue
            key = (target, host)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "target_repo": target,
                "host": host,
                "values_path": str(path.relative_to(repos_root)),
            })
    return rows
