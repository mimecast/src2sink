#!/usr/bin/env python3
"""Metabase v2 extractor — flow-graph nodes (source / propagator / sink / store)."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import multiprocessing as mp
import os
import re
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .internal_groups import add_internal_groups_arguments, apply_internal_groups_from_args
from .limits import (
    DEFAULT_MAX_FILES_PER_REPO,
    DEFAULT_PER_REPO_TIMEOUT_S,
    map_with_timeout,
)
from . import prescreen
from .safe_paths import is_escaping_symlink
from .sanitize import redact_literals
from .repo_utils import (
    classify_frameworks,
    detect_build_systems,
    detect_git_sha,
    is_internal_coordinate,
    is_skipped_path,
    parse_package_json_dependencies,
    parse_pom_dependencies,
)

from .aggregators.graphs import aggregate_graphs_v2
from .aggregators.phase3 import aggregate_phase3_v2
from .aggregators.pii_flow_v2 import write_pii_flow_v2
from .aggregators.taint_catalogs import aggregate_taint_catalogs_v2
from .constants import MAX_FILE_BYTES, SKIP_DIRS, SOURCE_EXTENSIONS
from .extractors.config import extract_from_config, is_config_path
from .extractors.unified import extract_from_file
from .known_api_clients import get_bindings
from .renderers.markdown import merge_with_manual, render_repo_md_v2
from .schema import SCHEMA_VERSION, FlowEdge, FlowNode, RepoSummaryV2

ROOT = Path(__file__).resolve().parent.parent.parent

# Per-repo file-count cap, configured per worker via _worker_init (see D-4).
# A repo with more scannable files than this is truncated with a note rather
# than silently — protects against file-count amplification by a hostile repo.
_MAX_FILES_PER_REPO = DEFAULT_MAX_FILES_PER_REPO

# Per-file size cap in bytes, configured per worker via _worker_init (see D-4).
# A file larger than this is skipped with a recorded note rather than parsed;
# 0 disables the cap. Overridable with --max-file-bytes.
_MAX_FILE_BYTES = MAX_FILE_BYTES


def language_for_path(path: Path) -> str | None:
    """Return the extractor language id for a file's extension, or None if unsupported."""
    return SOURCE_EXTENSIONS.get(path.suffix.lower())


def is_skip_dir(name: str) -> bool:
    """True if a directory name should be excluded from scanning."""
    return name in SKIP_DIRS or (name.startswith(".") and name not in {".github"})


def iter_repo_files(repo_root: Path) -> Iterator[Path]:
    """Yield every scannable file under a repo, skipping excluded dirs and escaping symlinks.

    os.walk does not follow symlinked directories (followlinks defaults to
    False), but it still lists symlinked *files*. Skip any file symlink whose
    target escapes the repo so a crafted repo cannot exfiltrate outside content
    (e.g. ``x.java -> /etc/passwd``) into the metabase. See threat-model T-2.
    """
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if not is_skip_dir(d)]
        for fn in filenames:
            path = Path(dirpath) / fn
            if is_escaping_symlink(path, repo_root):
                continue
            yield path


def safe_read_text(path: Path) -> str | None:
    """Read a text file, returning None if it is oversized or unreadable.

    Bounds untrusted scanned-repo files to ``_MAX_FILE_BYTES`` before reading
    (0 disables the cap).
    """
    try:
        if _MAX_FILE_BYTES and path.stat().st_size > _MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _apply_max_file_bytes(max_file_bytes: int) -> None:
    """Propagate the per-file size cap to this process's read paths (0 disables).

    Sets both this module's ``_MAX_FILE_BYTES`` (used by ``safe_read_text`` on the
    node-producing scan path) and ``repo_utils.MAX_FILE_BYTES`` (manifest reads +
    the XML size gates) so a ``--max-file-bytes`` override applies uniformly.
    """
    global _MAX_FILE_BYTES  # noqa: PLW0603
    _MAX_FILE_BYTES = max_file_bytes
    from . import repo_utils
    repo_utils.MAX_FILE_BYTES = max_file_bytes


def repo_relpath(repo_root: Path, abs_path: Path) -> str:
    """Return abs_path relative to repo_root as a string, or the absolute path if unrelated."""
    try:
        return str(abs_path.relative_to(repo_root))
    except ValueError:
        return str(abs_path)


