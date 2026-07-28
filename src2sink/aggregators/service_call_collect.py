"""Collect cross-repo service-call edges from v2 flow nodes."""

from __future__ import annotations

from typing import Any

from ..graph_common import (
    build_repo_alias_index,
    extract_urls_and_paths,
    iter_nodes,
    match_path_in_inbound_index,
    repo_id,
    resolve_repo_for_host,
)
from .service_call_index import InboundRow, build_inbound_index
from .service_call_models import CallEdge


def _append_path_edge(
    edges: list[CallEdge],
    seen: set[tuple[str, str, str, str]],
    *,
    src: str,
    tgt_repo: str,
    tgt_path: str,
    conf: str,
    evidence: str,
    ref: str,
) -> None:
    """Append a deduplicated path-level call edge unless it is a self-edge."""
    if tgt_repo == src:
        return
    key = (src, tgt_repo, tgt_path, conf)
    if key in seen:
        return
    seen.add(key)
    edges.append(CallEdge(
        source_repo=src,
        target_repo=tgt_repo,
        target_path=tgt_path,
        confidence=conf,
        evidence=evidence,
        refs=[ref],
    ))


def _append_host_edge(
    edges: list[CallEdge],
    seen: set[tuple[str, str, str, str]],
    *,
    src: str,
    tgt_repo: str,
    evidence: str,
    ref: str,
) -> None:
    """Append a deduplicated low-confidence host-level call edge unless self-edge."""
    if tgt_repo == src:
        return
    key = (src, tgt_repo, "*", "low")
    if key in seen:
        return
    seen.add(key)
    edges.append(CallEdge(
        source_repo=src,
        target_repo=tgt_repo,
        target_path="*",
        confidence="low",
        evidence=evidence,
        refs=[ref],
    ))


def _paths_from_http_out_detail(detail: dict[str, Any], raw: str) -> tuple[list[str], list[str]]:
    """Extract (hosts, paths) from an http-out node detail plus its raw text."""
    hosts = [detail["host"]] if detail.get("host") else []
    paths: list[str] = list(detail.get("paths") or [])
    if detail.get("path"):
        paths.insert(0, detail["path"])
    extra_hosts, extra_paths = extract_urls_and_paths(raw)
    hosts.extend(h for h in extra_hosts if h not in hosts)
    paths.extend(p for p in extra_paths if p not in paths)
    return hosts, paths


def _collect_http_out_edges(
    records: list[dict[str, Any]],
    inbound: dict[str, list[InboundRow]],
    alias_to_repo: dict[str, str],
    seen: set[tuple[str, str, str, str]],
    edges: list[CallEdge],
    broken: list[dict[str, str]],
) -> None:
    """Collect edges from http-out sink nodes, recording unmatched refs in broken."""
    for data in records:
        src = repo_id(data)
        for node in iter_nodes(data):
            if node.get("family") != "http-out" or node.get("kind") != "sink":
                continue
            detail = node.get("detail") or {}
            raw = detail.get("raw", "")
            ref = f"{node.get('file')}:{node.get('line')}"
            hosts, paths = _paths_from_http_out_detail(detail, raw)

            matched = False
            for path in paths:
                targets, conf = match_path_in_inbound_index(path, inbound)
                for tgt_repo, tgt_path, _method, _in_ref in targets:
                    _append_path_edge(
                        edges,
                        seen,
                        src=src,
                        tgt_repo=tgt_repo,
                        tgt_path=tgt_path,
                        conf=conf,
                        evidence=f"path literal {path!r} in http-out",
                        ref=ref,
                    )
                    matched = True

            for host in hosts:
                tgt_repo = resolve_repo_for_host(host, alias_to_repo)
                if not tgt_repo:
                    if not matched:
                        broken.append({
                            "source_repo": src,
                            "host": host,
                            "ref": ref,
                            "raw": raw[:120],
                        })
                    continue
                _append_host_edge(
                    edges,
                    seen,
                    src=src,
                    tgt_repo=tgt_repo,
                    evidence=f"host {host!r} in http-out",
                    ref=ref,
                )


def _collect_reference_edges(
    records: list[dict[str, Any]],
    inbound: dict[str, list[InboundRow]],
    alias_to_repo: dict[str, str],
    seen: set[tuple[str, str, str, str]],
    edges: list[CallEdge],
) -> None:
    """Match path/URL literals in any node detail (config, tests, clients)."""
    for data in records:
        src = repo_id(data)
        for node in iter_nodes(data):
            detail = node.get("detail") or {}
            blob = " ".join(
                str(v) for v in detail.values() if isinstance(v, str)
            )
            if not blob:
                continue
            ref = f"{node.get('file')}:{node.get('line')}"
            hosts, paths = extract_urls_and_paths(blob)
            for path in paths:
                targets, conf = match_path_in_inbound_index(path, inbound)
                for tgt_repo, tgt_path, _, _ in targets:
                    _append_path_edge(
                        edges,
                        seen,
                        src=src,
                        tgt_repo=tgt_repo,
                        tgt_path=tgt_path,
                        conf=conf,
                        evidence=f"path/url reference in {node.get('family')}",
                        ref=ref,
                    )
            for host in hosts:
                tgt_repo = resolve_repo_for_host(host, alias_to_repo)
                if tgt_repo:
                    _append_host_edge(
                        edges,
                        seen,
                        src=src,
                        tgt_repo=tgt_repo,
                        evidence=f"host reference in {node.get('family')}",
                        ref=ref,
                    )


def collect_service_edges(
    records: list[dict[str, Any]],
) -> tuple[list[CallEdge], list[dict[str, str]]]:
    """Collect all cross-repo call edges; returns (edges, unmatched outbound refs)."""
    inbound = build_inbound_index(records)
    alias_to_repo = build_repo_alias_index(records)
    edges: list[CallEdge] = []
    broken: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    _collect_http_out_edges(records, inbound, alias_to_repo, seen, edges, broken)
    _collect_reference_edges(records, inbound, alias_to_repo, seen, edges)
    return edges, broken


def merge_openapi_edges(
    edges: list[CallEdge],
    records: list[dict[str, Any]],
    repos_root: Any,
) -> int:
    """Append OpenAPI-matched edges; returns count added."""
    from pathlib import Path

    root = Path(repos_root)
    if not root.is_dir():
        return 0
    from .openapi_edges import (
        build_openapi_inbound_index,
        discover_openapi_specs,
        match_http_out_to_openapi,
    )

    specs = discover_openapi_specs(root)
    if not specs:
        return 0
    inbound = build_openapi_inbound_index(specs)
    alias_to_repo = build_repo_alias_index(records)
    rows = match_http_out_to_openapi(records, inbound, alias_to_repo)
    seen = {(e.source_repo, e.target_repo, e.target_path, e.confidence) for e in edges}
    added = 0
    for row in rows:
        key = (row["source_repo"], row["target_repo"], row["target_path"], "openapi")
        if key in seen:
            continue
        seen.add(key)
        edges.append(CallEdge(
            source_repo=row["source_repo"],
            target_repo=row["target_repo"],
            target_path=row["target_path"],
            confidence="openapi",
            evidence=row.get("evidence", "OpenAPI path match"),
            refs=row.get("refs", []),
        ))
        added += 1
    return added
