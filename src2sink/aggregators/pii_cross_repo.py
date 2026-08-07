"""Cross-repo PII flow links (queue hops + HTTP edges) for showcase fields."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .service_calls import collect_service_edges
from ..graph_common import iter_nodes, load_v2_repo_records, repo_id
from ..models.pii_lifecycle import PiiTouchpoint
from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE, for_markdown

MAX_FLOW_ROWS = 80


def _repos_with_field(
    touches: list[PiiTouchpoint],
    field_key: str,
) -> set[str]:
    """Return repos with any touchpoint for the given field."""
    return {t.repo for t in touches if t.field_key == field_key}


# Cross-repo hops: repos must have material handling of the field (not merely
# a pii-field declaration or a low-confidence "nearby http-out/sql" hint).
_MATERIAL_FAMILIES = frozenset({"pii-field", "pii-log", "pii-storage", "queue-pub"})
_MATERIAL_STAGES = frozenset({"store", "log", "collect", "delete", "encrypt"})
_STRONG_FAMILIES = frozenset({"pii-log", "pii-storage", "queue-pub", "queue-sub"})
# Generic routes shared by many services — not indicative of PII data flow.
_HTTP_NOISE_PATHS = frozenset({
    "/metrics",
    "/health",
    "/healthz",
    "/ready",
    "/live",
    "/actuator",
    "/swagger",
    "/openapi",
    "/api/health",
    "/status",
})


def _material_repos_with_field(
    touches: list[PiiTouchpoint],
    field_key: str,
) -> set[str]:
    """Return repos that materially handle the field (not mere declarations/hints)."""
    out: set[str] = set()
    for t in touches:
        if t.field_key != field_key:
            continue
        if t.family in _MATERIAL_FAMILIES:
            out.add(t.repo)
        elif t.stage in _MATERIAL_STAGES:
            out.add(t.repo)
        elif t.stage == "transmit" and t.confidence != "low":
            out.add(t.repo)
    return out


def _strong_material_repos_with_field(
    touches: list[PiiTouchpoint],
    field_key: str,
) -> set[str]:
    """Repos with log/store/queue touchpoints — used for HTTP cross-repo hops."""
    out: set[str] = set()
    for t in touches:
        if t.field_key != field_key:
            continue
        if t.family in _STRONG_FAMILIES:
            out.add(t.repo)
        elif t.stage in ("store", "log", "delete", "encrypt"):
            out.add(t.repo)
    return out


def _is_noise_http_path(path: str) -> bool:
    """Return True for generic infra paths (health/metrics/etc.) that aren't PII flow."""
    norm = path.split("?")[0].rstrip("/") or "/"
    if norm in _HTTP_NOISE_PATHS:
        return True
    for prefix in _HTTP_NOISE_PATHS:
        if norm.startswith(prefix + "/"):
            return True
    return False


def _queue_topics_by_repo(records: list[dict[str, Any]]) -> dict[str, dict[str, set[str]]]:
    """repo -> {produce: topics, consume: topics}."""
    out: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"produce": set(), "consume": set()},
    )
    for data in records:
        rid = repo_id(data)
        for node in iter_nodes(data):
            family = node.get("family", "")
            topic = (node.get("detail") or {}).get("topic", "")
            if not topic or topic == "?":
                continue
            if family == "queue-pub":
                out[rid]["produce"].add(topic)
            elif family == "queue-sub":
                out[rid]["consume"].add(topic)
    return out


def _build_queue_flows(
    records: list[dict[str, Any]],
    *,
    field_key: str,
    material_repos: set[str],
) -> list[dict[str, Any]]:
    """Link repos that share a topic and both materially touch the field."""
    flows: list[dict[str, Any]] = []
    queue_map = _queue_topics_by_repo(records)
    topics: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"produce": set(), "consume": set()},
    )
    for rid, dirs in queue_map.items():
        if rid not in material_repos:
            continue
        for topic in dirs["produce"]:
            topics[topic]["produce"].add(rid)
        for topic in dirs["consume"]:
            topics[topic]["consume"].add(rid)

    for topic, dirs in sorted(topics.items()):
        producers = sorted(dirs["produce"] & material_repos)
        consumers = sorted(dirs["consume"] & material_repos)
        if not producers or not consumers:
            continue
        for p in producers:
            for c in consumers:
                if p == c:
                    continue
                flows.append({
                    "field_key": field_key,
                    "kind": "queue",
                    "source_repo": p,
                    "target_repo": c,
                    "via": topic,
                    "confidence": "medium",
                    "evidence": f"Both repos touch `{field_key}`; topic `{topic}`",
                })
    return flows


