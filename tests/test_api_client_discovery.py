"""Discovery + promote flow for candidate api-clients.json bindings (plan B11)."""

from __future__ import annotations

import json
from pathlib import Path

from src2sink.aggregators.api_client_discovery import (
    DISCOVERED_FILE,
    STATUS_ACCEPTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    discover_api_clients,
    promote_api_clients,
)
from src2sink.schema import SCHEMA_VERSION


def _write_repo_json(metabase: Path, group: str, name: str, **extra) -> None:
    d = {"schema_version": SCHEMA_VERSION, "group": group, "name": name,
         "nodes": [], "dependencies_internal": []}
    d.update(extra)
    out = metabase / "repos" / group / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d), encoding="utf-8")


def _write_pom(repos_root: Path, group: str, name: str, gid: str, aid: str) -> None:
    p = repos_root / group / name / "pom.xml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"<project><groupId>{gid}</groupId><artifactId>{aid}</artifactId></project>",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A consumer depending on a client lib published by a scanned target service."""
    metabase, repos = tmp_path / "mb", tmp_path / "repos"
    # Target service: publishes sql-runner-api-client, and exposes an http-in path.
    _write_pom(repos, "acme", "sql-runner-api", "com.example.acme", "sql-runner-api-client")
    _write_repo_json(
        metabase, "acme", "sql-runner-api",
        nodes=[{"family": "http-in", "detail": {"path": "/query"}}],
    )
    # Consumer: declares an internal dep on that client artifact.
    _write_repo_json(
        metabase, "acme", "reporting",
        dependencies_internal=[
            {"groupId": "com.example.acme", "artifactId": "sql-runner-api-client",
             "version": "1.2.0", "kind": "internal"},
            {"groupId": "com.example.acme", "artifactId": "commons-util",  # not a client
             "version": "3.0", "kind": "internal"},
        ],
    )
    return metabase, repos


def _candidates(metabase: Path) -> list[dict]:
    data = json.loads((metabase / DISCOVERED_FILE).read_text(encoding="utf-8"))
    return data["candidates"]


def test_discovery_resolves_target_and_paths(tmp_path):
    metabase, repos = _fixture(tmp_path)
    n = discover_api_clients(metabase, None, repos)
    assert n == 1
    cands = _candidates(metabase)
    assert len(cands) == 1  # commons-util (non-client) is ignored
    c = cands[0]
    assert c["maven_artifact"] == "sql-runner-api-client"
    assert c["target_repo"] == "acme/sql-runner-api"          # resolved via identity index
    assert c["paths"] == ["/query"]                           # from target http-in
    assert c["confidence"] == "high"                          # resolved + scanned + paths
    assert c["status"] == STATUS_PENDING
    assert c["evidence"]["consumers"] == ["acme/reporting"]


def test_unresolved_client_is_low_confidence(tmp_path):
    metabase = tmp_path / "mb"
    repos = tmp_path / "repos"
    repos.mkdir()
    _write_repo_json(
        metabase, "acme", "reporting",
        dependencies_internal=[
            {"groupId": "ext", "artifactId": "vendor-sdk", "version": "1", "kind": "internal"},
        ],
    )
    discover_api_clients(metabase, None, repos)
    c = _candidates(metabase)[0]
    assert c["target_repo"] == ""
    assert c["confidence"] == "low"
    assert c["evidence"]["resolved"] is False


def test_reviewer_decision_preserved_on_rediscovery(tmp_path):
    metabase, repos = _fixture(tmp_path)
    discover_api_clients(metabase, None, repos)
    # Reviewer accepts and tightens the import prefix.
    path = metabase / DISCOVERED_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["candidates"][0]["status"] = STATUS_ACCEPTED
    data["candidates"][0]["import_prefix"] = "com.example.acme.sqlrunner.client"
    path.write_text(json.dumps(data), encoding="utf-8")
    # Re-run discovery: the accepted status and tuned field survive.
    discover_api_clients(metabase, None, repos)
    c = _candidates(metabase)[0]
    assert c["status"] == STATUS_ACCEPTED
    assert c["import_prefix"] == "com.example.acme.sqlrunner.client"


def test_promote_only_merges_accepted_and_is_idempotent(tmp_path):
    metabase, repos = _fixture(tmp_path)
    discover_api_clients(metabase, None, repos)
    target = tmp_path / "api-clients.json"

    # Nothing accepted yet → promote is a no-op, file not created.
    assert promote_api_clients(metabase, target) == 0
    assert not target.exists()

    # Accept the candidate, then promote.
    path = metabase / DISCOVERED_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["candidates"][0]["status"] = STATUS_ACCEPTED
    path.write_text(json.dumps(data), encoding="utf-8")

    assert promote_api_clients(metabase, target) == 1
    bindings = json.loads(target.read_text(encoding="utf-8"))["bindings"]
    assert len(bindings) == 1
    assert bindings[0]["target_repo"] == "acme/sql-runner-api"
    assert bindings[0]["maven_artifact"] == "sql-runner-api-client"

    # Idempotent: promoting again updates in place, no duplicate.
    assert promote_api_clients(metabase, target) == 1
    bindings = json.loads(target.read_text(encoding="utf-8"))["bindings"]
    assert len(bindings) == 1


def test_promote_ignores_rejected(tmp_path):
    metabase, repos = _fixture(tmp_path)
    discover_api_clients(metabase, None, repos)
    path = metabase / DISCOVERED_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["candidates"][0]["status"] = STATUS_REJECTED
    path.write_text(json.dumps(data), encoding="utf-8")
    target = tmp_path / "api-clients.json"
    assert promote_api_clients(metabase, target) == 0
    assert not target.exists()