_GRADLE_DEP_RX = re.compile(
    r"(?:implementation|api|compile|runtimeOnly|testImplementation)\s*"
    r'[\(\s]+["\']'
    r"([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+)(?::([A-Za-z0-9_.\-+]+))?"
)


def _parse_gradle_deps(gradle_path: Path) -> list[dict[str, str]]:
    """Extract dependency coordinates from a Gradle build script via regex.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    text = safe_read_text(gradle_path)
    if not text:
        return []
    deps: list[dict[str, str]] = []
    for m in _GRADLE_DEP_RX.finditer(text):
        gid, aid, ver = m.group(1), m.group(2), m.group(3) or ""
        kind = "internal" if is_internal_coordinate(gid, aid) else "external"
        deps.append({"groupId": gid, "artifactId": aid, "version": ver, "kind": kind})
    return deps


# A version catalog holds the coordinate that the build script only references by
# alias, so without these the build file yields nothing at all (OI-3). Every run
# is length-bounded to stay linear on hostile input (tests/test_redos_bounds.py).
_CATALOG_TOML_RX = re.compile(
    r'^\s*([A-Za-z0-9_.\-]{1,120})\s*=\s*\{[^}\n]{0,200}?module\s*=\s*["\']'
    r'([A-Za-z0-9_.\-]{1,120}):([A-Za-z0-9_.\-]{1,120})["\']',
    re.MULTILINE,
)
_CATALOG_DSL_RX = re.compile(
    r'library\(\s*["\']([A-Za-z0-9_.\-]{1,120})["\']\s*,\s*["\']([A-Za-z0-9_.\-]{1,120})["\']'
    r'\s*,\s*["\']([A-Za-z0-9_.\-]{1,120})["\']'
)
_CATALOG_REF_RX = re.compile(
    r"\b(?:implementation|api|compile|runtimeOnly|testImplementation|compileOnly)"
    r"\s*[\(\s]\s*libs\.([A-Za-z0-9_.]{1,120})"
)
# A repo with more catalog files than this is pathological; parsing them all
# would turn a bounded read into an unbounded one.
_MAX_CATALOG_FILES = 20


def _normalise_alias(alias: str) -> str:
    """Reduce a catalog alias to its lookup key.

    Gradle exposes a catalog entry named ``warehouse-service-client`` to build
    scripts as ``libs.warehouseServiceClient``, so the declaration and the
    reference differ by both separator and case. Discarding both is what makes
    the two sides meet.
    """
    return alias.replace("-", "").replace(".", "").replace("_", "").lower()


def _parse_version_catalog(repo_root: Path) -> dict[str, tuple[str, str]]:
    """Map catalog alias -> (groupId, artifactId) from TOML and the settings DSL.

    Reads through ``safe_read_text`` (size-capped) and honours ``is_skipped_path``,
    like every other manifest read.
    """
    catalog: dict[str, tuple[str, str]] = {}
    candidates = [
        *sorted(repo_root.rglob("*.versions.toml")),
        *sorted(repo_root.rglob("settings.gradle.kts")),
        *sorted(repo_root.rglob("settings.gradle")),
    ][:_MAX_CATALOG_FILES]
    for path in candidates:
        if is_skipped_path(path, repo_root):
            continue
        text = safe_read_text(path) or ""
        for alias, gid, aid in _CATALOG_TOML_RX.findall(text):
            catalog.setdefault(_normalise_alias(alias), (gid, aid))
        for alias, gid, aid in _CATALOG_DSL_RX.findall(text):
            catalog.setdefault(_normalise_alias(alias), (gid, aid))
    return catalog


def _resolve_catalog_refs(
    gradle_paths: list[Path], catalog: dict[str, tuple[str, str]]
) -> tuple[list[dict[str, str]], int]:
    """Resolve `libs.<alias>` references against the catalog.

    Returns the resolved dependencies and the count of references that could not
    be resolved — the caller turns a non-zero count into a repo note, because a
    dependency list that silently degrades to empty is the failure shape this
    whole issue is about.
    """
    deps: list[dict[str, str]] = []
    unresolved = 0
    for path in gradle_paths:
        text = safe_read_text(path) or ""
        for alias in _CATALOG_REF_RX.findall(text):
            entry = catalog.get(_normalise_alias(alias))
            if entry is None:
                unresolved += 1
                continue
            gid, aid = entry
            deps.append({
                "groupId": gid,
                "artifactId": aid,
                "version": "",
                "kind": "internal" if is_internal_coordinate(gid, aid) else "external",
            })
    return deps, unresolved


def _collect_dependencies(repo_root: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Gather declared dependencies; returns (deps, notes).

    Notes carry anything that would otherwise make the dependency list quietly
    incomplete — currently unresolved version-catalog references.
    """
    deps: list[dict[str, str]] = []
    notes: list[str] = []
    for pom in repo_root.rglob("pom.xml"):
        if not is_skipped_path(pom, repo_root):
            deps.extend(parse_pom_dependencies(pom))
    gradle_paths = [
        g
        for g in list(repo_root.rglob("build.gradle")) + list(repo_root.rglob("build.gradle.kts"))
        if not is_skipped_path(g, repo_root)
    ]
    for gradle in gradle_paths:
        deps.extend(_parse_gradle_deps(gradle))
    if gradle_paths:
        catalog_deps, unresolved = _resolve_catalog_refs(
            gradle_paths, _parse_version_catalog(repo_root)
        )
        deps.extend(catalog_deps)
        if unresolved:
            notes.append(
                f"gradle version catalog unresolved: {unresolved} libs.* reference(s) "
                "in build.gradle* matched no catalog entry; dependencies may be "
                "incomplete (looked for *.versions.toml and settings.gradle*)"
            )
    for pkg in repo_root.rglob("package.json"):
        if not is_skipped_path(pkg, repo_root):
            deps.extend(parse_package_json_dependencies(pkg))
    return deps, notes


