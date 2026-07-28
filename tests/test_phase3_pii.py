"""Phase 3 PII lifecycle, ROPA, and convention card tests."""

from __future__ import annotations

import json
from pathlib import Path

from src2sink.aggregators.auth_cards import _collect_repo_auth
from src2sink.aggregators.crypto_cards import _collect_repo_crypto
from src2sink.aggregators.pii_lifecycle import collect_pii_touchpoints, write_pii_lifecycle_graph
from src2sink.aggregators.ropa import build_ropa_activities
from src2sink.models.pii_lifecycle import normalize_field_key
from src2sink.trace_batch import _safe_slug, load_catalogue_targets


def test_normalize_field_key_phone() -> None:
    assert normalize_field_key("phoneNumber") == "phone"
    assert normalize_field_key("PHONE") == "phone"


def test_pii_lifecycle_stages_from_fixture() -> None:
    data = {
        "schema_version": 2,
        "group": "crm",
        "name": "contacts-api",
        "nodes": [
            {
                "family": "pii-field",
                "kind": "source",
                "file": "Contact.java",
                "line": 10,
                "detail": {"field_name": "phone"},
                "pii_classification": "direct-pii",
            },
            {
                "family": "sql",
                "kind": "sink",
                "file": "Contact.java",
                "line": 40,
                "detail": {"symbol": "execute", "execution": True},
            },
            {
                "family": "pii-storage",
                "kind": "sink",
                "file": "Repo.java",
                "line": 5,
                "detail": {"field_name": "phone", "subkind": "jpa"},
                "pii_classification": "direct-pii",
            },
            {
                "family": "pii-log",
                "kind": "sink",
                "file": "Log.java",
                "line": 2,
                "detail": {"field_name": "phone"},
                "pii_classification": "direct-pii",
            },
        ],
        "edges": [],
    }
    touches = collect_pii_touchpoints([data])
    stages = {t.stage for t in touches if t.field_key == "phone"}
    assert "collect" in stages
    assert "store" in stages
    assert "log" in stages
    assert "process" in stages


def test_ropa_projection_from_touches() -> None:
    from src2sink.models.pii_lifecycle import PiiTouchpoint

    touches = [
        PiiTouchpoint(
            repo="a/b",
            stage="store",
            family="pii-storage",
            field_key="phone",
            field_name="phone",
            pii_classification="direct-pii",
            data_class=None,
            file="f.java",
            line=1,
            confidence="medium",
        ),
    ]
    acts = build_ropa_activities(touches)
    assert len(acts) == 1
    assert acts[0].category == "Contact and identity data"
    assert "phone" in acts[0].field_keys


def test_auth_crypto_cards() -> None:
    data = {
        "group": "g",
        "name": "svc",
        "frameworks": ["spring"],
        "nodes": [
            {
                "family": "auth",
                "detail": {"pattern": "spring-pre-authorize"},
                "file": "A.java",
                "line": 1,
            },
            {
                "family": "http-in",
                "detail": {"path": "/api"},
                "file": "A.java",
                "line": 2,
            },
            {
                "family": "crypto-algorithm",
                "detail": {"algorithm": "AES"},
                "file": "C.java",
                "line": 3,
            },
        ],
    }
    auth = _collect_repo_auth(data)
    crypto = _collect_repo_crypto(data)
    assert auth["maturity"] == "authenticated-default"
    assert "AES" in crypto["algorithms"]


def test_write_pii_lifecycle_and_catalogue_slug(tmp_path: Path) -> None:
    metabase = tmp_path / "metabase"
    repos_dir = metabase / "repos" / "g"
    repos_dir.mkdir(parents=True)
    (repos_dir / "r.json").write_text(
        json.dumps({
            "schema_version": 2,
            "group": "g",
            "name": "r",
            "nodes": [
                {
                    "family": "pii-field",
                    "kind": "source",
                    "file": "x.java",
                    "line": 1,
                    "detail": {"field_name": "mobile"},
                    "pii_classification": "direct-pii",
                },
            ],
            "edges": [],
        }),
        encoding="utf-8",
    )
    taint = metabase / "taint"
    taint.mkdir(parents=True)
    (taint / "raw-code-payload-endpoints.jsonl").write_text(
        json.dumps({
            "repo": "g/r",
            "detail": {"endpoint_path": "/queries"},
        })
        + "\n",
        encoding="utf-8",
    )
    write_pii_lifecycle_graph(metabase, [repos_dir / "r.json"])
    assert (metabase / "graphs" / "pii-lifecycle.md").is_file()
    targets = load_catalogue_targets(metabase)
    assert targets == [("g/r", "/queries")]
    assert _safe_slug("g/r", "/queries") == "g-r-queries"
