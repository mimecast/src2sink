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
    conf: str = "low",
) -> None:
    """Append a deduplicated service-level (``*`` path) call edge unless self-edge.

    Defaults to ``low`` for a hostname hint. A caller with stronger evidence for
    the hop but no resolved route — an api-client binding, which is explicit
    configuration — passes its own confidence.
    """
    if tgt_repo == src:
        return
    key = (src, tgt_repo, "*", conf)
    if key in seen:
        return
    seen.add(key)
    edges.append(CallEdge(
        source_repo=src,
        target_repo=tgt_repo,
        target_path="*",
        confidence=conf,
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


def _declared_target_repo(
    detail: dict[str, Any], alias_to_repo: dict[str, str]
) -> tuple[str, str] | None:
    """Return (target repo, confidence) a node already knows, or None.

    ``target_repo`` is stamped on an http-out node either by an api-client
    ``class_patterns`` match (a declaration — ``high``) or by a service-alias hit
    in the call context (a strong hint — ``medium``); the extractor records which
    in ``target_repo_confidence``. ``config_key`` is an externalised property name
    (``${some-service.base-url}``) whose dotted segments are matched against the
    alias index, which is likewise a hint rather than a declaration.
    """
    declared = detail.get("target_repo")
    if isinstance(declared, str) and declared:
        conf = detail.get("target_repo_confidence")
        return declared, conf if conf in ("high", "medium", "low") else "medium"
    config_key = detail.get("config_key")
    if isinstance(config_key, str) and config_key:
        for segment in config_key.lower().split("."):
            tgt = alias_to_repo.get(segment)
            if tgt:
                return tgt, "medium"
    return None


def _collect_http_out_edges(
    records: list[dict[str, Any]],
    inbound: dict[str, list[InboundRow]],
    alias_to_repo: dict[str, str],
    seen: set[tuple[str, str, str, str]],
    edges: list[CallEdge],
    broken: list[dict[str, str]],
) -> None:
    """Collect edges from http-out sink nodes, recording unmatched refs in broken."""
    memo: dict[str, tuple[list[Any], str]] = {}
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
            edged_repos: set[str] = set()
            for path in paths:
                targets, conf = match_path_in_inbound_index(path, inbound, memo=memo)
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
                    edged_repos.add(tgt_repo)

            # A call site whose target the extractor already resolved does not
            # need the inbound index at all — this is the hop that carries a
            # compiled-in client library, where the consumer's source names no
            # host or path for the index to match. Skipped when the index already
            # matched that same repo, so one hop is not reported twice at two
            # confidences.
            resolved = _declared_target_repo(detail, alias_to_repo)
            if resolved and resolved[0] in edged_repos:
                resolved = None
            if resolved:
                declared, declared_conf = resolved
                why = detail.get("target_repo_evidence") or "declared target"
                if paths:
                    for path in paths:
                        _append_path_edge(
                            edges,
                            seen,
                            src=src,
                            tgt_repo=declared,
                            tgt_path=path,
                            conf=declared_conf,
                            evidence=f"{why}; path {path!r} at call site",
                            ref=ref,
                        )
                else:
                    _append_host_edge(
                        edges,
                        seen,
                        src=src,
                        tgt_repo=declared,
                        evidence=str(why),
                        ref=ref,
                        conf=declared_conf,
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
                            "reason": "host did not resolve to a known repo",
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
            if not matched and not hosts:
                # Neither a host nor a route nor a declared target: an outbound
                # call site that produced no edge at all. Recorded so lost
                # coverage is visible instead of silently vanishing (§3.4).
                broken.append({
                    "source_repo": src,
                    "host": "",
                    "ref": ref,
                    "raw": raw[:120],
                    "reason": "no host, path, or declared target resolved",
                })


def _collect_api_client_edges(
    records: list[dict[str, Any]],
    seen: set[tuple[str, str, str, str]],
    edges: list[CallEdge],
) -> None:
    """Turn api-client-consumer propagator nodes into cross-repo call edges.

    These nodes already carry the binding's ``target_repo`` and declared
    ``paths``, but nothing downstream of the payload-producers report read them —
    so a repo that calls a service purely through its published client library
    could never appear in the service-call graph at all (report §3.2).

    An import proves the hop, not which route it hits, so a binding declaring
    several paths yields one service-level (``*``) edge; a single declared path is
    specific enough to emit as a route.
    """
    for data in records:
        src = repo_id(data)
        for node in iter_nodes(data):
            if node.get("family") != "api-client-consumer":
                continue
            detail = node.get("detail") or {}
            tgt_repo = detail.get("target_repo")
            if not tgt_repo:
                continue
            ref = f"{node.get('file')}:{node.get('line')}"
            paths = [p for p in (detail.get("paths") or []) if isinstance(p, str)]
            client = detail.get("client") or "api-client"
            evidence = f"api-client binding {client}"
            if len(paths) == 1:
                _append_path_edge(
                    edges, seen,
                    src=src, tgt_repo=tgt_repo, tgt_path=paths[0],
                    conf="high",
                    evidence=f"{evidence} (declared path)",
                    ref=ref,
                )
                continue
            if paths:
                evidence += " — declared paths: " + ", ".join(paths[:6])
            _append_host_edge(
                edges, seen,
                src=src, tgt_repo=tgt_repo,
                evidence=evidence,
                ref=ref,
                conf="high",
            )


def _collect_reference_edges(
    records: list[dict[str, Any]],
    inbound: dict[str, list[InboundRow]],
    alias_to_repo: dict[str, str],
    seen: set[tuple[str, str, str, str]],
    edges: list[CallEdge],
) -> None:
    """Match path/URL literals in any node detail (config, tests, clients)."""
    memo: dict[str, tuple[list[Any], str]] = {}
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
                targets, conf = match_path_in_inbound_index(path, inbound, memo=memo)
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
    _collect_api_client_edges(records, seen, edges)
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
