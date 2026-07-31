"""Phase 4: cross-repo aggregator tests on synthetic fixture repos."""

from __future__ import annotations

import json
from pathlib import Path

from src2sink.aggregators.queues import write_queue_graph
from src2sink.aggregators.service_calls import collect_service_edges
from src2sink.build_metabase_v2 import analyse_repo_v2, summary_to_dict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic-repos"


def _records_for_group(group: str, names: list[str]) -> list[dict]:
    records: list[dict] = []
    for name in names:
        repo_root = FIXTURES / group / name
        summary = analyse_repo_v2(
            repo_root,
            group,
            name,
            f"{group}/{name}",
        )
        records.append(summary_to_dict(summary))
    return records


def test_service_call_edge_synthetic_pair() -> None:
    records = _records_for_group(
        "acme",
        ["api-consumer", "sql-runner-api"],
    )
    edges, broken = collect_service_edges(records)
    assert any(
        e.source_repo == "acme/api-consumer"
        and e.target_repo == "acme/sql-runner-api"
        for e in edges
    )
    assert isinstance(broken, list)


def test_queue_graph_synthetic_notifications() -> None:
    import tempfile

    records = _records_for_group("notifications", ["sms-gateway", "sms-consumer"])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        jsons: list[Path] = []
        for rec in records:
            out = root / "repos" / rec["group"]
            out.mkdir(parents=True, exist_ok=True)
            jp = out / f"{rec['name']}.json"
            jp.write_text(json.dumps(rec), encoding="utf-8")
            jsons.append(jp)
        write_queue_graph(root, jsons)
        jsonl = root / "graphs" / "queue-graph.jsonl"
        assert jsonl.is_file()
        topics = [json.loads(ln) for ln in jsonl.read_text(encoding="utf-8").splitlines() if ln]
        match = next(
            (t for t in topics if t.get("topic") == "customer-phone-updates"),
            None,
        )
        assert match is not None
        assert match.get("queue_types") == ["kafka"] or match.get("queue_type") == "kafka"
        assert "notifications/sms-gateway" in match["producers"]
        assert "notifications/sms-consumer" in match["consumers"]
