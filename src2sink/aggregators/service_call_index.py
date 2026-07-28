"""Inbound route index for cross-repo service-call matching."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..graph_common import iter_nodes, normalize_path_template as norm_path, repo_id

# (target_repo, inbound_path, http_method, file:line)
InboundRow = tuple[str, str, str, str]


def build_inbound_index(
    records: list[dict[str, Any]],
) -> dict[str, list[InboundRow]]:
    """Map normalised path template -> inbound http-in nodes."""
    index: dict[str, list[InboundRow]] = defaultdict(list)
    for data in records:
        rid = repo_id(data)
        for node in iter_nodes(data):
            if node.get("family") != "http-in" or node.get("kind") != "source":
                continue
            path = (node.get("detail") or {}).get("path", "")
            if not path or path == "?":
                continue
            key = norm_path(path)
            ref = f"{node.get('file')}:{node.get('line')}"
            method = (node.get("detail") or {}).get("method", "?")
            index[key].append((rid, path, method, ref))
    return index
