"""Render service-call graph markdown and JSONL."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .fleet_pass import records_or_load
from ..known_api_clients import get_bindings
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


def _render_binding_coverage(edges: list[CallEdge]) -> list[str]:
    """Return markdown lines reconciling configured api-client bindings against edges.

    A binding is a declaration that some repos call a service through a published
    client. If a configured binding produced no edge at all, detection for that
    service is broken (bad import prefix, wrong artifact id, no consumers
    scanned) — and without this table that shows up as an empty graph rather than
    a reported gap (report §3.4 / Fix 6).
    """
    bindings = get_bindings()
    if not bindings:
        return [
            "\n## API-client binding coverage\n",
            "_No api-client bindings configured — cross-repo client-library "
            "callers cannot be detected. Pass `--api-clients` to enable._\n",
        ]

    # Two distinct signals: callers found by *any* route (a URL literal, a config
    # host) versus callers found *because of the binding*. A service can look
    # covered on the first count while client-library detection is entirely
    # broken — which is exactly the failure this table exists to surface.
    all_callers: dict[str, set[str]] = {}
    binding_callers: dict[str, set[str]] = {}
    for e in edges:
        all_callers.setdefault(e.target_repo, set()).add(e.source_repo)
        if "api-client" in e.evidence:
            binding_callers.setdefault(e.target_repo, set()).add(e.source_repo)

    def _status(target: str) -> str:
        """Report whether a configured binding actually produced any callers."""
        if binding_callers.get(target):
            return "OK"
        if all_callers.get(target):
            return "⚠ no binding-derived callers"
        return "⚠ no callers at all"

    rows = [
        [
            b.target_repo,
            b.maven_artifact,
            str(len(all_callers.get(b.target_repo, ()))),
            str(len(binding_callers.get(b.target_repo, ()))),
            _status(b.target_repo),
        ]
        for b in sorted(bindings, key=lambda b: b.target_repo)
    ]
    uncovered = sum(1 for r in rows if r[4] != "OK")
    out = [
        "\n## API-client binding coverage\n",
        f"_{len(rows)} binding(s) configured; **{uncovered}** produced no "
        "binding-derived caller. A binding declares that some repos call a "
        "service through its published client; if it yields no callers, "
        "detection for that service is not working — check its `import_prefix`, "
        "`maven_artifact` and `class_patterns`. Callers found by other means "
        "(URL literal, config host) are counted separately, because a service "
        "can look covered while the client-library path is entirely broken._\n",
        md_table(
            [
                "Target repo",
                "Client artifact",
                "Caller repos (any route)",
                "Caller repos (via binding)",
                "Status",
            ],
            rows,
        ),
    ]
    return out


def _write_unmatched(out_dir: Path, broken: list[dict[str, str]]) -> None:
    """Persist unmatched outbound call sites so lost coverage is machine-readable.

    ``collect_service_edges`` has always computed this list but only ever sampled
    it into the markdown, so there was no way to diff coverage between runs or
    drive a CI gate off it.
    """
    with (out_dir / "service-call-unmatched.jsonl").open("w", encoding="utf-8") as fh:
        for b in broken:
            fh.write(json.dumps({
                "source_repo": b.get("source_repo", ""),
                "host": b.get("host", ""),
                "ref": b.get("ref", ""),
                "reason": b.get("reason", "unmatched outbound call site"),
                "raw": b.get("raw", ""),
            }, ensure_ascii=False) + "\n")


def write_service_call_graph(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    repos_root: Path | None = None,
    records: list[dict[str, Any]] | None = None,
) -> tuple[list[CallEdge], list[dict[str, Any]]]:
    """Write the service-call graph markdown report and edges JSONL to graphs/.

    Returns the edges and unmatched call sites it computed. Both are fleet-wide
    and target-independent, and three separate consumers were each recomputing
    them — the derivation `OI-14` identified as dominating cost (`OI-41`).
    """
    records = records_or_load(records, metabase_root, repo_jsons)
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
        "templates, plus hops declared by an api-client binding. Confidence: "
        "**high** = same normalised path, or a configured api-client binding; "
        "**medium** = prefix/template overlap; **low** = hostname hints only; "
        "**openapi** = path matched to a discovered OpenAPI/Swagger spec. A "
        "`target_path` of `*` is a service-level hop whose route was not "
        "resolved._\n",
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
    md.extend(_render_binding_coverage(edges))

    md.append("\n## Broken / unmatched outbound refs\n")
    md.append(
        f"_{len(broken)} outbound call site(s) produced no edge: the host did "
        "not resolve to a known repo, or nothing at the call site named a host, "
        "route, or target service. Full list in "
        "`service-call-unmatched.jsonl`; sampled below._\n\n",
    )
    if broken:
        md.append(md_table(
            ["Source repo", "Host / hint", "File:line", "Reason", "Raw (trimmed)"],
            [
                [
                    b["source_repo"],
                    b.get("host") or "—",
                    b["ref"],
                    b.get("reason", ""),
                    b["raw"],
                ]
                for b in broken[:200]
            ],
        ))
    else:
        md.append("_No unmatched outbound call sites._\n")

    (out_dir / "service-call-graph.md").write_text("\n".join(md), encoding="utf-8")
    _write_unmatched(out_dir, broken)

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
    return edges, broken
