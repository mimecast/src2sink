"""OI-42: `promote` merged on trust and rewrote the file lossily.

`docs/api-clients-json.md` §4 documents five disqualifying review gates and a set
of post-conditions to assert after promoting. `promote` performed none of them —
every check was the reviewer's, by hand, and anything missed reached the taint
graph as an authoritative binding.

Two of the five gates are pure computation the tool already does at discovery
time, using code written for `OI-33` and `OI-40`. **50 of 191 candidates in the
first fleet review failed one of them.** The reviewer keeps the judgement calls;
this takes the arithmetic.

Two lossy behaviours alongside, both documented as things the *reviewer* must
work around:

* the file was rewritten as `{"bindings": [...]}`, dropping every other
  top-level key — including the `_comment` carrying the *"never commit —
  internal topology"* notice on a gitignored, sensitivity-marked file;
* duplicate keys were indexed with a dict comprehension, so only the last copy
  was updated and earlier ones went stale while remaining loaded.

The post-conditions from that document are asserted here rather than left as a
checklist for a person: a checklist a tool can run should not be one a person
runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src2sink.aggregators import api_client_discovery as acd


def _metabase(tmp_path: Path, repos: dict[str, list[str]]) -> Path:
    """A metabase where each repo maps to the inbound endpoint paths it declares."""
    root = tmp_path / "metabase"
    for rid, paths in repos.items():
        group, name = rid.split("/", 1)
        d = root / "repos" / group
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps({
            "schema_version": 2, "group": group, "name": name,
            "nodes": [
                {"family": "http-in", "kind": "source", "file": "Api.java",
                 "line": 1, "detail": {"path": p, "method": "GET"}}
                for p in paths
            ],
            "edges": [], "dependencies_internal": [],
        }), encoding="utf-8")
    return root


def _candidate(**over: Any) -> dict[str, Any]:
    base = {
        "target_repo": "commerce/warehouse-service",
        "maven_artifact": "warehouse-service-client",
        "import_prefix": "com.example.warehouse",
        "paths": ["/stock"],
        "payload_fields": ["sql"],
        "service_aliases": ["warehouse-service"],
        "class_patterns": [],
        "status": acd.STATUS_ACCEPTED,
    }
    base.update(over)
    return base


def _discovered(root: Path, candidates: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / acd.DISCOVERED_FILE).write_text(
        json.dumps({"candidates": candidates}), encoding="utf-8",
    )


_FLEET = {
    "commerce/warehouse-service": ["/stock", "/stock/adjust"],
    "commerce/warehouse-service-client": [],          # a library: no endpoints
}


# --- Gate 1: can the binding resolve at all? ---------------------------------


@pytest.mark.parametrize("target", ["", "not-a/real-repo"])
def test_an_unresolvable_target_is_refused(tmp_path, target, capsys):
    """Gate 1, which rejected 8 of 191 by hand.

    An empty or unknown `target_repo` promotes to an edge pointing at a node
    that does not exist. `promote` merged it without comment.
    """
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [_candidate(target_repo=target)])
    out = tmp_path / "api-clients.json"

    assert acd.promote_api_clients(root, out) == 0
    assert "REFUSED" in capsys.readouterr().err


# --- Gate 2: the service, or the client library? -----------------------------


def test_a_client_library_target_is_refused(tmp_path, capsys):
    """Gate 2, which rejected 42 of 191 by hand.

    A binding's target is the service that *receives* the calls. `OI-40` fixed
    this at discovery; nothing stopped a reviewer accepting one anyway, and
    `promote` would take it.
    """
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [_candidate(target_repo="commerce/warehouse-service-client")])
    out = tmp_path / "api-clients.json"

    assert acd.promote_api_clients(root, out) == 0
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "client library" in err
    assert "warehouse-service" in err, "the refusal must name the service it should be"


def test_a_valid_candidate_is_still_promoted(tmp_path):
    """The gates must not reject the thing they exist to let through."""
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [_candidate()])
    out = tmp_path / "api-clients.json"

    assert acd.promote_api_clients(root, out) == 1
    written = json.loads(out.read_text(encoding="utf-8"))["bindings"]
    assert [b["target_repo"] for b in written] == ["commerce/warehouse-service"]


def test_a_refusal_does_not_block_the_rest(tmp_path, capsys):
    """One bad candidate must not cost the reviewer the whole batch."""
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [
        _candidate(),
        _candidate(target_repo="", maven_artifact="broken-client"),
    ])
    out = tmp_path / "api-clients.json"

    assert acd.promote_api_clients(root, out) == 1
    assert "broken-client" in capsys.readouterr().err


# --- the lossy rewrite -------------------------------------------------------


def test_the_handling_notice_survives_promotion(tmp_path):
    """`_comment` carries the "never commit" marker on a sensitive file.

    It was dropped silently and the file still looked correct — `OI-36` with a
    confidentiality consequence.
    """
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [_candidate()])
    out = tmp_path / "api-clients.json"
    out.write_text(json.dumps({
        "_comment": "NEVER COMMIT — internal topology",
        "schema_note": "keep me too",
        "bindings": [],
    }), encoding="utf-8")

    acd.promote_api_clients(root, out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["_comment"] == "NEVER COMMIT — internal topology"
    assert doc["schema_note"] == "keep me too"


def test_every_duplicate_of_a_key_is_updated(tmp_path):
    """Indexing a list with duplicates keeps only the last.

    The earlier copies stayed at their old values — still present, still loaded
    by `_load_bindings`, now disagreeing with their twin about the same binding.
    """
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [_candidate(import_prefix="com.example.tightened")])
    out = tmp_path / "api-clients.json"
    dup = {
        "target_repo": "commerce/warehouse-service",
        "maven_artifact": "warehouse-service-client",
        "import_prefix": "com.example.STALE",
    }
    out.write_text(json.dumps({"bindings": [dict(dup), dict(dup)]}), encoding="utf-8")

    acd.promote_api_clients(root, out)
    written = json.loads(out.read_text(encoding="utf-8"))["bindings"]
    assert len(written) == 2, "duplicates are not silently dropped either"
    assert [b["import_prefix"] for b in written] == [
        "com.example.tightened", "com.example.tightened",
    ], "an un-refreshed duplicate is a stale binding the tool still loads"


# --- the documented post-conditions, asserted rather than checklisted --------


def test_the_documented_post_conditions_hold(tmp_path):
    """`api-clients-json.md` lists these for a human to verify after promoting.

    A checklist a tool can run should not be a checklist a person runs.
    """
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [
        _candidate(),
        _candidate(target_repo="", maven_artifact="unresolvable-client"),
        _candidate(target_repo="commerce/warehouse-service-client",
                   maven_artifact="library-client"),
    ])
    out = tmp_path / "api-clients.json"
    original = {"target_repo": "other/thing", "maven_artifact": "thing-client"}
    out.write_text(json.dumps({
        "_comment": "NEVER COMMIT", "bindings": [dict(original)],
    }), encoding="utf-8")

    acd.promote_api_clients(root, out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    bindings = doc["bindings"]
    keys = [(b.get("target_repo"), b.get("maven_artifact")) for b in bindings]

    assert [b for b in bindings if not b.get("target_repo")] == [], "empty target_repo == 0"
    assert not [b for b in bindings if b.get("maven_artifact") == "library-client"], (
        "client-library targets == 0"
    )
    assert ("other/thing", "thing-client") in keys, "original bindings intact"
    assert len(keys) == len(set(keys)), "duplicate keys unchanged from before the merge"
    assert doc["_comment"] == "NEVER COMMIT", "handling notice preserved"


def test_promotion_is_still_idempotent(tmp_path):
    """Re-running must merge the same set without creating duplicates."""
    root = _metabase(tmp_path, _FLEET)
    _discovered(root, [_candidate()])
    out = tmp_path / "api-clients.json"

    acd.promote_api_clients(root, out)
    first = json.loads(out.read_text(encoding="utf-8"))["bindings"]
    acd.promote_api_clients(root, out)
    second = json.loads(out.read_text(encoding="utf-8"))["bindings"]
    assert first == second
