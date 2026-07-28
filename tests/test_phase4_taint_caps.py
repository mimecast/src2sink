"""Phase 4: taint catalogue markdown size caps."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src2sink.aggregators.taint_catalogs import MAX_MD_ROWS, MAX_PII_MD_BYTES, aggregate_taint_catalogs_v2


def _synthetic_pii_heavy_repo(repo_id: str, n_fields: int) -> dict:
    nodes = []
    for i in range(n_fields):
        nodes.append({
            "family": "pii-field",
            "kind": "source",
            "file": f"src/Model{i}.java",
            "line": i + 1,
            "detail": {"field_name": "phone"},
            "pii_classification": "direct-pii",
            "confidence": "medium",
        })
    group, name = repo_id.split("/", 1)
    return {
        "schema_version": 2,
        "group": group,
        "name": name,
        "nodes": nodes,
        "edges": [],
    }


def test_pii_sources_md_under_byte_cap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repos = root / "repos" / "synthetic"
        repos.mkdir(parents=True)
        data = _synthetic_pii_heavy_repo("synthetic/pii-heavy", 8000)
        json_path = repos / "pii-heavy.json"
        json_path.write_text(json.dumps(data), encoding="utf-8")

        aggregate_taint_catalogs_v2(root, [json_path])

        pii_md = root / "taint" / "pii-sources.md"
        assert pii_md.is_file()
        size = pii_md.stat().st_size
        assert size <= MAX_PII_MD_BYTES, (
            f"pii-sources.md is {size} bytes (cap {MAX_PII_MD_BYTES})"
        )
        text = pii_md.read_text(encoding="utf-8")
        assert "truncated" in text.lower() or "pii-sources.jsonl" in text

        jsonl = root / "taint" / "pii-sources.jsonl"
        assert jsonl.is_file()
        line_count = sum(1 for _ in jsonl.open(encoding="utf-8"))
        assert line_count == 8000


def test_taint_detail_table_row_cap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repos = root / "repos" / "synthetic"
        repos.mkdir(parents=True)
        nodes = []
        for i in range(MAX_MD_ROWS + 50):
            nodes.append({
                "family": "file",
                "kind": "sink",
                "file": f"src/W{i}.java",
                "line": i,
                "detail": {"symbol": "write"},
                "confidence": "medium",
            })
        data = {
            "schema_version": 2,
            "group": "synthetic",
            "name": "files",
            "nodes": nodes,
            "edges": [],
        }
        json_path = repos / "files.json"
        json_path.write_text(json.dumps(data), encoding="utf-8")
        aggregate_taint_catalogs_v2(root, [json_path])

        md = (root / "taint" / "file-sinks.md").read_text(encoding="utf-8")
        assert "additional rows" in md or "file-sinks.jsonl" in md
        body_rows = [ln for ln in md.splitlines() if ln.startswith("| ") and "---" not in ln]
        # header + sample rows only (not unbounded)
        assert len(body_rows) <= MAX_MD_ROWS + 5
