"""Index repos that produce traffic to dangerous-payload HTTP endpoints."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..graph_common import (
    confidence_rank,
    host_matches_repo,
    load_v2_repo_records,
    repo_id,
)
from ..known_api_clients import ApiClientBinding, get_bindings
from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE

IMPORT_SCAN_RX = re.compile(
    r"^\s*import\s+([\w.]+)|"
    r"import\s+([\w.]+)",
    re.MULTILINE,
)


@dataclass
class ProducerHit:
    """A single evidence that one repo produces traffic to a payload endpoint."""

    source_repo: str
    target_repo: str
    path: str
    kind: str
    confidence: str
    evidence: str
    ref: str = ""


@dataclass
class ProducerIndex:
    """Per-target index: an API-client binding and the producer hits found for it."""

    binding: ApiClientBinding
    hits: list[ProducerHit] = field(default_factory=list)


def _internal_dep_hit(
    rid: str, binding: ApiClientBinding, data: dict[str, Any]
) -> ProducerHit | None:
    """Return a hit if the repo declares an internal dependency on the client artifact."""
    for dep in data.get("dependencies_internal", []):
        aid = dep.get("artifactId", "")
        if binding.maven_artifact in aid:
            return ProducerHit(
                source_repo=rid,
                target_repo=binding.target_repo,
                path="*",
                kind="internal-dep",
                confidence="high",
                evidence=f"{dep.get('groupId')}:{aid}",
            )
    return None


def _api_client_consumer_hit(
    rid: str, binding: ApiClientBinding, detail: dict[str, Any], ref: str
) -> ProducerHit | None:
    """Return a hit if an `api-client-consumer` node targets the binding's repo."""
    if detail.get("target_repo") != binding.target_repo:
        return None
    return ProducerHit(
        source_repo=rid,
        target_repo=binding.target_repo,
        path=",".join(detail.get("paths", [])) or "*",
        kind="import",
        confidence="high",
        evidence=detail.get("import", "")[:120],
        ref=ref,
    )


def _http_out_hit(
    rid: str, binding: ApiClientBinding, detail: dict[str, Any], ref: str
) -> ProducerHit | None:
    """Return a hit if an `http-out` node's host or path matches the binding's target."""
    host = detail.get("host", "")
    path = detail.get("path", "")
    if host and host_matches_repo(host, binding.target_repo):
        return ProducerHit(
            source_repo=rid,
            target_repo=binding.target_repo,
            path=path or "*",
            kind="http-out",
            confidence="high" if path else "medium",
            evidence=detail.get("url") or detail.get("raw", "")[:120],
            ref=ref,
        )
    if path and any(
        path.rstrip("/").endswith(p.rstrip("/").replace("{handle}", ""))
        or p in (detail.get("paths") or [])
        for p in binding.paths
    ):
        return ProducerHit(
            source_repo=rid,
            target_repo=binding.target_repo,
            path=path,
            kind="http-out-path",
            confidence="medium",
            evidence=detail.get("raw", "")[:120],
            ref=ref,
        )
    return None


def _hits_from_repo_json(data: dict[str, Any], binding: ApiClientBinding) -> list[ProducerHit]:
    """Collect all producer hits for one binding from a single repo's v2 JSON."""
    rid = repo_id(data)
    if rid == binding.target_repo:
        return []
    hits: list[ProducerHit] = []
    dep_hit = _internal_dep_hit(rid, binding, data)
    if dep_hit:
        hits.append(dep_hit)

    for node in data.get("nodes", []):
        family = node.get("family", "")
        detail = node.get("detail") or {}
        ref = f"{node.get('file')}:{node.get('line')}"
        if family == "api-client-consumer":
            hit = _api_client_consumer_hit(rid, binding, detail, ref)
        elif family == "http-out":
            hit = _http_out_hit(rid, binding, detail, ref)
        else:
            hit = None
        if hit:
            hits.append(hit)
    return hits