def _record_dependencies(summary: RepoSummaryV2, deps: list[dict[str, str]]) -> None:
    """De-duplicate deps by coordinate and split into internal / external count."""
    seen_coord: set[str] = set()
    for d in deps:
        key = f"{d.get('groupId', '')}:{d.get('artifactId', '')}"
        if key in seen_coord:
            continue
        seen_coord.add(key)
        if d["kind"] == "internal":
            summary.dependencies_internal.append(d)
        else:
            summary.dependencies_external_count += 1


def _scan_repo_files(
    repo_root: Path, repo_id: str, summary: RepoSummaryV2
) -> tuple[Counter[str], list[FlowNode], list[FlowEdge]]:
    """Extract flow nodes/edges from each scannable file (with cap + pre-screen)."""
    lang_counts: Counter[str] = Counter()
    all_nodes: list[FlowNode] = []
    all_edges: list[FlowEdge] = []
    scanned = 0
    for path in iter_repo_files(repo_root):
        if _MAX_FILES_PER_REPO and scanned >= _MAX_FILES_PER_REPO:
            summary.notes.append(
                f"file cap reached ({_MAX_FILES_PER_REPO}); remaining files skipped"
            )
            break
        scanned += 1
        rel = repo_relpath(repo_root, path)
        # Record oversized-file skips explicitly (they were previously silent —
        # see SAST "no silent caps"): a big generated/bundled file dropping tens
        # of thousands of nodes should tell the user which knob to turn.
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if _MAX_FILE_BYTES and size > _MAX_FILE_BYTES:
            summary.notes.append(
                f"skipped {rel}: file exceeds size cap "
                f"({size} > {_MAX_FILE_BYTES} bytes; raise --max-file-bytes)"
            )
            continue
        text = safe_read_text(path)
        if not text:
            continue

        # Skip suspicious/pathological files before they reach the parsers.
        skip_reason = prescreen.screen(path, text)
        if skip_reason:
            summary.notes.append(f"skipped {rel}: {skip_reason}")
            continue

        if is_config_path(path.name, path.suffix.lower()):
            cnodes, cedges = extract_from_config(repo_id=repo_id, rel_path=rel, source=text)
            all_nodes.extend(cnodes)
            all_edges.extend(cedges)
            continue

        lang = language_for_path(path)
        if not lang:
            continue
        lang_counts[lang] += 1
        nodes, edges = extract_from_file(
            repo_id=repo_id, rel_path=rel, language=lang, source=text
        )
        all_nodes.extend(nodes)
        all_edges.extend(edges)
    return lang_counts, all_nodes, all_edges


