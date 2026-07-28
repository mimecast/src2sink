"""v2 PII flow roll-up (replaces v1 graphs/pii-flow.md)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..graph_common import iter_nodes, load_v2_repo_records
from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE

MAX_MD_ROWS = 200


@dataclass
class _PiiFlowCounts:
    """Aggregated PII/HTTP/queue touchpoint counters across all repos."""

    by_class: Counter = field(default_factory=Counter)
    by_repo: Counter = field(default_factory=Counter)
    field_counts: Counter = field(default_factory=Counter)
    sensitive_by_repo: Counter = field(default_factory=Counter)
    http_in_by_repo: Counter = field(default_factory=Counter)
    http_out_by_repo: Counter = field(default_factory=Counter)
    queue_by_repo: Counter = field(default_factory=Counter)


def _collect_pii_flow(records: list[dict]) -> _PiiFlowCounts:
    """Tally PII, HTTP, and queue nodes per classification and per repo."""
    c = _PiiFlowCounts()
    for data in records:
        rid = f"{data['group']}/{data['name']}"
        for node in iter_nodes(data):
            family = node.get("family")
            if family in ("pii-field", "pii-log", "pii-storage"):
                cls = node.get("pii_classification") or "unknown"
                c.by_class[cls] += 1
                c.by_repo[rid] += 1
                field_name = (node.get("detail") or {}).get("field_name")
                if field_name:
                    c.field_counts[field_name.lower()] += 1
                if cls in ("sensitive", "special-category-gdpr"):
                    c.sensitive_by_repo[rid] += 1
            elif family == "http-in":
                c.http_in_by_repo[rid] += 1
            elif family == "http-out":
                c.http_out_by_repo[rid] += 1
            elif family in ("queue-in", "queue-out"):
                c.queue_by_repo[rid] += 1
    return c


def write_pii_flow_v2(metabase_root: Path, repo_jsons: list[Path]) -> None:
    """Write the v2 PII flow roll-up markdown (graphs/pii-flow.md)."""
    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    c = _collect_pii_flow(records)
    by_class = c.by_class
    by_repo = c.by_repo
    field_counts = c.field_counts
    sensitive_by_repo = c.sensitive_by_repo
    http_in_by_repo = c.http_in_by_repo
    http_out_by_repo = c.http_out_by_repo
    queue_by_repo = c.queue_by_repo

    graphs_dir = metabase_root / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    md: list[str] = [
        "# PII flow roll-up (v2)\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Aggregated from `pii-field`, `pii-log`, and `pii-storage` nodes. "
        "For lifecycle stages see `pii-lifecycle.md`._\n",
        "\n## By GDPR classification\n",
        md_table(
            ["Classification", "Touchpoints"],
            [[k, str(v)] for k, v in by_class.most_common()],
        ),
        "\n## Top fields (all repos)\n",
        md_table(
            ["Field", "Count"],
            [[k, str(v)] for k, v in field_counts.most_common(40)],
        ),
        "\n## Top repos by PII touchpoints\n",
        md_table(
            ["Repo", "Touchpoints"],
            [[k, str(v)] for k, v in by_repo.most_common(MAX_MD_ROWS)],
        ),
    ]
    tail = len(by_repo) - MAX_MD_ROWS
    if tail > 0:
        md.append(f"\n_{tail} more repos — see `taint/pii-sources.jsonl`._\n")

    # Probable PII handlers: repos that have inbound HTTP endpoints AND
    # reference at least one sensitive/special-category-gdpr PII node.
    handlers = [
        rid for rid in sensitive_by_repo
        if http_in_by_repo.get(rid, 0) > 0
    ]
    handler_rows = sorted(
        handlers,
        key=lambda r: -sensitive_by_repo[r],
    )
    md.append(
        "\n## Probable PII handlers\n"
        "_Repos with inbound HTTP endpoints and `sensitive` or "
        "`special-category-gdpr` PII nodes. Top candidates for PII & "
        "data-minimisation review._\n"
    )
    md.append(md_table(
        ["Repo", "HTTP-in", "HTTP-out", "Queue I/O", "Sensitive count"],
        [[r, str(http_in_by_repo[r]), str(http_out_by_repo.get(r, 0)),
          str(queue_by_repo.get(r, 0)), str(sensitive_by_repo[r])]
         for r in handler_rows[:80]] or [["—", "—", "—", "—", "—"]],
    ))

    (graphs_dir / "pii-flow.md").write_text("\n".join(md), encoding="utf-8")
