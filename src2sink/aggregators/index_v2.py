"""v2 repo index (replaces v1 index/ for flow-graph metabase)."""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .fleet_pass import records_or_load
from ..graph_common import iter_nodes
from ..renderers.markdown import md_table


def _row_from_record(data: dict[str, Any]) -> dict[str, Any]:
    """Summarise one repo record into an index row of per-family node counts."""
    families: Counter[str] = Counter()
    weak_algos: list[str] = []
    for node in iter_nodes(data):
        families[node.get("family", "")] += 1
        if node.get("family") == "crypto-algorithm":
            algo = (node.get("detail") or {}).get("algorithm", "")
            if algo and algo.upper() in {"MD5", "SHA1", "SHA-1", "DES", "RC4"}:
                weak_algos.append(algo)
    return {
        "group": data["group"],
        "name": data["name"],
        "path": data.get("path", ""),
        "primary_language": data.get("primary_language", "unknown"),
        "frameworks": data.get("frameworks") or [],
        "nodes": len(data.get("nodes", [])),
        "edges": len(data.get("edges", [])),
        "http_in": families.get("http-in", 0),
        "http_out": families.get("http-out", 0),
        "sql": families.get("sql", 0),
        "raw_payload": families.get("raw-code-payload", 0),
        "sql_payload_out": families.get("sql-payload-out", 0),
        "pii_field": families.get("pii-field", 0),
        "pii_log": families.get("pii-log", 0),
        "queue": families.get("queue-pub", 0) + families.get("queue-sub", 0),
        "crypto": families.get("crypto-algorithm", 0),
        "weak_algos": sorted(set(weak_algos)),
    }


def write_index_v2(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    rows: list[dict[str, Any]] | None = None,
) -> None:
    """Write the v2 repo index JSON and the all/by-group/by-language markdown pages.

    ``rows`` lets a caller that has already streamed the fleet hand the mapped
    rows in, rather than this parsing the metabase again (`OI-41`).
    """
    if rows is None:
        rows = [_row_from_record(d) for d in records_or_load(None, metabase_root, repo_jsons)]
    index_dir = metabase_root / "index"
    index_dir.mkdir(parents=True, exist_ok=True)

    (index_dir / "repos.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    md = [
        "# All repos (v2 index)\n",
        f"_Generated {ts} from `schema_version: 2` JSON summaries._\n",
        f"_{len(rows)} repos._\n\n",
        md_table(
            [
                "Group / Repo",
                "Lang",
                "Frameworks",
                "Nodes",
                "HTTP-in",
                "HTTP-out",
                "SQL",
                "Raw payload",
                "PII fields",
            ],
            sorted(
                [
                    [
                        f"[{r['group']}/{r['name']}](../repos/{r['group']}/{r['name']}.md)",
                        r["primary_language"],
                        ", ".join(r["frameworks"]) or "—",
                        str(r["nodes"]),
                        str(r["http_in"]),
                        str(r["http_out"]),
                        str(r["sql"]),
                        str(r["raw_payload"]),
                        str(r["pii_field"]),
                    ]
                    for r in rows
                ],
                key=lambda row: row[0].lower(),
            ),
        ),
    ]
    (index_dir / "repos.md").write_text("\n".join(md), encoding="utf-8")

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_group[r["group"]].append(r)
    out = [f"# Repos by group (v2)\n\n_Generated {ts}._\n\n"]
    for g in sorted(by_group):
        out.append(f"## {g} ({len(by_group[g])} repos)\n")
        out.append(
            md_table(
                ["Repo", "Lang", "Nodes", "Raw payload", "PII"],
                [
                    [
                        f"[{r['name']}](../repos/{g}/{r['name']}.md)",
                        r["primary_language"],
                        str(r["nodes"]),
                        str(r["raw_payload"]),
                        str(r["pii_field"]),
                    ]
                    for r in sorted(by_group[g], key=lambda x: x["name"].lower())
                ],
            ),
        )
    (index_dir / "by-group.md").write_text("\n".join(out), encoding="utf-8")

    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_lang[r["primary_language"]].append(r)
    out = ["# Repos by primary language (v2)\n\n"]
    for lang in sorted(by_lang):
        out.append(f"## {lang} ({len(by_lang[lang])} repos)\n")
        out.append(
            md_table(
                ["Group", "Repo", "Nodes", "HTTP-in"],
                [
                    [
                        r["group"],
                        f"[{r['name']}](../repos/{r['group']}/{r['name']}.md)",
                        str(r["nodes"]),
                        str(r["http_in"]),
                    ]
                    for r in sorted(by_lang[lang], key=lambda x: (x["group"], x["name"]))
                ],
            ),
        )
    (index_dir / "by-language.md").write_text("\n".join(out), encoding="utf-8")
