"""Characterization (golden) tests pinning current behavior of the hot paths.

These snapshots exist to make the Phase B security fixes and the Phase C
cognitive-complexity refactors *provably behavior-preserving* (see
the delivery plan). They capture the current output of the highest-
complexity / most-refactored functions:

* ``build_metabase_v2.analyse_repo_v2``            (whole per-repo extraction)
* ``repo_utils._build_repo_artifact_index``        (cx 40)
* ``repo_utils._build_component_identity_index``   (cx 34)
* ``aggregators.queues.write_queue_graph``         (cx 34)
* ``aggregators.taint_writers.write_pii_catalogues`` (cx 21)
* ``trace.run_trace`` + upstream helpers           (cx 19–24)

When a Phase B change *intentionally* alters output (e.g. snippet redaction in
write_pii_catalogues), refresh the affected snapshot deliberately with
``UPDATE_METABASE_SNAPSHOTS=1`` and review the diff — an unexplained diff during
a Phase C refactor is a regression.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from snapshot_utils import load_snapshot, normalize_extraction, write_snapshot

from src2sink import repo_utils
from src2sink.aggregators.queues import write_queue_graph
from src2sink.aggregators.taint_buckets import collect_taint_buckets
from src2sink.aggregators.taint_writers import write_pii_catalogues
from src2sink.build_metabase_v2 import analyse_repo_v2, summary_to_dict
from src2sink.trace import run_trace

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYN = FIXTURES / "synthetic-repos"
SNAPSHOTS = FIXTURES / "characterization-snapshots"
UPDATE = os.environ.get("UPDATE_METABASE_SNAPSHOTS", "").lower() in {"1", "true", "yes"}


def _assert_snapshot(name: str, actual: dict[str, Any]) -> None:
    path = SNAPSHOTS / f"{name}.json"
    if UPDATE:
        write_snapshot(path, actual)
        pytest.skip(f"Updated snapshot {name}")
    expected = load_snapshot(path)
    assert actual == expected, (
        f"Characterization snapshot mismatch for {name}. If this change is "
        "intentional, refresh with UPDATE_METABASE_SNAPSHOTS=1 and review the diff."
    )


def _records_for(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Build v2 summary dicts for the given (group, name) fixture repos."""
    records: list[dict[str, Any]] = []
    for group, name in pairs:
        summary = analyse_repo_v2(SYN / group / name, group, name, f"{group}/{name}")
        records.append(summary_to_dict(summary))
    return records


def _write_records(root: Path, records: list[dict[str, Any]]) -> list[Path]:
    """Write records to root/repos/<group>/<name>.json and return the paths."""
    jsons: list[Path] = []
    for rec in records:
        out = root / "repos" / rec["group"]
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"{rec['name']}.json"
        jp.write_text(json.dumps(rec), encoding="utf-8")
        jsons.append(jp)
    return jsons


def _coord_dict(d: dict[tuple[str, str], str]) -> dict[str, str]:
    """Render a ``(group, artifact) -> path`` dict with string keys for JSON."""
    return {f"{g}:{a}": v for (g, a), v in sorted(d.items())}


# ---------------------------------------------------------------------------
# analyse_repo_v2 — whole per-repo extraction pipeline
# ---------------------------------------------------------------------------


def _norm_summary(summary: Any) -> dict[str, Any]:
    base = normalize_extraction(summary.nodes, summary.edges)
    base.update(
        {
            "primary_language": summary.primary_language,
            "language_breakdown": dict(sorted(summary.language_breakdown.items())),
            "build_systems": sorted(summary.build_systems),
            "frameworks": sorted(summary.frameworks),
            "dependencies_internal": sorted(
                f"{d.get('groupId', '')}:{d.get('artifactId', '')}"
                for d in summary.dependencies_internal
            ),
            "dependencies_external_count": summary.dependencies_external_count,
        }
    )
    return base


@pytest.mark.parametrize(
    ("group", "name"),
    [
        ("dataplatform", "query-api-service"),
        ("dataplatform", "api-consumer"),
        ("notifications", "sms-gateway"),
        ("python", "sms-sender"),
        ("negative", "safe-crud"),
    ],
)
def test_char_analyse_repo_v2(group: str, name: str) -> None:
    summary = analyse_repo_v2(SYN / group / name, group, name, f"{group}/{name}")
    _assert_snapshot(f"analyse_{group}_{name}", _norm_summary(summary))