def analyse_repo_v2(repo_root: Path, group: str, name: str, path_rel: str) -> RepoSummaryV2:
    """Analyse one repo: git SHA, build systems, dependencies, and extracted flow nodes/edges."""
    repo_id = f"{group}/{name}"
    summary = RepoSummaryV2(
        group=group,
        name=name,
        path=path_rel,
        schema_version=SCHEMA_VERSION,
    )
    summary.git_sha = detect_git_sha(repo_root)
    summary.analysed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    summary.build_systems = detect_build_systems(repo_root)

    deps, dep_notes = _collect_dependencies(repo_root)
    summary.notes.extend(dep_notes)
    _record_dependencies(summary, deps)
    summary.frameworks = classify_frameworks(deps)

    lang_counts, all_nodes, all_edges = _scan_repo_files(repo_root, repo_id, summary)
    summary.language_breakdown = dict(lang_counts)
    if lang_counts:
        summary.primary_language = lang_counts.most_common(1)[0][0]
    summary.file_counts = dict(lang_counts)
    summary.nodes = all_nodes
    summary.edges = all_edges
    return summary


# Free-text node detail fields that may incidentally capture literal PII/secrets
# from source and must be redacted before they are persisted (see PRV-NEW-2 and
# SAST finding 4). Redacting here — once, at the source — means every downstream
# JSONL/Markdown writer inherits the masking. Symbol/field *names* are deliberately
# excluded: they are the tool's output, not values (and redact_literals is a no-op
# on a bare identifier anyway).
_REDACT_DETAIL_FIELDS = ("snippet", "raw", "url", "bucket", "endpoint_path")


def summary_to_dict(summary: RepoSummaryV2) -> dict[str, Any]:
    """Serialize a repo summary to a JSON-ready dict, redacting literal PII/secret fields."""
    d = dataclasses.asdict(summary)
    node_dicts = [dataclasses.asdict(n) for n in summary.nodes]
    for node in node_dicts:
        detail = node.get("detail")
        if isinstance(detail, dict):
            for f in _REDACT_DETAIL_FIELDS:
                if isinstance(detail.get(f), str):
                    detail[f] = redact_literals(detail[f])
    d["nodes"] = node_dicts
    d["edges"] = [dataclasses.asdict(e) for e in summary.edges]
    return d


def _read_existing_sha(json_path: Path) -> str | None:
    """Return the git_sha recorded in a prior run's JSON output, if present and readable."""
    if not json_path.is_file():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("git_sha") or None