_BINDING_SCAN_SUFFIXES = frozenset({".java", ".kt", ".kts", ".xml", ".gradle"})
_BINDING_SCAN_SKIP = frozenset({"node_modules", ".git", "target", "build"})
_BUILD_FILE_NAMES = ("pom.xml", "build.gradle", "build.gradle.kts")


def _read_capped(path: Path, cap: int = 400_000) -> str | None:
    """Read a file as text, returning None if it exceeds the byte cap or errors."""
    try:
        if path.stat().st_size > cap:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _import_hits_in_file(
    text: str, path: Path, repo_dir: Path, src: str,
    binding: ApiClientBinding, seen: set[tuple[str, str]],
) -> list[ProducerHit]:
    """Scan one file's imports for the binding's import prefix, deduped via `seen`."""
    hits: list[ProducerHit] = []
    for m in IMPORT_SCAN_RX.finditer(text):
        pkg = m.group(1) or m.group(2) or ""
        if binding.import_prefix in pkg and (src, "import") not in seen:
            seen.add((src, "import"))
            line = text.count("\n", 0, m.start()) + 1
            hits.append(ProducerHit(
                source_repo=src,
                target_repo=binding.target_repo,
                path="*",
                kind="import-scan",
                confidence="high",
                evidence=f"import {pkg}",
                ref=f"{path.relative_to(repo_dir)}:{line}",
            ))
    return hits


def _scan_repo_for_binding(
    repo_dir: Path, src: str, binding: ApiClientBinding,
    seen: set[tuple[str, str]], pom_rx: re.Pattern[str], gradle_rx: re.Pattern[str],
) -> list[ProducerHit]:
    """Scan a repo directory tree for imports and build-file references to the binding."""
    hits: list[ProducerHit] = []
    found_pom = False
    for path in repo_dir.rglob("*"):
        if path.suffix.lower() not in _BINDING_SCAN_SUFFIXES:
            continue
        if any(p in path.parts for p in _BINDING_SCAN_SKIP):
            continue
        text = _read_capped(path)
        if text is None:
            continue
        if path.name in _BUILD_FILE_NAMES and (pom_rx.search(text) or gradle_rx.search(text)):
            found_pom = True
        hits.extend(_import_hits_in_file(text, path, repo_dir, src, binding, seen))

    if found_pom and (src, "pom") not in seen:
        seen.add((src, "pom"))
        hits.append(ProducerHit(
            source_repo=src,
            target_repo=binding.target_repo,
            path="*",
            kind="build-dep-scan",
            confidence="high",
            evidence=binding.maven_artifact,
        ))
    return hits


def _scan_repos_for_binding(
    repos_root: Path,
    binding: ApiClientBinding,
) -> list[ProducerHit]:
    """Scan every repo under `repos_root` for source-level uses of the binding."""
    if not repos_root.is_dir():
        return []
    hits: list[ProducerHit] = []
    seen: set[tuple[str, str]] = set()
    pom_rx = re.compile(
        rf"<artifactId>({re.escape(binding.maven_artifact)})</artifactId>",
        re.IGNORECASE,
    )
    gradle_rx = re.compile(re.escape(binding.maven_artifact), re.IGNORECASE)

    for group_dir in repos_root.iterdir():
        if not group_dir.is_dir():
            continue
        for repo_dir in group_dir.iterdir():
            if not repo_dir.is_dir():
                continue
            src = f"{group_dir.name}/{repo_dir.name}"
            if src != binding.target_repo:
                hits.extend(
                    _scan_repo_for_binding(repo_dir, src, binding, seen, pom_rx, gradle_rx)
                )
    return hits


