"""Tests for OpenAPI / Helm discovery and matching."""

from __future__ import annotations

from src2sink.aggregators.openapi_discovery import (
    discover_helm_hosts,
    discover_openapi_specs,
    repo_from_under_repos,
)
from src2sink.aggregators.openapi_match import build_openapi_inbound_index, match_http_out_to_openapi
from src2sink.aggregators.openapi_models import OpenApiSpec

_SPEC = """openapi: 3.0.0
servers:
  - url: https://query-api-service/api
paths:
  /queries:
    get: {}
  /queries/{id}:
    get: {}
"""


def _make(tmp_path):
    repo = tmp_path / "repos" / "dp" / "query-api-service"
    repo.mkdir(parents=True)
    (repo / "openapi.yaml").write_text(_SPEC, encoding="utf-8")
    (repo / "values.yaml").write_text("ingress:\nhost: query-api.internal\n", encoding="utf-8")
    return tmp_path / "repos"


def test_repo_from_under_repos(tmp_path):
    repos = tmp_path / "repos"
    spec = repos / "g" / "r" / "openapi.yaml"
    assert repo_from_under_repos(spec, repos) == "g/r"
    assert repo_from_under_repos(repos / "toplevel.yaml", repos) is None


def test_discover_openapi_specs(tmp_path):
    repos = _make(tmp_path)
    specs = discover_openapi_specs(repos)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.target_repo == "dp/query-api-service"
    assert "/queries" in spec.paths
    assert any("query-api-service" in s for s in spec.servers)
    assert discover_openapi_specs(tmp_path / "nope") == []


def test_discover_helm_hosts(tmp_path):
    repos = _make(tmp_path)
    hosts = discover_helm_hosts(repos)
    assert any(h["host"] == "query-api.internal" for h in hosts)


def test_openapi_match():
    spec = OpenApiSpec(
        target_repo="dp/query-api-service",
        spec_path="openapi.yaml",
        paths=["/queries"],
        servers=["https://query-api-service/api"],
    )
    inbound = build_openapi_inbound_index([spec])
    assert inbound
    edges = match_http_out_to_openapi(
        [{"group": "apps", "name": "consumer", "nodes": [
            {"family": "http-out", "kind": "sink", "file": "C.java", "line": 1,
             "detail": {"raw": 'post("https://query-api-service/queries")'}},
        ]}],
        inbound,
    )
    assert isinstance(edges, list)