def _build_http_flows(
    records: list[dict[str, Any]],
    *,
    field_key: str,
    strong_repos: set[str],
) -> list[dict[str, Any]]:
    """Link high/openapi service-call edges where both repos strongly touch the field."""
    flows: list[dict[str, Any]] = []
    edges, _ = collect_service_edges(records)
    for edge in edges:
        if edge.confidence not in ("high", "openapi"):
            continue
        if _is_noise_http_path(edge.target_path):
            continue
        if edge.source_repo not in strong_repos or edge.target_repo not in strong_repos:
            continue
        flows.append({
            "field_key": field_key,
            "kind": "http",
            "source_repo": edge.source_repo,
            "target_repo": edge.target_repo,
            "via": edge.target_path,
            "confidence": edge.confidence,
            "evidence": edge.evidence[:120],
        })
    return flows


def build_cross_repo_flows(
    records: list[dict[str, Any]],
    touches: list[PiiTouchpoint],
    *,
    field_key: str = "phone",
) -> list[dict[str, Any]]:
    """Build queue + HTTP cross-repo flows for a field between repos that touch it."""
    material_repos = _material_repos_with_field(touches, field_key)
    if not material_repos:
        return []
    strong_repos = _strong_material_repos_with_field(touches, field_key)
    return (
        _build_queue_flows(records, field_key=field_key, material_repos=material_repos)
        + _build_http_flows(records, field_key=field_key, strong_repos=strong_repos)
    )


def write_pii_cross_repo_graph(
    metabase_root: Path,
    touches: list[PiiTouchpoint],
    repo_jsons: list[Path] | None = None,
    *,
    field_key: str = "phone",
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Write the cross-repo field-flow markdown + jsonl and return the flows.

    ``records`` lets a caller running this per PII field parse the fleet once
    rather than once per field. Three fields meant three full parses of a 2.2 GB
    metabase for identical input (`OI-41`).
    """
    if records is None:
        records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    phone_repos = _material_repos_with_field(touches, field_key)
    strong_repos = _strong_material_repos_with_field(touches, field_key)
    flows = build_cross_repo_flows(records, touches, field_key=field_key)

    graphs_dir = metabase_root / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = graphs_dir / f"pii-{field_key}-cross-repo.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in flows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    queue_rows = [f for f in flows if f["kind"] == "queue"]
    http_rows = [f for f in flows if f["kind"] == "http"]

    md: list[str] = [
        f"# Cross-repo `{for_markdown(field_key, max_len=80)}` flows\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Messaging hops require a material field touchpoint; HTTP hops require "
        "**strong** touchpoints (log/store/queue) on both sides plus a "
        "non-generic path (`/metrics`, `/health`, etc. excluded). "
        "**high**/**openapi** service-call edges only._\n",
        f"\n**Repos with material `{field_key}` touchpoints:** "
        f"{len(phone_repos)} "
        f"(**strong** for HTTP: {len(strong_repos)}; "
        f"of {len(_repos_with_field(touches, field_key))} with any touchpoint).\n",
        f"\n**Cross-repo hops:** {len(flows)} "
        f"({len(queue_rows)} queue, {len(http_rows)} HTTP).\n",
    ]

    if queue_rows:
        md.append("\n## Messaging hops\n")
        md.append(
            md_table(
                ["Producer", "Topic", "Consumer", "Confidence"],
                [
                    [
                        r["source_repo"],
                        r["via"],
                        r["target_repo"],
                        r["confidence"],
                    ]
                    for r in queue_rows[:MAX_FLOW_ROWS]
                ],
            ),
        )
    if http_rows:
        md.append("\n## HTTP hops (both repos touch field)\n")
        md.append(
            md_table(
                ["Caller", "Target path", "Callee", "Confidence"],
                [
                    [
                        r["source_repo"],
                        r["via"],
                        r["target_repo"],
                        r["confidence"],
                    ]
                    for r in http_rows[:MAX_FLOW_ROWS]
                ],
            ),
        )

    if not flows:
        md.append(
            "\n_No cross-repo hops found. Extend queue/http extraction or "
            "re-run after more repos expose `queue-pub`/`queue-sub` nodes._\n",
        )

    tail = max(0, len(flows) - MAX_FLOW_ROWS)
    if tail:
        md.append(f"\n_{tail} more rows in `pii-{field_key}-cross-repo.jsonl`._\n")

    (graphs_dir / f"pii-{field_key}-cross-repo.md").write_text(
        "\n".join(md),
        encoding="utf-8",
    )
    return flows