def build_producer_indices(
    metabase_root: Path,
    *,
    repos_root: Path | None = None,
    json_paths: list[Path] | None = None,
) -> list[ProducerIndex]:
    """Build a producer index per known API-client binding.

    Merges v2-JSON hits with optional source-tree scan hits, keeping the
    highest-confidence hit per (repo, kind).

    Args:
        metabase_root: Metabase root holding per-repo v2 JSON.
        repos_root: Optional checkout root to scan for imports/build deps.
        json_paths: Explicit v2 JSON paths (else discovered under metabase_root).

    Returns:
        One ProducerIndex per binding, hits sorted by (source_repo, kind).
    """
    records = load_v2_repo_records(metabase_root, json_paths=json_paths)
    indices: list[ProducerIndex] = []

    for binding in get_bindings():
        by_repo: dict[str, list[ProducerHit]] = defaultdict(list)

        for data in records:
            for hit in _hits_from_repo_json(data, binding):
                by_repo[hit.source_repo].append(hit)

        if repos_root:
            for hit in _scan_repos_for_binding(repos_root, binding):
                by_repo[hit.source_repo].append(hit)

        # Merge per repo: keep highest confidence per kind
        merged: list[ProducerHit] = []
        for src, repo_hits in sorted(by_repo.items()):
            kinds: dict[str, ProducerHit] = {}
            for h in repo_hits:
                prev = kinds.get(h.kind)
                if prev is None or _conf_rank(h.confidence) > _conf_rank(prev.confidence):
                    kinds[h.kind] = h
            merged.extend(kinds.values())

        indices.append(ProducerIndex(binding=binding, hits=sorted(
            merged,
            key=lambda h: (h.source_repo, h.kind),
        )))

    return indices


def _conf_rank(c: str) -> int:
    """Map a confidence label to a sortable integer rank.

    Delegates so there is one definition of "stronger evidence"; this module
    and `trace` were comparing confidences against separate copies of it.
    """
    return confidence_rank(c)


def write_payload_producer_index(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    repos_root: Path | None = None,
) -> None:
    """Write the payload-endpoint-producers markdown + jsonl catalogue."""
    root = repos_root
    if root is None:
        candidate = metabase_root.parent / "repos"
        root = candidate if candidate.is_dir() else None

    indices = build_producer_indices(
        metabase_root,
        repos_root=root,
        json_paths=repo_jsons,
    )

    out_dir = metabase_root / "graphs"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "payload-endpoint-producers.jsonl"
    all_records: list[dict[str, Any]] = []

    md: list[str] = [
        "# Payload endpoint producers\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Repos that call dangerous-payload HTTP APIs via published clients, "
        "declared dependencies, or enriched `http-out` nodes. Built from "
        "`known_api_clients.py` + optional `repos/` import scan._\n",
    ]

    for index in indices:
        binding = index.binding
        md.append(f"\n## Target: `{binding.target_repo}`\n")
        md.append(
            f"Client: `{binding.maven_artifact}` · paths: "
            + ", ".join(f"`{p}`" for p in binding.paths)
            + "\n",
        )
        if not index.hits:
            md.append("_No producers indexed._\n")
            continue

        md.append(md_table(
            ["Producer repo", "Kind", "Path", "Confidence", "Evidence", "Ref"],
            [
                [
                    h.source_repo,
                    h.kind,
                    h.path,
                    h.confidence,
                    h.evidence[:90],
                    h.ref,
                ]
                for h in index.hits[:200]
            ],
        ))
        if len(index.hits) > 200:
            md.append(f"\n_{len(index.hits) - 200} more in jsonl._\n")

        for h in index.hits:
            rec = {
                "target_repo": h.target_repo,
                "source_repo": h.source_repo,
                "path": h.path,
                "kind": h.kind,
                "confidence": h.confidence,
                "evidence": h.evidence,
                "ref": h.ref,
                "client_artifact": binding.maven_artifact,
            }
            all_records.append(rec)

    (out_dir / "payload-endpoint-producers.md").write_text("\n".join(md), encoding="utf-8")
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for rec in all_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
