"""Per-repo crypto agility cards from v2 crypto nodes (Phase 3)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..graph_common import iter_nodes, load_v2_repo_records, repo_id
from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE, for_markdown

WEAK_ALGORITHMS = frozenset({
    "MD5", "SHA1", "SHA-1", "DES", "RC4", "3DES", "ECB",
})
MAX_CARD_ROWS = 500


def _crypto_maturity(has_algorithms: bool, has_key_sources: bool) -> str:
    """Classify a repo's crypto posture from algorithm and key-source presence."""
    if has_algorithms and has_key_sources:
        return "config-driven"
    if has_algorithms:
        return "hardcoded-algorithms"
    if has_key_sources:
        return "config-keys-only"
    return "none-detected"


def _collect_repo_crypto(data: dict[str, Any]) -> dict[str, Any]:
    """Build one repo's crypto card from its crypto-related flow nodes."""
    rid = repo_id(data)
    algorithms: Counter[str] = Counter()
    key_sources: Counter[str] = Counter()
    weak: list[str] = []

    for node in iter_nodes(data):
        family = node.get("family", "")
        detail = node.get("detail") or {}
        if family == "crypto-algorithm":
            algo = (detail.get("algorithm") or "?").upper()
            algorithms[algo] += 1
            if algo in WEAK_ALGORITHMS or "MD5" in algo or "SHA1" in algo:
                # raw is PII-redacted upstream; neutralise the markdown structure of
                # both the sample and the untrusted file path (SAST finding 1 class).
                weak.append(
                    f"{for_markdown(detail.get('raw', algo), max_len=60)} @ "
                    f"{for_markdown(node.get('file'), max_len=80)}:{node.get('line')}"
                )
        elif family == "crypto-config":
            src = detail.get("key_source") or detail.get("source") or "config"
            key_sources[str(src)] += 1
        elif family == "config-security":
            key_sources["config-security"] += 1

    maturity = _crypto_maturity(bool(algorithms), bool(key_sources))

    return {
        "repo": rid,
        "maturity": maturity,
        "algorithms": dict(algorithms.most_common(15)),
        "key_sources": dict(key_sources.most_common(10)),
        "weak_samples": weak[:5],
    }


def write_crypto_agility_catalog(
    metabase_root: Path,
    repo_jsons: list[Path] | None = None,
    *,
    cards: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Write crypto-agility cards (JSONL + markdown) to conventions/ and return them."""
    if cards is None:
        cards = [
            _collect_repo_crypto(data)
            for data in load_v2_repo_records(metabase_root, json_paths=repo_jsons)
        ]

    conv_dir = metabase_root / "conventions"
    conv_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = conv_dir / "crypto-agility.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")

    algo_fleet: Counter[str] = Counter()
    maturity_counts: Counter[str] = Counter()
    weak_repos = 0
    for card in cards:
        maturity_counts[card["maturity"]] += 1
        if card["weak_samples"]:
            weak_repos += 1
        for algo, count in card["algorithms"].items():
            algo_fleet[algo] += count

    body_parts = [
        "# Crypto agility (v2)\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Algorithms from `crypto-algorithm` nodes; key sources from "
        "`crypto-config` / `config-security`. Classify repos as hardcoded vs "
        "config-driven for rotation planning._\n",
        "\n## Fleet algorithms\n",
        md_table(
            ["Algorithm", "Count"],
            [[a, str(c)] for a, c in algo_fleet.most_common(25)],
        ),
        "\n## Maturity buckets\n",
        md_table(
            ["Bucket", "Repos"],
            [[k, str(v)] for k, v in maturity_counts.most_common()],
        ),
        f"\n_Repos with weak algorithm hits: **{weak_repos}**._\n",
        "\n## Per-repo cards (sample)\n",
        md_table(
            ["Repo", "Maturity", "Algorithms", "Key sources", "Weak"],
            [
                [
                    c["repo"],
                    c["maturity"],
                    ", ".join(list(c["algorithms"].keys())[:4]),
                    ", ".join(list(c["key_sources"].keys())[:3]) or "—",
                    "yes" if c["weak_samples"] else "—",
                ]
                for c in sorted(cards, key=lambda x: x["repo"])[:MAX_CARD_ROWS]
            ],
        ),
    ]
    tail = max(0, len(cards) - MAX_CARD_ROWS)
    if tail:
        body_parts.append(f"\n_{tail} more in `crypto-agility.jsonl`._\n")

    md_path = conv_dir / "crypto-agility.md"
    body = "\n".join(body_parts)
    md_path.write_text(body, encoding="utf-8")
    return cards
