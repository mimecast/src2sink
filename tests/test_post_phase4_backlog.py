"""Post–Phase 4 backlog: OpenAPI, index, traces index, material PII cross-repo."""

from __future__ import annotations

import json
from pathlib import Path

from src2sink.aggregators.index_v2 import write_index_v2
from src2sink.aggregators.openapi_edges import (
    build_openapi_inbound_index,
    discover_helm_hosts,
)
from src2sink.aggregators.pii_cross_repo import _material_repos_with_field
from src2sink.aggregators.traces_index import write_traces_index
from src2sink.models.pii_lifecycle import PiiTouchpoint


def test_material_repos_excludes_low_confidence_process_only() -> None:
    touches = [
        PiiTouchpoint(
            repo="a/x",
            stage="process",
            family="sql+near-pii-field",
            field_key="phone",
            field_name="phone",
            pii_classification="direct-pii",
            data_class=None,
            file="f.java",
            line=1,
            confidence="low",
        ),
        PiiTouchpoint(
            repo="b/y",
            stage="store",
            family="pii-storage",
            field_key="phone",
            field_name="phone",
            pii_classification="direct-pii",
            data_class=None,
            file="g.java",
            line=2,
            confidence="medium",
        ),
    ]
    material = _material_repos_with_field(touches, "phone")
    assert "b/y" in material
    assert "a/x" not in material


def test_index_v2_and_traces_index(tmp_path: Path) -> None:
    metabase = tmp_path / "metabase"
    repos_dir = metabase / "repos" / "g"
    repos_dir.mkdir(parents=True)
    (repos_dir / "r.json").write_text(
        json.dumps({
            "schema_version": 2,
            "group": "g",
            "name": "r",
            "primary_language": "java",
            "frameworks": ["spring"],
            "nodes": [
                {
                    "family": "http-in",
                    "kind": "source",
                    "file": "A.java",
                    "line": 1,
                    "detail": {"path": "/api"},
                },
            ],
            "edges": [],
        }),
        encoding="utf-8",
    )
    write_index_v2(metabase, [repos_dir / "r.json"])
    assert (metabase / "index" / "repos.json").is_file()
    assert (metabase / "index" / "by-group.md").is_file()

    traces = metabase / "graphs" / "traces"
    traces.mkdir(parents=True)
    (traces / "g-r-api.md").write_text(
        "# Flow trace: g/r\n\n_Path filter: `/api`_\n",
        encoding="utf-8",
    )
    n = write_traces_index(metabase)
    assert n == 1
    assert (traces / "INDEX.md").is_file()


def test_openapi_path_line_regex() -> None:
    text = """
paths:
  /v1/users/{id}:
    get:
  /health:
    get:
"""
    from src2sink.aggregators.openapi_edges import PATH_LINE_RX, OpenApiSpec

    paths = sorted({m.group(1) for m in PATH_LINE_RX.finditer(text)})
    assert "/v1/users/{id}" in paths
    spec = OpenApiSpec("g/svc", "openapi.yaml", paths=paths, servers=[])
    idx = build_openapi_inbound_index([spec])
    assert "/v1/users/{id}" in idx or any("/v1/users" in k for k in idx)


def test_discover_helm_hosts_empty_without_repos(tmp_path: Path) -> None:
    assert discover_helm_hosts(tmp_path / "missing") == []