def process_one_v2(args: tuple[Path, str, str, Path, bool]) -> dict[str, Any] | None:
    """Worker unit of work: analyse one repo (unless unchanged) and write its JSON/Markdown."""
    repo_root, group, name, metabase_root, force = args
    json_path = metabase_root / "repos" / group / f"{name}.json"

    if not force:
        current_sha = detect_git_sha(repo_root)
        if current_sha and current_sha == _read_existing_sha(json_path):
            return {"_skipped": True, "group": group, "name": name}

    try:
        path_rel = str(repo_root.relative_to(repo_root.parent.parent))
    except ValueError:
        path_rel = str(repo_root)
    try:
        summary = analyse_repo_v2(repo_root, group, name, path_rel)
    except Exception as exc:  # pragma: no cover
        # Report only the exception *type* + repo id — never the message, which
        # could carry file paths or scanned content into CI logs (I-2).
        return {"_error": True, "group": group, "name": name, "error": type(exc).__name__}

    out_dir = metabase_root / "repos" / group
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(summary_to_dict(summary), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_text = render_repo_md_v2(summary)
    md_path.write_text(merge_with_manual(md_path, md_text), encoding="utf-8")

    families = Counter(n.family for n in summary.nodes)
    return {
        "group": summary.group,
        "name": summary.name,
        "path": summary.path,
        "git_sha": summary.git_sha,
        "schema_version": summary.schema_version,
        "primary_language": summary.primary_language,
        "frameworks": summary.frameworks,
        "nodes": len(summary.nodes),
        "edges": len(summary.edges),
        "sql_sinks": families.get("sql", 0),
        "file_sinks": families.get("file", 0),
        "http_out": families.get("http-out", 0),
        "pii_log": families.get("pii-log", 0),
        "raw_code_payload": families.get("raw-code-payload", 0),
    }


def _worker_init(
    pattern_strings: list[str],
    api_clients_path: str,
    max_files_per_repo: int,
    max_file_bytes: int,
    max_line_bytes: int,
    prescreen_indicators: tuple[str, ...],
) -> None:
    """Multiprocessing pool initializer: configure this worker's global scan settings."""
    from .internal_groups import configure_internal_group_patterns
    from .known_api_clients import configure_from_path
    global _MAX_FILES_PER_REPO  # noqa: PLW0603
    _MAX_FILES_PER_REPO = max_files_per_repo
    _apply_max_file_bytes(max_file_bytes)
    prescreen.configure_max_line_bytes(max_line_bytes)
    prescreen.configure_indicators(prescreen_indicators)
    configure_internal_group_patterns(pattern_strings)
    if api_clients_path:
        # The parent already validated the file and reported any problem; a
        # worker must not duplicate the warning or fail the pool.
        configure_from_path(api_clients_path, allow_empty=True)


def _tool_version() -> str:
    """Return the installed src2sink package version, or "unknown" if not installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("src2sink")
    except PackageNotFoundError:  # pragma: no cover - only when not installed
        return "unknown"


def _write_run_manifest(
    metabase_root: Path,
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    *,
    skipped: int,
    timed_out: int,
    started_at: str,
    finished_at: str,
) -> None:
    """Record run provenance for reproducibility / GDPR Art. 30 (finding R-1).

    Captures the tool version, a *secret-free* summary of the invocation, the
    per-repo SHAs of what was (re)built this run, and counts. No absolute paths
    or config contents are recorded — only basenames/booleans.
    """
    manifest = {
        "tool": "src2sink",
        "tool_version": _tool_version(),
        "started_at": started_at,
        "finished_at": finished_at,
        "invocation": {
            "repos_root": Path(args.repos_root).name,
            "metabase_root": Path(args.metabase_root).name,
            "workers": args.workers,
            "repo_timeout_s": args.repo_timeout,
            "max_files_per_repo": args.max_files_per_repo,
            "max_file_bytes": args.max_file_bytes,
            "max_line_bytes": args.max_line_bytes,
            "force": bool(args.force),
            "repo_filter": args.repo or None,
            "limit": args.limit or None,
            "api_clients_configured": bool(args.api_clients),
            # The boolean above only records that a path was passed. The count is
            # what tells you detection was actually enabled — a run with the flag
            # set and 0 bindings loaded looked identical to a healthy one before
            # (report §2).
            "api_clients_binding_count": len(get_bindings()),
            "prescreen_indicators_configured": bool(args.prescreen_indicators),
        },
        "counts": {
            "updated": len(rows),
            "skipped": skipped,
            "timed_out": timed_out,
        },
        "updated_repos": sorted(
            (
                {"repo": f"{r['group']}/{r['name']}", "git_sha": r.get("git_sha")}
                for r in rows
            ),
            key=lambda e: str(e["repo"]),
        ),
    }
    (metabase_root / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _load_v2_jsons(metabase_root: Path) -> list[Path]:
    """Return paths to every repo JSON under metabase_root matching the current schema version."""
    valid: list[Path] = []
    for jp in sorted(metabase_root.glob("repos/*/*.json")):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema_version") == SCHEMA_VERSION:
            valid.append(jp)
    return valid


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for build_metabase_v2's extraction/aggregation modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-root", required=True)
    parser.add_argument("--metabase-root", required=True)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument(
        "--repo-timeout",
        type=int,
        default=DEFAULT_PER_REPO_TIMEOUT_S,
        help="Per-repo wall-clock timeout in seconds; a repo exceeding it is "
        "killed and skipped (0 disables the timeout).",
    )
    parser.add_argument(
        "--max-files-per-repo",
        type=int,
        default=DEFAULT_MAX_FILES_PER_REPO,
        help="Cap on files scanned per repo; excess files are skipped with a "
        "note (0 disables the cap).",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=MAX_FILE_BYTES,
        help="Per-file size cap in bytes; a larger file is skipped with a note "
        f"(default {MAX_FILE_BYTES}, 0 disables the cap).",
    )
    parser.add_argument(
        "--prescreen-indicators",
        default=None,
        help="Path to a text file of content indicators (one substring per "
        "line, '#' comments); files containing one are skipped before parsing. "
        "Structural checks (binary/minified) always run regardless.",
    )
    parser.add_argument(
        "--max-line-bytes",
        type=int,
        default=prescreen.MAX_LINE_BYTES,
        help="Pre-screen threshold: a file with any single line longer than this "
        "(minified/obfuscated) is skipped with a note "
        f"(default {prescreen.MAX_LINE_BYTES}, 0 disables the check).",
    )
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="Re-render v2 taint catalogues from existing schema_version=2 JSONs only.",
    )
    parser.add_argument(
        "--graphs-only",
        action="store_true",
        help="Regenerate graphs/ from v2 JSONs (implies aggregate-only for graphs).",
    )
    parser.add_argument(
        "--phase3-only",
        action="store_true",
        help="Regenerate Phase 3 outputs only (PII lifecycle, ROPA, auth/crypto cards).",
    )
    parser.add_argument(
        "--no-phase3",
        action="store_true",
        help="Skip Phase 3 aggregators when rebuilding graphs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract all repos even if git SHA matches the last run.",
    )
    parser.add_argument(
        "--fix-source-map",
        action="store_true",
        help="Only resolve flagged library-source-map entries from on-disk "
        "pom.xml groupId/artifactId, then exit (no extraction).",
    )
    add_internal_groups_arguments(parser)
    parser.add_argument("--api-clients", default=None, help="Path to api-clients.json")
    parser.add_argument(
        "--allow-empty-api-clients",
        action="store_true",
        help="Continue when --api-clients loads 0 bindings. Without this, an "
        "empty/malformed bindings file is a hard error, because it silently "
        "disables all cross-repo API-client detection.",
    )
    parser.add_argument(
        "--discover-api-clients",
        action="store_true",
        help="After aggregation, draft candidate api-client bindings into "
        "metabase/api-clients.discovered.json for review (never authoritative).",
    )
    parser.add_argument(
        "--promote-api-clients",
        action="store_true",
        help="Only merge 'accepted' candidates from "
        "metabase/api-clients.discovered.json into --api-clients (default "
        "api-clients.json), then exit (no extraction).",
    )
    return parser


def _configure_api_clients(
    api_clients_path: str | None, *, allow_empty: bool = False
) -> int:
    """Load and globally configure API-client bindings; return the binding count.

    A file that yields zero bindings is a hard error unless ``allow_empty``: it
    silently disables every cross-repo client-detection path while the run still
    reports success, which is how ~24 of 25 callers of one service went missing
    (report §3.1). ``--allow-empty-api-clients`` is the explicit opt-out.
    """
    if not api_clients_path:
        return 0
    from .known_api_clients import ApiClientConfigError, configure_from_path
    # warn=True only here (the single top-level load) so a malformed sensitive
    # config is surfaced once, not silently ignored (I-3) and not per-worker.
    try:
        bindings = configure_from_path(
            api_clients_path, warn=True, allow_empty=allow_empty
        )
    except ApiClientConfigError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    if not bindings:
        print(
            f"WARNING: api-clients file '{Path(api_clients_path).name}' loaded "
            "0 bindings; cross-repo API-client detection is disabled "
            "(--allow-empty-api-clients)",
            file=sys.stderr,
        )
    return len(bindings)


def _discover_repos(
    repos_root: Path, metabase_root: Path, args: argparse.Namespace, force: bool
) -> list[tuple[Path, str, str, Path, bool]]:
    """Enumerate (repo_dir, group, name) tuples under repos_root, applying repo filter and limit."""
    discovered: list[tuple[Path, str, str, Path, bool]] = []
    for group_dir in sorted(repos_root.iterdir()):
        if not group_dir.is_dir() or group_dir.name in SKIP_DIRS:
            continue
        for repo_dir in sorted(group_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name in SKIP_DIRS:
                continue
            rel = f"{group_dir.name}/{repo_dir.name}"
            if args.repo and args.repo not in rel and not rel.endswith(args.repo):
                continue
            discovered.append(
                (repo_dir, group_dir.name, repo_dir.name, metabase_root, force)
            )
    if args.limit:
        discovered = discovered[: args.limit]
    return discovered


def _generate_source_map(
    metabase_root: Path, jsons: list[Path], repos_root: Path, *, prefix: str
) -> None:
    """Regenerate the library source map, then resolve any flagged entries from on-disk manifests."""
    from .aggregators.library_source_map import (
        fix_flagged_mappings,
        generate_library_source_map,
    )

    generate_library_source_map(metabase_root, jsons)
    fixed = fix_flagged_mappings(metabase_root, repos_root)
    if fixed:
        print(f"{prefix}resolved {fixed} flagged source-map entries from pom.xml")


def _run_aggregate_only(
    metabase_root: Path, repos_root: Path, args: argparse.Namespace
) -> int:
    """Re-render aggregator outputs from existing v2 JSONs without re-scanning repos."""
    jsons = _load_v2_jsons(metabase_root)
    if not jsons:
        print(
            "No schema_version=2 JSONs found. Run build_metabase_v2.py "
            "without --aggregate-only first.",
            file=sys.stderr,
        )
        return 2
    print(f"Aggregate-only: {len(jsons)} v2 JSONs")
    if args.phase3_only:
        write_pii_flow_v2(metabase_root, jsons)
        aggregate_phase3_v2(metabase_root, jsons)
    else:
        if not args.graphs_only:
            aggregate_taint_catalogs_v2(metabase_root, jsons)
        aggregate_graphs_v2(
            metabase_root, jsons, repos_root=repos_root, phase3=not args.no_phase3
        )
        _generate_source_map(metabase_root, jsons, repos_root, prefix="  ")
    print("Done.")
    return 0


def _classify_result(
    r: dict[str, Any], args: argparse.Namespace
) -> str:
    """Print a per-repo result line and return its category for counting."""
    if r.get("_timeout"):
        print(
            f"  TIMEOUT {r.get('group', '?')}/{r.get('name', '?')} "
            f"(exceeded {args.repo_timeout}s) — skipped",
            file=sys.stderr,
        )
        return "timed_out"
    if r.get("_skipped"):
        print(f"  skip  {r['group']}/{r['name']} (unchanged)")
        return "skipped"
    if r.get("_error"):
        print(
            f"  ERROR {r.get('group', '?')}/{r.get('name', '?')}: {r['error']}",
            file=sys.stderr,
        )
        return "error"
    print(f"  done  {r['group']}/{r['name']} nodes={r['nodes']}")
    return "done"


def _dispatch_and_collect(
    discovered: list[tuple[Path, str, str, Path, bool]],
    args: argparse.Namespace,
    pattern_strings: list[str],
    prescreen_indicators: tuple[str, ...],
) -> tuple[list[dict[str, Any]], int, int]:
    """Run extraction across repos with the per-repo bulkhead; collect results."""
    rows: list[dict[str, Any]] = []
    skipped = 0
    timed_out = 0

    def _on_timeout(item: tuple[Path, str, str, Path, bool]) -> dict[str, Any]:
        """Build the timeout-marker result for a repo whose worker was killed."""
        return {"_timeout": True, "group": item[1], "name": item[2]}

    for r in map_with_timeout(
        process_one_v2,
        discovered,
        workers=args.workers,
        timeout=args.repo_timeout,
        initializer=_worker_init,
        initargs=(
            pattern_strings,
            args.api_clients or "",
            args.max_files_per_repo,
            args.max_file_bytes,
            args.max_line_bytes,
            prescreen_indicators,
        ),
        on_timeout=_on_timeout,
    ):
        if not r:
            continue
        category = _classify_result(r, args)
        if category == "timed_out":
            timed_out += 1
        elif category == "skipped":
            skipped += 1
        elif category == "done":
            rows.append(r)
    return rows, skipped, timed_out


def _limits_hit_summary(json_paths: list[Path], timed_out: int) -> str:
    """Return a one-line hint naming every scan limit that skipped content.

    Scans the persisted repo ``notes`` for the cap markers plus the timeout
    counter, so the run-end log tells the user content was dropped and which
    knob adjusts each limit. Returns "" when nothing was capped.
    """
    hits: set[str] = set()
    for jp in json_paths:
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for note in data.get("notes", []):
            if "file exceeds size cap" in note:
                hits.add("per-file size cap (--max-file-bytes)")
            elif "file cap reached" in note:
                hits.add("per-repo file cap (--max-files-per-repo)")
            elif "oversized line" in note:
                hits.add("minified/oversized-line pre-screen (--max-line-bytes)")
    if timed_out:
        hits.add("per-repo timeout (--repo-timeout)")
    if not hits:
        return ""
    return "  ⚠ limits hit — content was skipped: " + "; ".join(sorted(hits))


def main() -> int:
    """CLI entry point: discover repos, extract in parallel, aggregate, and write the run manifest."""
    args = _build_arg_parser().parse_args()

    repos_root = Path(args.repos_root).resolve()
    metabase_root = Path(args.metabase_root).resolve()
    pattern_strings = apply_internal_groups_from_args(args)
    # Apply the size cap in the parent too, so aggregation-phase manifest reads
    # (identity index) honour a --max-file-bytes override; workers set it again.
    _apply_max_file_bytes(args.max_file_bytes)
    # Skip when promoting: that flow *writes* --api-clients, so pre-loading the
    # not-yet-created file would emit a spurious "0 bindings loaded" warning.
    if not args.promote_api_clients:
        _configure_api_clients(
            args.api_clients, allow_empty=args.allow_empty_api_clients
        )

    if not repos_root.is_dir():
        print(f"repos-root not a directory: {repos_root}", file=sys.stderr)
        return 2
    metabase_root.mkdir(parents=True, exist_ok=True)

    if args.fix_source_map:
        from .aggregators.library_source_map import fix_flagged_mappings
        fixed = fix_flagged_mappings(metabase_root, repos_root)
        print(f"Resolved {fixed} flagged source-map entries from pom.xml")
        return 0

    if args.promote_api_clients:
        from .aggregators.api_client_discovery import promote_api_clients
        target = Path(args.api_clients) if args.api_clients else Path("api-clients.json")
        merged = promote_api_clients(metabase_root, target)
        print(f"Promoted {merged} accepted candidate binding(s) into {target.name}")
        return 0

    if args.aggregate_only or args.graphs_only or args.phase3_only:
        return _run_aggregate_only(metabase_root, repos_root, args)

    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    force = args.force
    discovered = _discover_repos(repos_root, metabase_root, args, force)

    # Load pre-screen indicators once in the parent; pass the list to workers.
    prescreen_indicators: tuple[str, ...] = ()
    if args.prescreen_indicators:
        prescreen_indicators = prescreen.load_indicators(Path(args.prescreen_indicators))
        print(
            f"loaded {len(prescreen_indicators)} pre-screen indicator(s) from "
            f"'{Path(args.prescreen_indicators).name}'"
        )

    print(
        f"v2: {len(discovered)} repos, workers={args.workers}, "
        f"repo-timeout={args.repo_timeout}s{' (force)' if force else ''}"
    )
    rows, skipped, timed_out = _dispatch_and_collect(
        discovered, args, pattern_strings, prescreen_indicators
    )

    jsons = _load_v2_jsons(metabase_root)
    if jsons:
        aggregate_taint_catalogs_v2(metabase_root, jsons)
        aggregate_graphs_v2(metabase_root, jsons, repos_root=repos_root)
        _generate_source_map(metabase_root, jsons, repos_root, prefix="")
        if args.discover_api_clients:
            from .aggregators.api_client_discovery import (
                DISCOVERED_FILE,
                discover_api_clients,
            )
            n = discover_api_clients(metabase_root, jsons, repos_root)
            print(f"Discovered {n} candidate api-client binding(s) → metabase/{DISCOVERED_FILE}")
    _write_run_manifest(
        metabase_root,
        args,
        rows,
        skipped=skipped,
        timed_out=timed_out,
        started_at=started_at,
        finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )
    print(
        f"Finished {len(rows)} updated, {skipped} skipped, {timed_out} timed out, "
        f"{len(jsons)} v2 JSONs for aggregation."
        f"{_limits_hit_summary(jsons, timed_out)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
