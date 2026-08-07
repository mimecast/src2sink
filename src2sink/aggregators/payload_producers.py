"""Index repos that produce traffic to dangerous-payload HTTP endpoints."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator
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


@dataclass(frozen=True)
class _BindingMatcher:
    """One binding with its build-file patterns compiled once."""

    binding: ApiClientBinding
    pom_rx: re.Pattern[str]
    gradle_rx: re.Pattern[str]


def _matchers(bindings: list[ApiClientBinding]) -> list[_BindingMatcher]:
    """Compile the per-binding build-file patterns once for the whole walk."""
    return [
        _BindingMatcher(
            binding=b,
            pom_rx=re.compile(
                rf"<artifactId>({re.escape(b.maven_artifact)})</artifactId>",
                re.IGNORECASE,
            ),
            gradle_rx=re.compile(re.escape(b.maven_artifact), re.IGNORECASE),
        )
        for b in bindings
    ]


def _import_hits_in_file(
    text: str, path: Path, repo_dir: Path, src: str,
    binding: ApiClientBinding, seen: set[tuple[str, str]],
) -> list[ProducerHit]:
    """Scan one file's imports for the binding's import prefix, deduped via `seen`."""
    return _import_hits_from_scanned(
        _scan_imports(text), text, path, repo_dir, src, binding, seen,
    )


def _scan_imports(text: str) -> list[tuple[str, int]]:
    """Every imported package in a file, with the offset it was found at.

    Run **once per file** rather than once per binding. `IMPORT_SCAN_RX` was
    inside the per-binding loop, so a fleet with ten bindings matched the same
    regex over the same text ten times.
    """
    out: list[tuple[str, int]] = []
    for m in IMPORT_SCAN_RX.finditer(text):
        pkg = m.group(1) or m.group(2) or ""
        if pkg:
            out.append((pkg, m.start()))
    return out


def _import_hits_from_scanned(
    imports: list[tuple[str, int]], text: str, path: Path, repo_dir: Path,
    src: str, binding: ApiClientBinding, seen: set[tuple[str, str]],
) -> list[ProducerHit]:
    """Turn already-scanned imports into hits for one binding."""
    hits: list[ProducerHit] = []
    for pkg, offset in imports:
        if binding.import_prefix in pkg and (src, "import") not in seen:
            seen.add((src, "import"))
            line = text.count("\n", 0, offset) + 1
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


def _iter_scannable_files(repo_dir: Path) -> Iterator[tuple[Path, str]]:
    """Yield each scannable file in a repo with its text, reading each one once."""
    for path in repo_dir.rglob("*"):
        if path.suffix.lower() not in _BINDING_SCAN_SUFFIXES:
            continue
        if any(p in path.parts for p in _BINDING_SCAN_SKIP):
            continue
        text = _read_capped(path)
        if text is None:
            continue
        yield path, text


def _scan_repo_for_bindings(
    repo_dir: Path, src: str, matchers: list[_BindingMatcher],
    seen: list[set[tuple[str, str]]],
) -> list[list[ProducerHit]]:
    """Scan one repo once, matching every binding against each file as it is read.

    The inversion that `OI-30` is about. Each file was previously read from disk
    once *per binding*, so a fleet with ten bindings was read ten times over and
    the only thing that differed between passes was which regex ran against text
    already in memory.
    """
    hits: list[list[ProducerHit]] = [[] for _ in matchers]
    found_pom = [False] * len(matchers)

    for path, text in _iter_scannable_files(repo_dir):
        imports = _scan_imports(text)
        is_build_file = path.name in _BUILD_FILE_NAMES
        for i, matcher in enumerate(matchers):
            if src == matcher.binding.target_repo:
                continue
            if is_build_file and _names_artifact(matcher, text):
                found_pom[i] = True
            hits[i].extend(_import_hits_from_scanned(
                imports, text, path, repo_dir, src, matcher.binding, seen[i],
            ))

    for i, matcher in enumerate(matchers):
        pom_hit = _build_dep_hit(matcher, src, found=found_pom[i], seen=seen[i])
        if pom_hit is not None:
            hits[i].append(pom_hit)
    return hits


def _names_artifact(matcher: _BindingMatcher, text: str) -> bool:
    """Whether a build file declares a dependency on the binding's artifact."""
    return bool(matcher.pom_rx.search(text) or matcher.gradle_rx.search(text))


