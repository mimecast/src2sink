"""ROPA Article 30 view projected from PII lifecycle touchpoints (Phase 3)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .pii_lifecycle import collect_pii_touchpoints
from ..graph_common import load_v2_repo_records
from ..models.ropa import ROPA_CATEGORY_BY_CLASSIFICATION, RopaProcessingActivity
from ..renderers.markdown import md_table

MAX_MD_ROWS = 300


def _group_touches(touches: list[Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Group PII touchpoints by (ROPA category, classification, repo) with stage flags."""
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for touch in touches:
        category = ROPA_CATEGORY_BY_CLASSIFICATION.get(
            touch.pii_classification, "Unclassified personal data"
        )
        key = (category, touch.pii_classification, touch.repo)
        bucket = grouped.setdefault(key, {
            "field_keys": set(), "stages": set(),
            "store": False, "log": False, "transmit": False,
        })
        bucket["field_keys"].add(touch.field_key)
        bucket["stages"].add(touch.stage)
        if touch.stage in ("store", "log", "transmit"):
            bucket[touch.stage] = True
    return grouped


def _ropa_purposes(bucket: dict[str, Any]) -> list[str]:
    """Infer ROPA processing purposes from the touchpoint bucket's stages."""
    purposes: list[str] = []
    if "collect" in bucket["stages"] or "process" in bucket["stages"]:
        purposes.append("Service delivery / API processing")
    if bucket["store"]:
        purposes.append("Persistence")
    if bucket["log"]:
        purposes.append("Operations and diagnostics (logging)")
    if bucket["transmit"]:
        purposes.append("Inter-service transmission")
    return purposes or ["Code references (stage unclear)"]


def _ropa_security(bucket: dict[str, Any]) -> list[str]:
    """Infer ROPA security-measure notes from the touchpoint bucket's stages."""
    security: list[str] = []
    if "encrypt" in bucket["stages"]:
        security.append("Crypto operations near field (static)")
    if bucket["log"]:
        security.append("Review logging sinks for field")
    else:
        security.append("Logging exposure not detected in static scan")
    return security


def build_ropa_activities(
    touches: list[Any],
) -> list[RopaProcessingActivity]:
    """Group touchpoints into repo × ROPA category processing activities."""
    grouped = _group_touches(touches)

    activities: list[RopaProcessingActivity] = []
    for (category, pii_class, repo), bucket in sorted(grouped.items()):
        purposes = _ropa_purposes(bucket)
        security = _ropa_security(bucket)
        retention = (
            "See repo config / data-store graph"
            if bucket["store"]
            else "Not inferred from static scan"
        )

        activities.append(
            RopaProcessingActivity(
                category=category,
                pii_classification=pii_class,
                repo=repo,
                purposes=purposes,
                data_subjects="Customers and end users (inferred)",
                recipients="Internal services" if bucket["transmit"] else "Not inferred",
                retention_hint=retention,
                security_measures=security,
                field_keys=sorted(bucket["field_keys"])[:20],
            ),
        )
    return activities


def write_ropa_view(
    metabase_root: Path,
    repo_jsons: list[Path] | None = None,
    *,
    touches: list[Any] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> list[RopaProcessingActivity]:
    """Write the ROPA Article 30 markdown + jsonl view and return the activities."""
    if records is None:
        records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    if touches is None:
        touches = collect_pii_touchpoints(records)
    activities = build_ropa_activities(touches)

    ropa_dir = metabase_root / "ropa"
    ropa_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = ropa_dir / "processing-activities.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for act in activities:
            fh.write(json.dumps(act.to_dict(), ensure_ascii=False) + "\n")

    by_category: dict[str, list[RopaProcessingActivity]] = defaultdict(list)
    for act in activities:
        by_category[act.category].append(act)

    md: list[str] = [
        "# Categories of personal data (ROPA Article 30 view)\n",
        "_Projected from v2 PII lifecycle touchpoints. For legal ROPA, validate "
        "purposes, lawful basis, and retention with product owners._\n",
        f"\n_Total processing activities (repo × category): **{len(activities)}**._\n",
    ]

    phone_acts = [
        a for a in activities
        if "phone" in a.field_keys
    ]
    if phone_acts:
        md.append("\n## Worked example: phone number processing\n")
        md.append(
            md_table(
                ["Repo", "Category", "Purposes", "Store", "Log", "Transmit"],
                [
                    [
                        a.repo,
                        a.category,
                        "; ".join(a.purposes[:2]),
                        "yes" if "Persistence" in a.purposes else "—",
                        "yes" if "logging" in " ".join(a.purposes).lower() else "—",
                        "yes" if a.recipients != "Not inferred" else "—",
                    ]
                    for a in sorted(phone_acts, key=lambda x: x.repo)[:40]
                ],
            ),
        )

    md.append("\n## By category (summary)\n")
    md.append(
        md_table(
            ["Category", "Activities", "Distinct repos"],
            [
                [
                    cat,
                    str(len(acts)),
                    str(len({a.repo for a in acts})),
                ]
                for cat, acts in sorted(
                    by_category.items(),
                    key=lambda x: -len(x[1]),
                )[:30]
            ],
        ),
    )

    md.append("\n## All activities (sample)\n")
    md.append(
        md_table(
            ["Category", "Repo", "Fields", "Purposes"],
            [
                [
                    a.category,
                    a.repo,
                    ", ".join(a.field_keys[:5]),
                    "; ".join(a.purposes[:2]),
                ]
                for a in activities[:MAX_MD_ROWS]
            ],
        ),
    )
    tail = max(0, len(activities) - MAX_MD_ROWS)
    if tail:
        md.append(f"\n_{tail} more rows in `processing-activities.jsonl`._\n")

    (ropa_dir / "categories-of-personal-data.md").write_text(
        "\n".join(md),
        encoding="utf-8",
    )
    return activities
