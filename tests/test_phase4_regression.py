"""Phase 4: synthetic fixture repos and regression baselines."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from src2sink.aggregators.pii_cross_repo import build_cross_repo_flows
from src2sink.aggregators.pii_lifecycle import collect_pii_touchpoints
from src2sink.build_metabase_v2 import analyse_repo_v2
from src2sink.extractors.unified import extract_from_file

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic-repos"
BASELINE = Path(__file__).resolve().parent / "fixtures" / "regression-baseline.json"


def test_all_synthetic_repos_meet_baseline() -> None:
    spec = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert spec["schema_version"] == 2
    for repo_id, min_families in spec["repos"].items():
        group, name = repo_id.split("/", 1)
        summary = analyse_repo_v2(
            FIXTURES / group / name,
            group,
            name,
            repo_id,
        )
        counts = Counter(n.family for n in summary.nodes)
        for family, minimum in min_families.items():
            got = counts.get(family, 0)
            assert got >= minimum, f"{repo_id} {family}: got {got}, need >={minimum}"


def test_synthetic_phone_kafka_fixture_extract() -> None:
    pub = (
        FIXTURES / "notifications/sms-gateway/src/EventPublisher.java"
    ).read_text(encoding="utf-8")
    sub = (
        FIXTURES / "notifications/sms-consumer/src/PhoneListener.java"
    ).read_text(encoding="utf-8")
    pub_nodes, _ = extract_from_file(
        repo_id="notifications/sms-gateway",
        rel_path="src/EventPublisher.java",
        language="java",
        source=pub,
    )
    sub_nodes, _ = extract_from_file(
        repo_id="notifications/sms-consumer",
        rel_path="src/PhoneListener.java",
        language="java",
        source=sub,
    )
    assert "queue-pub" in {n.family for n in pub_nodes}
    assert "queue-sub" in {n.family for n in sub_nodes}
    assert any(n.family == "pii-log" for n in sub_nodes)


def test_phone_cross_repo_flow_on_synthetic_pair() -> None:
    records = []
    for name in ("sms-gateway", "sms-consumer"):
        repo_root = FIXTURES / "notifications" / name
        summary = analyse_repo_v2(
            repo_root,
            "notifications",
            name,
            f"notifications/{name}",
        )
        records.append({
            "schema_version": 2,
            "group": "notifications",
            "name": name,
            "nodes": [
                {
                    "family": n.family,
                    "kind": n.kind,
                    "file": n.file,
                    "line": n.line,
                    "detail": n.detail,
                    "pii_classification": n.pii_classification,
                    "data_class": n.data_class,
                    "confidence": n.confidence,
                }
                for n in summary.nodes
            ],
            "edges": [],
        })
    touches = collect_pii_touchpoints(records)
    flows = build_cross_repo_flows(records, touches, field_key="phone")
    queue_flows = [f for f in flows if f["kind"] == "queue"]
    assert any(
        f["source_repo"] == "notifications/sms-gateway"
        and f["target_repo"] == "notifications/sms-consumer"
        for f in queue_flows
    )


def test_sql_runner_api_has_raw_payload_not_safe_crud() -> None:
    for repo_id, expect_raw in (
        ("acme/sql-runner-api", True),
        ("negative/safe-crud", False),
    ):
        group, name = repo_id.split("/", 1)
        summary = analyse_repo_v2(
            FIXTURES / group / name,
            group,
            name,
            repo_id,
        )
        has_raw = any(n.family == "raw-code-payload" for n in summary.nodes)
        assert has_raw is expect_raw, repo_id