def _build_dep_hit(
    matcher: _BindingMatcher, src: str, *, found: bool, seen: set[tuple[str, str]],
) -> ProducerHit | None:
    """The one build-dependency hit a repo contributes to a binding, if any."""
    if not found or (src, "pom") in seen:
        return None
    seen.add((src, "pom"))
    return ProducerHit(
        source_repo=src,
        target_repo=matcher.binding.target_repo,
        path="*",
        kind="build-dep-scan",
        confidence="high",
        evidence=matcher.binding.maven_artifact,
    )


def scan_repos_for_bindings(
    repos_root: Path,
    bindings: list[ApiClientBinding],
) -> list[list[ProducerHit]]:
    """Walk the fleet **once**, returning the hits for each binding in order."""
    hits: list[list[ProducerHit]] = [[] for _ in bindings]
    if not repos_root.is_dir() or not bindings:
        return hits

    matchers = _matchers(bindings)
    # `seen` stays per binding: the dedup key is (repo, kind) *for that binding*,
    # so sharing one set across bindings would let the first binding to match a
    # repo suppress every other binding's hit in it.
    seen: list[set[tuple[str, str]]] = [set() for _ in bindings]

    for group_dir in repos_root.iterdir():
        if not group_dir.is_dir():
            continue
        for repo_dir in group_dir.iterdir():
            if not repo_dir.is_dir():
                continue
            src = f"{group_dir.name}/{repo_dir.name}"
            for i, repo_hits in enumerate(
                _scan_repo_for_bindings(repo_dir, src, matchers, seen)
            ):
                hits[i].extend(repo_hits)
    return hits


def build_producer_indices(
    metabase_root: Path,
    *,
    repos_root: Path | None = None,
    json_paths: list[Path] | None = None,
    records: list[dict[str, Any]] | None = None,
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
    if records is None:
        records = load_v2_repo_records(metabase_root, json_paths=json_paths)
    bindings = list(get_bindings())

    # One walk of the fleet for every binding, rather than one walk each. The
    # source scan reads every file in the checkout, so doing it per binding read
    # a 34 GB fleet once per binding — and the only thing that differed between
    # passes was which regex ran over text already in memory (`OI-30`).
    scanned = (
        scan_repos_for_bindings(repos_root, bindings)
        if repos_root else [[] for _ in bindings]
    )

    indices: list[ProducerIndex] = []
    for binding, scan_hits in zip(bindings, scanned, strict=True):
        by_repo: dict[str, list[ProducerHit]] = defaultdict(list)

        for data in records:
            for hit in _hits_from_repo_json(data, binding):
                by_repo[hit.source_repo].append(hit)

        for hit in scan_hits:
            by_repo[hit.source_repo].append(hit)

        indices.append(ProducerIndex(binding=binding, hits=sorted(
            _strongest_per_repo_and_kind(by_repo),
            key=lambda h: (h.source_repo, h.kind),
        )))

    return indices


def _strongest_per_repo_and_kind(
    by_repo: dict[str, list[ProducerHit]],
) -> list[ProducerHit]:
    """Keep the best-evidenced hit per (repo, kind), discarding weaker duplicates."""
    merged: list[ProducerHit] = []
    for _src, repo_hits in sorted(by_repo.items()):
        kinds: dict[str, ProducerHit] = {}
        for h in repo_hits:
            prev = kinds.get(h.kind)
            if prev is None or _conf_rank(h.confidence) > _conf_rank(prev.confidence):
                kinds[h.kind] = h
        merged.extend(kinds.values())
    return merged


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
    records: list[dict[str, Any]] | None = None,
) -> list[ProducerIndex]:
    """Write the payload-endpoint-producers markdown + jsonl catalogue.

    Returns the indices it built. They are the most expensive thing aggregation
    computes — a full source scan of the fleet — so a second consumer takes them
    from here rather than rebuilding them (`OI-30`).
    """
    root = repos_root
    if root is None:
        candidate = metabase_root.parent / "repos"
        root = candidate if candidate.is_dir() else None

    indices = build_producer_indices(
        metabase_root,
        repos_root=root,
        json_paths=repo_jsons,
        records=records,
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
    return indices
