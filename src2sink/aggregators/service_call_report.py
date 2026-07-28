"""Render service-call graph markdown and JSONL."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ..graph_common import load_v2_repo_records
from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE, for_mermaid_label

from .service_call_collect import collect_service_edges, merge_openapi_edges
from .service_call_models import CallEdge


def _render_mermaid_section(edges: list[CallEdge]) -> list[str]:
    """Return markdown lines for a Mermaid flowchart of the top cross-repo pairs."""
    top = Counter((e.source_repo, e.target_repo) for e in edges).most_common(40)
    if not top:
        return []
    lines = [
        "\n## Mermaid (top cross-repo pairs)\n\n```mermaid\nflowchart LR\n",
    ]
    for (src, dst), _ in top:
        sid = re.sub(r"[^A-Za-z0-9_]", "_", src)[:40]
        did = re.sub(r"[^A-Za-z0-9_]", "_", dst)[:40]
        # ids are slugified above; labels carry the raw repo id — neutralise them.
        lines.append(f'  {sid}["{for_mermaid_label(src)}"] --> {did}["{for_mermaid_label(dst)}"]\n')
    lines.append("```\n")
    return lines


def write_service_call_graph(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    repos_root: Path | None = None,
) -> None:
    """Write the service-call graph markdown report and edges JSONL to graphs/."""
    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    edges, broken = collect_service_edges(records)
    if repos_root:
        merge_openapi_edges(edges, records, repos_root)

    out_dir = metabase_root / "graphs"
    out_dir.mkdir(parents=True, exist_ok=True)

    by_conf = Counter(e.confidence for e in edges)
    md: list[str] = [
        "# Service-call graph (v2)\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Cross-repo edges from `http-out` call sites matched to `http-in` path "
        "templates. Confidence: **high** = same normalised path; **medium** = "
        "prefix/template overlap; **low** = hostname hints only; **openapi** = "
        "path matched to a discovered OpenAPI/Swagger spec._\n",
        "\n> Regex-derived; validate against OpenAPI / Helm before acting on "
        "low-confidence edges.\n",
        "\n## Summary\n",
        md_table(
            ["Confidence", "Edge count"],
            [[k, str(v)] for k, v in by_conf.most_common()],
        ),
    ]

    detail = sorted(
        edges,
        key=lambda e: (e.confidence, e.target_repo, e.source_repo),
    )
    md.append("\n## Matched edges (sampled)\n")
    md.append(md_table(
        ["Source", "Target", "Path", "Confidence", "Evidence"],
        [
            [
                e.source_repo,
                e.target_repo,
                e.target_path,
                e.confidence,
                e.evidence[:100],
            ]
            for e in detail[:500]
        ],
    ))
    if len(detail) > 500:
        md.append(f"\n_{len(detail) - 500} additional edges omitted._\n")

    md.extend(_render_mermaid_section(edges))

    md.append("\n## Broken / unmatched outbound refs\n")
    md.append(
        "_http-out sites with a resolvable host or path that did not match any "
        "indexed inbound route (sampled)._\n\n",
    )
    if broken:
        md.append(md_table(
            ["Source repo", "Host / hint", "File:line", "Raw (trimmed)"],
            [
                [b["source_repo"], b.get("host", "?"), b["ref"], b["raw"]]
                for b in broken[:200]
            ],
        ))
    else:
        md.append("_No unmatched hosts in sample._\n")

    (out_dir / "service-call-graph.md").write_text("\n".join(md), encoding="utf-8")

    jsonl = out_dir / "service-call-edges.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for e in edges:
            fh.write(json.dumps({
                "source_repo": e.source_repo,
                "target_repo": e.target_repo,
                "target_path": e.target_path,
                "confidence": e.confidence,
                "evidence": e.evidence,
                "refs": e.refs,
            }, ensure_ascii=False) + "\n")
