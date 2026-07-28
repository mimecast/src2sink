"""Render PII lifecycle graph markdown and JSONL."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..graph_common import load_v2_repo_records
from ..models.pii_lifecycle import LIFECYCLE_STAGES, FieldLifecycleAggregate, PiiTouchpoint
from ..sanitize import UNTRUSTED_CONTENT_NOTICE
from ..renderers.markdown import md_table

from .pii_touchpoint_collect import collect_pii_touchpoints

MAX_MD_ROWS = 400


def aggregate_by_field(touches: list[PiiTouchpoint]) -> dict[str, FieldLifecycleAggregate]:
    """Group touchpoints into per-(field, classification) lifecycle aggregates."""
    buckets: dict[str, FieldLifecycleAggregate] = {}
    for touch in touches:
        bucket_key = f"{touch.field_key}|{touch.pii_classification}"
        if bucket_key not in buckets:
            buckets[bucket_key] = FieldLifecycleAggregate(
                field_key=touch.field_key,
                pii_classification=touch.pii_classification,
            )
        buckets[bucket_key].add(touch)
    return buckets


def _append_phone_example(
    md: list[str],
    aggregates: dict[str, FieldLifecycleAggregate],
) -> None:
    """Append a worked `phone` lifecycle example section to the markdown lines."""
    phone_agg = [a for a in aggregates.values() if a.field_key == "phone"]
    if not phone_agg:
        return
    md.append("\n## Worked example: `phone` (direct PII)\n")
    for agg in sorted(phone_agg, key=lambda a: -a.touchpoints)[:3]:
        md.append(f"\n### Classification: `{agg.pii_classification}`\n")
        md.append(
            md_table(
                ["Stage", "Repos (count)"],
                [
                    [s, str(len(agg.repos_by_stage.get(s, set())))]
                    for s in LIFECYCLE_STAGES
                ],
            ),
        )
        if agg.sample_refs:
            md.append("\nSample locations:\n")
            for ref in agg.sample_refs:
                md.append(f"- {ref}\n")


def write_pii_lifecycle_graph(
    metabase_root: Path,
    repo_jsons: list[Path] | None = None,
) -> list[PiiTouchpoint]:
    """Write the PII lifecycle graph (JSONL + markdown) to graphs/ and return touches."""
    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    touches = collect_pii_touchpoints(records)
    aggregates = aggregate_by_field(touches)

    graphs_dir = metabase_root / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = graphs_dir / "pii-lifecycle.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for touch in touches:
            fh.write(json.dumps(touch.to_dict(), ensure_ascii=False) + "\n")

    stage_counts = Counter(t.stage for t in touches)
    class_counts = Counter(t.pii_classification for t in touches)

    md: list[str] = [
        "# PII lifecycle (fleet)\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Static analysis from v2 `pii-field`, `pii-storage`, `pii-log`, and "
        "file-local proximity to SQL/HTTP/crypto. Not a runtime data-flow proof._\n",
        "\n## Stage totals\n",
        md_table(
            ["Stage", "Touchpoints"],
            [[s, str(stage_counts.get(s, 0))] for s in LIFECYCLE_STAGES],
        ),
        "\n## By GDPR classification\n",
        md_table(
            ["Classification", "Touchpoints"],
            [[k, str(v)] for k, v in class_counts.most_common()],
        ),
    ]

    _append_phone_example(md, aggregates)

    md.append("\n## Field catalogue (top by touchpoints)\n")
    ranked = sorted(aggregates.values(), key=lambda a: -a.touchpoints)
    md.append(
        md_table(
            ["Field", "Classification", "Touchpoints", "Collect repos", "Store repos", "Log repos"],
            [
                [
                    a.field_key,
                    a.pii_classification,
                    str(a.touchpoints),
                    str(len(a.repos_by_stage.get("collect", set()))),
                    str(len(a.repos_by_stage.get("store", set()))),
                    str(len(a.repos_by_stage.get("log", set()))),
                ]
                for a in ranked[:MAX_MD_ROWS]
            ],
        ),
    )
    tail = max(0, len(ranked) - MAX_MD_ROWS)
    if tail:
        md.append(f"\n_{tail} more fields in `pii-lifecycle.jsonl`._\n")

    (graphs_dir / "pii-lifecycle.md").write_text("\n".join(md), encoding="utf-8")
    return touches
