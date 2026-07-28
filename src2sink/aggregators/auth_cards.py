"""Per-repo auth model cards from v2 auth + http-in nodes (Phase 3)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..graph_common import iter_nodes, load_v2_repo_records, repo_id
from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE

MAX_CARD_ROWS = 500


def _auth_maturity(patterns: "Counter[str]", http_in: int) -> str:
    """Classify a repo's auth posture from its pattern counts and http-in count."""
    if patterns.get("secured-authenticated") or patterns.get("spring-pre-authorize"):
        return "authenticated-default"
    if patterns.get("permit-all-config") or patterns.get("is-anonymous"):
        return "anonymous-or-permit-all"
    if http_in and not patterns:
        return "http-without-auth-annotations"
    return "unknown"


def _collect_repo_auth(data: dict[str, Any]) -> dict[str, Any]:
    """Build one repo's auth card from its `auth` and `http-in` flow nodes."""
    rid = repo_id(data)
    patterns: Counter[str] = Counter()
    http_in = 0
    public_paths: list[str] = []

    for node in iter_nodes(data):
        family = node.get("family", "")
        if family == "auth":
            label = (node.get("detail") or {}).get("pattern", "unknown")
            patterns[label] += 1
        elif family == "http-in":
            http_in += 1
            path = (node.get("detail") or {}).get("path", "")
            is_public = patterns.get("permit-all-config") or patterns.get("is-anonymous")
            if is_public and path and len(public_paths) < 10:
                public_paths.append(path)

    frameworks = data.get("frameworks") or []
    maturity = _auth_maturity(patterns, http_in)

    return {
        "repo": rid,
        "frameworks": frameworks,
        "http_in_count": http_in,
        "auth_patterns": dict(patterns.most_common(12)),
        "maturity": maturity,
        "sample_public_paths": public_paths[:5],
    }


def write_auth_models_catalog(
    metabase_root: Path,
    repo_jsons: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """Write auth-model cards (JSONL + markdown) to conventions/ and return them."""
    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    cards = [_collect_repo_auth(data) for data in records]

    conv_dir = metabase_root / "conventions"
    conv_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = conv_dir / "auth-models.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")

    fleet_patterns: Counter[str] = Counter()
    maturity_counts: Counter[str] = Counter()
    for card in cards:
        maturity_counts[card["maturity"]] += 1
        for pat, count in card["auth_patterns"].items():
            fleet_patterns[pat] += count

    body_parts = [
        "# Auth-model conventions (v2)\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Per-repo cards from `auth` and `http-in` flow nodes. High counts of "
        "`permit-all` / `is-anonymous` warrant manual review._\n",
        "\n## Fleet pattern frequency\n",
        md_table(
            ["Pattern", "Count"],
            [[p, str(c)] for p, c in fleet_patterns.most_common(30)],
        ),
        "\n## Maturity buckets\n",
        md_table(
            ["Bucket", "Repos"],
            [[k, str(v)] for k, v in maturity_counts.most_common()],
        ),
        "\n## Per-repo cards (sample)\n",
        md_table(
            ["Repo", "Maturity", "HTTP-in", "Top auth patterns"],
            [
                [
                    c["repo"],
                    c["maturity"],
                    str(c["http_in_count"]),
                    ", ".join(f"{k}({v})" for k, v in list(c["auth_patterns"].items())[:4]),
                ]
                for c in sorted(cards, key=lambda x: x["repo"])[:MAX_CARD_ROWS]
            ],
        ),
    ]
    tail = max(0, len(cards) - MAX_CARD_ROWS)
    if tail:
        body_parts.append(f"\n_{tail} more in `auth-models.jsonl`._\n")

    (conv_dir / "auth-models.md").write_text("\n".join(body_parts), encoding="utf-8")
    return cards
