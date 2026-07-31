"""Match http-out nodes to OpenAPI-discovered inbound paths."""

from __future__ import annotations

from typing import Any

from ..graph_common import (
    build_repo_alias_index,
    extract_urls_and_paths,
    iter_nodes,
    match_path_in_inbound_index,
    normalize_path_template as norm_path,
    repo_id,
)

from .openapi_models import OpenApiSpec


def build_openapi_inbound_index(
    specs: list[OpenApiSpec],
) -> dict[str, list[tuple[str, str, str]]]:
    """normalized_path -> [(repo, original_path, spec_path)]."""
    index: dict[str, list[tuple[str, str, str]]] = {}
    for spec in specs:
        for path in spec.paths:
            key = norm_path(path)
            index.setdefault(key, []).append(
                (spec.target_repo, path, spec.spec_path),
            )
    return index


# Node families whose paths can be matched against an OpenAPI spec. Restricting
# this to `http-out` meant a caller that reaches a service through its published
# client library — whose declared paths live on the api-client-consumer node —
# could never produce an OpenAPI edge (report §3.2).
_OUTBOUND_FAMILIES = frozenset({"http-out", "api-client-consumer"})


def _paths_from_http_out(detail: dict[str, Any], raw: str) -> list[str]:
    """Collect candidate request paths from an outbound node detail and raw text."""
    paths: list[str] = list(detail.get("paths") or [])
    if detail.get("path"):
        paths.insert(0, detail["path"])
    paths.extend(p for p in (detail.get("client_paths") or []) if p not in paths)
    _, extra_paths = extract_urls_and_paths(raw)
    paths.extend(p for p in extra_paths if p not in paths)
    return [p for p in paths if isinstance(p, str)]


def match_http_out_to_openapi(
    records: list[dict[str, Any]],
    inbound: dict[str, list[tuple[str, str, str]]],
    alias_to_repo: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return edges from outbound call sites that match an OpenAPI path."""
    _ = alias_to_repo or build_repo_alias_index(records)
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    memo: dict[str, tuple[list[Any], str]] = {}

    for data in records:
        src = repo_id(data)
        for node in iter_nodes(data):
            if node.get("family") not in _OUTBOUND_FAMILIES:
                continue
            detail = node.get("detail") or {}
            raw = detail.get("raw", "")
            ref = f"{node.get('file')}:{node.get('line')}"
            # When the node names its target repo (an api-client binding), only
            # that repo's spec can legitimately match — otherwise a shared route
            # template like /queries would fan out to every service declaring it.
            declared = detail.get("target_repo")

            for path in _paths_from_http_out(detail, raw):
                targets, _conf = match_path_in_inbound_index(path, inbound, memo=memo)
                for tgt_repo, tgt_path, spec_path in targets:
                    if tgt_repo == src:
                        continue
                    if declared and tgt_repo != declared:
                        continue
                    key = (src, tgt_repo, norm_path(tgt_path))
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append({
                        "source_repo": src,
                        "target_repo": tgt_repo,
                        "target_path": tgt_path,
                        "confidence": "openapi",
                        "evidence": f"{node.get('family')} path matches OpenAPI in {spec_path}",
                        "refs": [ref],
                        "spec_path": spec_path,
                    })

    return edges
