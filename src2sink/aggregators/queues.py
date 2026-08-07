"""Queue / messaging graph aggregator — v2 queue-pub / queue-sub nodes.

Split into `compute_queue_graph` (pure, returns data) and `write_queue_graph`
(the edge that renders and writes it). The first of the fourteen aggregators to
be split this way; see `docs/plans/src2sink-3.0-plan.md`, Phase 1.

The split is what `OI-15` needs. While computing and rendering were one step
there was no result to persist, so answering "which repos consume this topic"
meant re-reading every record in the fleet and re-deriving the whole graph.
`QueueGraph` is that result, and it is deliberately plain — tuples of strings,
no sets — so it can be written to a store as easily as to Markdown.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..graph_common import iter_nodes, load_v2_repo_records, repo_id
from ..sanitize import UNTRUSTED_CONTENT_NOTICE, for_markdown, for_mermaid_label
from ..renderers.markdown import md_table


@dataclass(frozen=True)
class QueueTopic:
    """One topic, with the repos on each side of it."""

    topic: str
    systems: tuple[str, ...]
    producers: tuple[str, ...]
    consumers: tuple[str, ...]

    @property
    def produce_only(self) -> bool:
        """Published by someone, read by nobody — a message going nowhere."""
        return bool(self.producers) and not self.consumers

    @property
    def consume_only(self) -> bool:
        """Read by someone, published by nobody visible — often a fleet-boundary gap."""
        return bool(self.consumers) and not self.producers


@dataclass(frozen=True)
class QueueGraph:
    """Every topic in the fleet and who is on each end of it.

    `topics` stays in **discovery** order rather than sorted, because the Mermaid
    section renders in that order and the table sorts. Sorting here would silently
    reorder the diagram — preserved deliberately, not by accident.
    """

    topics: tuple[QueueTopic, ...]

    @property
    def sorted_topics(self) -> list[QueueTopic]:
        """Topics in name order, which is what the table renders."""
        return sorted(self.topics, key=lambda t: t.topic)


class QueueCollector:
    """Topics and the repos on each side of them, reduced one record at a time.

    The streaming form of `_collect_queue_topics`, so a shared fleet pass can
    drive it alongside the other aggregators (`OI-41`).
    """

    def __init__(self) -> None:
        """Start an empty reduction."""
        self.topics: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: {"produce": set(), "consume": set(), "systems": set()},
        )

    def consume(self, record: dict[str, Any]) -> None:
        """Fold one repo record in."""
        rid = repo_id(record)
        for node in iter_nodes(record):
            family = node.get("family", "")
            detail = node.get("detail") or {}
            topic = detail.get("topic", "")
            if not topic or topic == "?":
                continue
            role = {"queue-pub": "produce", "queue-sub": "consume"}.get(family)
            if not role:
                continue
            self.topics[topic][role].add(rid)
            system = detail.get("system") or ""
            if system:
                self.topics[topic]["systems"].add(system)

    def result(self) -> QueueGraph:
        """The finished graph, in discovery order — see `QueueGraph.topics`."""
        return QueueGraph(topics=tuple(
            QueueTopic(
                topic=topic,
                systems=tuple(sorted(dirs["systems"])),
                producers=tuple(sorted(dirs["produce"])),
                consumers=tuple(sorted(dirs["consume"])),
            )
            for topic, dirs in self.topics.items()
        ))


def _collect_queue_topics(records: list[dict[str, Any]]) -> dict[str, dict[str, set[str]]]:
    """Map each topic to producing/consuming repos and messaging systems."""
    collector = QueueCollector()
    for data in records:
        collector.consume(data)
    return collector.topics


def compute_queue_graph(records: list[dict[str, Any]]) -> QueueGraph:
    """Derive the queue graph from repo records. Pure: no paths, no writes.

    The whole point of Phase 1 — everything above the store now has a value it
    can hand to a renderer or to a persisted index without repeating the work.
    """
    collected = _collect_queue_topics(records)
    return QueueGraph(topics=tuple(
        QueueTopic(
            topic=topic,
            systems=tuple(sorted(dirs["systems"])),
            producers=tuple(sorted(dirs["produce"])),
            consumers=tuple(sorted(dirs["consume"])),
        )
        # Insertion order, matching the Mermaid section. See `QueueGraph.topics`.
        for topic, dirs in collected.items()
    ))


def _queue_rows_and_orphans(
    graph: QueueGraph,
) -> tuple[list[list[str]], list[str], list[str]]:
    """Build topic table rows plus produce-only and consume-only orphan lists."""
    rows: list[list[str]] = []
    orphans_produce: list[str] = []
    orphans_consume: list[str] = []
    for entry in graph.sorted_topics:
        rows.append([
            entry.topic,
            ", ".join(entry.systems) if entry.systems else "—",
            ", ".join(entry.producers) if entry.producers else "—",
            ", ".join(entry.consumers) if entry.consumers else "—",
        ])
        if entry.produce_only:
            orphans_produce.append(entry.topic)
        if entry.consume_only:
            orphans_consume.append(entry.topic)
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


def _queue_mermaid(graph: QueueGraph) -> list[str]:
    """Render a Mermaid flowchart for topics that have both producers and consumers."""
    both = [t for t in graph.topics if t.producers and t.consumers][:25]
    if not both:
        return []
    md = ["\n## Mermaid (topics with producers and consumers)\n\n```mermaid\nflowchart LR\n"]
    for entry in both:
        # Both the node id and the label are attacker-influenced: the id must be a
        # strict slug (drop everything non-alphanumeric) or the topic's quotes /
        # newlines break out of the node; the label is neutralised separately.
        tid = re.sub(r"[^A-Za-z0-9_]", "_", entry.topic)[:50]
        topic_label = for_mermaid_label(entry.topic)
        for p in entry.producers[:3]:
            pid = re.sub(r"[^A-Za-z0-9_]", "_", p)[:30]
            md.append(f'  {pid}["{for_mermaid_label(p)}"] -->|{topic_label}| hub_{tid}["{topic_label}"]\n')
        for c in entry.consumers[:3]:
            cid = re.sub(r"[^A-Za-z0-9_]", "_", c)[:30]
            md.append(f'  hub_{tid} --> {cid}["{for_mermaid_label(c)}"]\n')
    md.append("```\n")
    return md


def render_queue_graph(graph: QueueGraph) -> str:
    """Render the graph as Markdown. Pure: takes a value, returns a string."""
    rows, orphans_produce, orphans_consume = _queue_rows_and_orphans(graph)
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
    md.extend(_queue_mermaid(graph))
    return "\n".join(md)


def queue_graph_records(graph: QueueGraph) -> list[dict[str, Any]]:
    """The graph as plain dicts — one per topic, in the JSONL's sorted order."""
    return [
        {
            "topic": entry.topic,
            "queue_types": list(entry.systems),
            "queue_type": ", ".join(entry.systems) if entry.systems else None,
            "producers": list(entry.producers),
            "consumers": list(entry.consumers),
        }
        for entry in graph.sorted_topics
    ]


def _write_queue_jsonl(path: Path, graph: QueueGraph) -> None:
    """Write one JSON line per topic with its systems, producers, and consumers."""
    with path.open("w", encoding="utf-8") as fh:
        for record in queue_graph_records(graph):
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_queue_graph(
    metabase_root: Path,
    repo_jsons: list[Path],
    *,
    graph: QueueGraph | None = None,
) -> None:
    """Write the queue/messaging graph markdown + jsonl from v2 queue nodes.

    ``graph`` lets a caller that has already streamed the fleet hand the computed
    result in, rather than this parsing the metabase again (`OI-41`).
    """
    if graph is None:
        graph = compute_queue_graph(
            load_v2_repo_records(metabase_root, json_paths=repo_jsons)
        )

    out_dir = metabase_root / "graphs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "queue-graph.md").write_text(render_queue_graph(graph), encoding="utf-8")
    _write_queue_jsonl(out_dir / "queue-graph.jsonl", graph)
