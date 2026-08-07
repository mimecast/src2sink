"""Data-store graph aggregator — v2 config + code store nodes."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..graph_common import iter_nodes, load_v2_repo_records, repo_id, store_key_from_node
from ..sanitize import UNTRUSTED_CONTENT_NOTICE, for_markdown
from ..renderers.markdown import md_table


class StoreCollector:
    """Store->repos, store metadata, and per-repo SQL execution sink counts.

    The reduction `_collect_stores` performed, turned inside out so it can run
    inside a shared streamed pass (`OI-41`). It retains only what it derives —
    a few thousand store keys — never the records it derived them from.
    """

    def __init__(self) -> None:
        """Start an empty reduction."""
        self.store_repos: dict[str, set[str]] = defaultdict(set)
        self.store_meta: dict[str, dict[str, Any]] = {}
        self.sql_execution_by_repo: Counter[str] = Counter()

    def consume(self, record: dict[str, Any]) -> None:
        """Fold one repo record in."""
        rid = repo_id(record)
        for node in iter_nodes(record):
            family = node.get("family", "")
            kind = node.get("kind")
            if family == "data-store" and kind == "store":
                key = store_key_from_node(node)
                if not key:
                    continue
                self.store_repos[key].add(rid)
                detail = node.get("detail") or {}
                self.store_meta.setdefault(key, {
                    "vendor": detail.get("vendor", "?"),
                    "sample_file": f"{node.get('file')}:{node.get('line')}",
                })
            elif family == "sql" and kind == "sink" and (node.get("detail") or {}).get("execution", True):
                self.sql_execution_by_repo[rid] += 1

    def result(self) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]], "Counter[str]"]:
        """The finished reduction, in the shape the renderer expects."""
        return self.store_repos, self.store_meta, self.sql_execution_by_repo


def _collect_stores(
    records: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]], "Counter[str]"]:
    """Collect store->repos, store metadata, and per-repo SQL execution sink counts.

    Retained as the non-streaming entry point; the reduction itself lives in
    :class:`StoreCollector` so one pass can drive it alongside the others.
    """
    collector = StoreCollector()
    for data in records:
        collector.consume(data)
    return collector.result()


def _stores_by_vendor(
    store_repos: dict[str, set[str]], store_meta: dict[str, dict[str, Any]]
) -> dict[str, list[tuple[str, list[str]]]]:
    """Group stores by vendor into (store_key, sorted repos) entries."""
    by_vendor: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for key, repos in sorted(store_repos.items(), key=lambda x: (-len(x[1]), x[0])):
        vendor = store_meta.get(key, {}).get("vendor", key.split(":")[0])
        by_vendor[vendor].append((key, sorted(repos)))
    return by_vendor


def _write_data_store_jsonl(
    path: Path, store_repos: dict[str, set[str]], store_meta: dict[str, dict[str, Any]]
) -> None:
    """Write one JSON line per store with vendor, referencing repos, and sample file."""
    with path.open("w", encoding="utf-8") as fh:
        for key, repos in sorted(store_repos.items()):
            meta = store_meta.get(key, {})
            fh.write(json.dumps({
                "store_key": key,
                "vendor": meta.get("vendor"),
                "repos": sorted(repos),
                "sample_file": meta.get("sample_file"),
            }, ensure_ascii=False) + "\n")


def write_data_store_graph(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    collected: tuple[dict[str, set[str]], dict[str, dict[str, Any]], "Counter[str]"] | None = None,
) -> None:
    """Write the bipartite data-store graph markdown + jsonl from v2 store nodes.

    ``collected`` lets a caller that has already streamed the fleet hand the
    reduction in, rather than this parsing the metabase again (`OI-41`).
    """
    if collected is None:
        collected = _collect_stores(
            load_v2_repo_records(metabase_root, json_paths=repo_jsons)
        )
    store_repos, store_meta, sql_execution_by_repo = collected

    out_dir = metabase_root / "graphs"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_vendor = _stores_by_vendor(store_repos, store_meta)

    md: list[str] = [
        "# Data-store graph (v2)\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Bipartite view: config-discovered stores (`data-store` nodes) and "
        "repos that reference them. SQL execution sink counts show repos with "
        "runtime DB access even when JDBC URL is only in deployed config._\n",
        "\n## Summary\n",
        md_table(
            ["Vendor", "Store count", "Repos touching"],
            [
                [
                    v,
                    str(len(entries)),
                    str(len({r for _, rs in entries for r in rs})),
                ]
                for v, entries in sorted(by_vendor.items())
            ],
        ),
    ]

    for vendor, entries in sorted(by_vendor.items()):
        # vendor is an untrusted extracted config value written into a heading.
        safe_vendor = for_markdown(vendor, max_len=80)
        md.append(f"\n## {safe_vendor}\n\n")
        md.append(md_table(
            ["Store key", "Repos", "Sample config ref"],
            [
                [
                    key,
                    ", ".join(repos[:8]) + (" …" if len(repos) > 8 else ""),
                    store_meta.get(key, {}).get("sample_file", ""),
                ]
                for key, repos in entries[:80]
            ],
        ))
        if len(entries) > 80:
            md.append(f"\n_{len(entries) - 80} more {safe_vendor} stores in jsonl._\n")

    md.append("\n## Repos with SQL execution sinks (no config store required)\n\n")
    md.append(md_table(
        ["Repo", "SQL execution sink count"],
        [
            [repo, str(count)]
            for repo, count in sql_execution_by_repo.most_common(100)
        ],
    ))

    (out_dir / "data-store-graph.md").write_text("\n".join(md), encoding="utf-8")
    _write_data_store_jsonl(out_dir / "data-store-graph.jsonl", store_repos, store_meta)