# ---------------------------------------------------------------------------
# repo_utils index builders — deterministic tmp fixture
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _index_fixture(tmp_path: Path) -> Path:
    repos = tmp_path / "repos"
    _write(
        repos / "platform" / "widget-service" / "pom.xml",
        "<project><groupId>com.acme</groupId>"
        "<artifactId>widget</artifactId></project>",
    )
    _write(
        repos / "platform" / "ui-kit" / "package.json",
        json.dumps({"name": "@acme/ui-kit", "version": "1.0.0"}),
    )
    _write(
        repos / "data" / "pyproj-lib" / "pyproject.toml",
        '[project]\nname = "acme-pytools"\n',
    )
    _write(repos / "systems" / "gomod" / "go.mod", "module github.com/acme/thing\n")
    return repos


def test_char_repo_artifact_index(tmp_path: Path) -> None:
    repos = _index_fixture(tmp_path)
    pom_by_coord, pom_by_artifact, npm_by_name = repo_utils._build_repo_artifact_index(
        repos
    )
    _assert_snapshot(
        "repo_artifact_index",
        {
            "pom_by_coord": _coord_dict(pom_by_coord),
            "pom_by_artifact": {k: sorted(v) for k, v in sorted(pom_by_artifact.items())},
            "npm_by_name": dict(sorted(npm_by_name.items())),
        },
    )


def test_char_component_identity_index(tmp_path: Path) -> None:
    repos = _index_fixture(tmp_path)
    by_coord, by_name, by_full = repo_utils._build_component_identity_index(repos)
    _assert_snapshot(
        "component_identity_index",
        {
            "by_coord": _coord_dict(by_coord),
            "by_name": {k: sorted(v) for k, v in sorted(by_name.items())},
            "by_full": dict(sorted(by_full.items())),
        },
    )


# ---------------------------------------------------------------------------
# aggregators — queue graph & PII catalogues
# ---------------------------------------------------------------------------


def test_char_queue_graph(tmp_path: Path) -> None:
    records = _records_for([("notifications", "sms-gateway"), ("notifications", "sms-consumer")])
    jsons = _write_records(tmp_path, records)
    write_queue_graph(tmp_path, jsons)
    jsonl = tmp_path / "graphs" / "queue-graph.jsonl"
    rows = [json.loads(ln) for ln in jsonl.read_text(encoding="utf-8").splitlines() if ln]
    rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
    _assert_snapshot("queue_graph", {"row_count": len(rows), "rows": rows})


def test_char_pii_catalogues(tmp_path: Path) -> None:
    records = _records_for(
        [
            ("notifications", "sms-gateway"),
            ("notifications", "sms-consumer"),
            ("python", "sms-sender"),
        ]
    )
    jsons = _write_records(tmp_path, records)
    buckets = collect_taint_buckets(jsons)
    taint_dir = tmp_path / "taint"
    taint_dir.mkdir(parents=True, exist_ok=True)
    write_pii_catalogues(taint_dir, buckets)
    out: dict[str, Any] = {}
    for jsonl in sorted(taint_dir.glob("*.jsonl")):
        rows = [
            json.loads(ln)
            for ln in jsonl.read_text(encoding="utf-8").splitlines()
            if ln
        ]
        rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
        out[jsonl.name] = rows
    _assert_snapshot("pii_catalogues", out)


# ---------------------------------------------------------------------------
# trace.run_trace + upstream resolution
# ---------------------------------------------------------------------------


def test_char_run_trace(tmp_path: Path) -> None:
    records = _records_for(
        [("dataplatform", "api-consumer"), ("dataplatform", "query-api-service")]
    )
    report = run_trace(
        tmp_path,
        "dataplatform/query-api-service",
        records=records,
        producer_indices=[],
    )
    normalized = {
        "target_repo": report.target_repo,
        "path_filter": report.path_filter,
        "inbound_count": len(report.inbound),
        "raw_payload_count": len(report.raw_payloads),
        "sql_sink_count": len(report.sql_sinks),
        "store_count": len(report.stores),
        "upstream": sorted(
            [
                {"source_repo": u.source_repo, "kind": u.kind, "confidence": u.confidence}
                for u in report.upstream
            ],
            key=lambda u: (u["source_repo"], u["kind"]),
        ),
    }
    _assert_snapshot("run_trace_query_api", normalized)
