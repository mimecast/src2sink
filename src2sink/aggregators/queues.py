"""Queue / messaging graph aggregator — v2 queue-pub / queue-sub nodes."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..graph_common import iter_nodes, load_v2_repo_records, repo_id
from ..sanitize import UNTRUSTED_CONTENT_NOTICE, for_markdown, for_mermaid_label
from ..renderers.markdown import md_table


def _collect_queue_topics(records: list[dict[str, Any]]) -> dict[str, dict[str, set[str]]]:
    """Map each topic to producing/consuming repos and messaging systems."""
    topics: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"produce": set(), "consume": set(), "systems": set()},
    )
    for data in records:
        rid = repo_id(data)
        for node in iter_nodes(data):
            family = node.get("family", "")
            detail = node.get("detail") or {}
            topic = detail.get("topic", "")
            if not topic or topic == "?":
                continue
            role = {"queue-pub": "produce", "queue-sub": "consume"}.get(family)
            if not role:
                continue
            topics[topic][role].add(rid)
            system = detail.get("system") or ""
            if system:
                topics[topic]["systems"].add(system)
    return topics


def _queue_rows_and_orphans(
    topics: dict[str, dict[str, set[str]]],
) -> tuple[list[list[str]], list[str], list[str]]:
    """Build topic table rows plus produce-only and consume-only orphan lists."""
    rows: list[list[str]] = []
    orphans_produce: list[str] = []
    orphans_consume: list[str] = []
    for topic, dirs in sorted(topics.items()):
        producers = sorted(dirs["produce"])
        consumers = sorted(dirs["consume"])
        systems = sorted(dirs["systems"])
        rows.append([
            topic,
            ", ".join(systems) if systems else "—",
            ", ".join(producers) if producers else "—",
            ", ".join(consumers) if consumers else "—",
        ])
        if producers and not consumers:
            orphans_produce.append(topic)
        if consumers and not producers:
            orphans_consume.append(topic)
    return rows, orphans_produce, orphans_consume


def _orphan_line(label: str, topics: list[str]) -> str:
    """Format a labelled bullet listing orphan topics (capped at 30)."""
    return (
        f"- **{label}** ({len(topics)}): "
        # topics are untrusted extracted literals emitted outside a table cell.
        + ", ".join(for_markdown(t, max_len=80) for t in topics[:30])
        + (" …" if len(topics) > 30 else "")
        + "\n"
    )


def _queue_mermaid(topics: dict[str, dict[str, set[str]]]) -> list[str]:
    """Render a Mermaid flowchart for topics that have both producers and consumers."""
    both = [t for t, d in topics.items() if d["produce"] and d["consume"]][:25]
    if not both:
        return []
    md = ["\n## Mermaid (topics with producers and consumers)\n\n```mermaid\nflowchart LR\n"]
    for topic in both:
        d = topics[topic]
        # Both the node id and the label are attacker-influenced: the id must be a
        # strict slug (drop everything non-alphanumeric) or the topic's quotes /
        # newlines break out of the node; the label is neutralised separately.
        tid = re.sub(r"[^A-Za-z0-9_]", "_", topic)[:50]
        topic_label = for_mermaid_label(topic)
        for p in sorted(d["produce"])[:3]:
            pid = re.sub(r"[^A-Za-z0-9_]", "_", p)[:30]
            md.append(f'  {pid}["{for_mermaid_label(p)}"] -->|{topic_label}| hub_{tid}["{topic_label}"]\n')
        for c in sorted(d["consume"])[:3]:
            cid = re.sub(r"[^A-Za-z0-9_]", "_", c)[:30]
            md.append(f'  hub_{tid} --> {cid}["{for_mermaid_label(c)}"]\n')
    md.append("```\n")
    return md


def _write_queue_jsonl(path: Path, topics: dict[str, dict[str, set[str]]]) -> None:
    """Write one JSON line per topic with its systems, producers, and consumers."""
    with path.open("w", encoding="utf-8") as fh:
        for topic, dirs in sorted(topics.items()):
            systems = sorted(dirs["systems"])
            fh.write(json.dumps({
                "topic": topic,
                "queue_types": systems,
                "queue_type": ", ".join(systems) if systems else None,
                "producers": sorted(dirs["produce"]),
                "consumers": sorted(dirs["consume"]),
            }, ensure_ascii=False) + "\n")


def write_queue_graph(metabase_root: Path, repo_jsons: list[Path]) -> None:
    """Write the queue/messaging graph markdown + jsonl from v2 queue nodes."""
    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    topics = _collect_queue_topics(records)

    out_dir = metabase_root / "graphs"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, orphans_produce, orphans_consume = _queue_rows_and_orphans(topics)
    md: list[str] = [
        "# Queue / messaging graph (v2)\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Producers (`queue-pub`) and consumers (`queue-sub`) per topic from "
        "v2 flow nodes. **Queue type** is the `detail.system` from the extractor "
        "(kafka, rabbitmq, sqs, sns, redis-stream, nats, jms, …)._\n",
        "\n## Topics\n",
        md_table(
            ["Topic", "Queue type", "Producers", "Consumers"],
            rows or [["—", "—", "—", "—"]],
        ),
    ]
    if orphans_produce or orphans_consume:
        md.append("\n## Orphan topics\n")
        if orphans_produce:
            md.append(_orphan_line("Produce-only", orphans_produce))
        if orphans_consume:
            md.append(_orphan_line("Consume-only", orphans_consume))
    md.extend(_queue_mermaid(topics))

    (out_dir / "queue-graph.md").write_text("\n".join(md), encoding="utf-8")
    _write_queue_jsonl(out_dir / "queue-graph.jsonl", topics)
