"""OI-33: the two discovery passes never agreed, so `both` was unreachable.

Found on the first completed discovery run over the fleet, once `OI-35` made the
pass finish at all:

```
discovery_method: dependency  125
discovery_method: call-site   101
discovery_method: both          0
```

Never one. `both` — a declared dependency *and* an observed call site
independently resolving to the same service — is the strongest signal the design
produces, and it could not occur.

The two passes named the same service differently. The demand-side pass uses
`repo_id(data)`, `group/name`. The supply-side pass used the resolver's output
directly, and the resolver returns *the directory that declares the coordinate* —
inside the repo for a multi-module build. An exact-string lookup cannot bridge
`group/repo` and `group/repo/some-client`.

The merge logic was correct. It was fed a key it could never match.

Truncating to two segments would be the wrong fix: `group/subgroup/repo` is a
valid GitLab path and this estate contains them (`OI-34`). Matching the longest
*known* repo id handles in-repo modules and nested repos alike, and needs neither
a depth rule nor `.git` — 65 of 746 repos in the observed fleet have no `.git`.

`test_both_is_reachable_end_to_end` is the one that matters most: `both` has never
occurred in a real run, so nothing has ever exercised that branch.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import src2sink.aggregators.api_client_discovery as acd

KNOWN = frozenset({"group/repo", "group/subgroup", "group/subgroup/nested"})


# --- the normalisation itself ------------------------------------------------


def test_module_path_resolves_to_owning_repo():
    """The case that made `both` unreachable for 79 of 117 supply-side targets."""
    assert acd._canonical_repo_id("group/repo/some-client", KNOWN) == "group/repo"


def test_a_deeper_module_path_still_resolves():
    """Multi-module builds nest more than one level."""
    assert acd._canonical_repo_id("group/repo/a/b/client", KNOWN) == "group/repo"


def test_a_nested_repo_id_is_not_truncated():
    """`group/subgroup/nested` is a repo, not a module inside `group/subgroup`.

    This is why a segment-count rule is wrong rather than merely crude: it would
    corrupt exactly the nested-subgroup projects `OI-34` is about.
    """
    assert acd._canonical_repo_id("group/subgroup/nested", KNOWN) == "group/subgroup/nested"


def test_the_longest_match_wins():
    """A path under a nested repo belongs to the nested repo, not its parent."""
    assert acd._canonical_repo_id(
        "group/subgroup/nested/client", KNOWN
    ) == "group/subgroup/nested"


def test_an_unknown_path_is_not_guessed():
    """A resolver failure must surface, not be rounded to a plausible repo."""
    assert acd._canonical_repo_id("other/thing/deep", KNOWN) is None


def test_an_empty_resolution_is_none():
    """The 8 empty targets in the field run came through here."""
    assert acd._canonical_repo_id("", KNOWN) is None


# --- end to end: the two passes must now meet --------------------------------


def _target(group: str, name: str, path: str) -> dict[str, Any]:
    """A service exposing an inbound route."""
    return {
        "schema_version": 2, "group": group, "name": name,
        "nodes": [{
            "family": "http-in", "kind": "source", "file": "src/Api.java",
            "line": 1, "detail": {"path": path, "method": "GET"},
        }],
        "edges": [], "dependencies_internal": [],
    }


def _consumer(group: str, name: str, *, dep: tuple[str, str], calls: str) -> dict[str, Any]:
    """A consumer that both declares the client dependency and calls the route.

    Both lines of evidence in one repo — which is precisely what `both` means and
    what no fixture has ever produced.
    """
    return {
        "schema_version": 2, "group": group, "name": name,
        "nodes": [{
            "family": "http-out", "kind": "sink", "file": "src/StockClient.java",
            "line": 3, "detail": {"path": calls, "raw": f'rest.get("{calls}")'},
        }],
        "edges": [],
        "dependencies_internal": [{"groupId": dep[0], "artifactId": dep[1]}],
    }


_RECORDS = [
    _target("commerce", "warehouse-service", "/stock/items"),
    _consumer(
        "fulfilment", "order-service",
        dep=("com.example", "warehouse-service-client"),
        calls="/stock/items",
    ),
]


def _run(tmp_path, resolve) -> list[dict[str, Any]]:
    """Run discovery and return the written candidates."""
    acd.discover_api_clients_from_records(tmp_path, _RECORDS, resolve)
    data = json.loads((tmp_path / acd.DISCOVERED_FILE).read_text(encoding="utf-8"))
    return data["candidates"]


def test_both_is_reachable_end_to_end(tmp_path):
    """The claim the whole issue rests on, and the branch nothing has exercised.

    The resolver returns a *module* path, as it does in the field. Supply-side
    and demand-side must still converge on one candidate for one service.
    """
    def resolve(_coord: str) -> str:
        return "commerce/warehouse-service/warehouse-client"   # the declaring module

    candidates = _run(tmp_path, resolve)
    methods = {c["target_repo"]: c["discovery_method"] for c in candidates}
    assert methods == {"commerce/warehouse-service": "both"}, (
        f"the two passes must agree on one candidate; got {methods}"
    )


def test_without_normalisation_the_passes_would_split(tmp_path):
    """The defect, reproduced — so the test above cannot pass vacuously.

    A resolver returning a module path that names no known repo cannot be
    normalised, so the supply-side candidate stays separate. That is exactly the
    shape the fleet run produced 79 times.
    """
    def resolve(_coord: str) -> str:
        return "unknown/elsewhere/warehouse-client"

    methods = sorted(c["discovery_method"] for c in _run(tmp_path, resolve))
    assert methods == ["call-site", "dependency"], (
        "an unresolvable target must not silently merge into an unrelated repo"
    )


def test_the_module_path_is_kept_as_evidence(tmp_path):
    """Strictly more information than the repo id, and `OI-34` may want it."""
    def resolve(_coord: str) -> str:
        return "commerce/warehouse-service/warehouse-client"

    candidate = _run(tmp_path, resolve)[0]
    assert candidate["evidence"]["declared_at"] == (
        "commerce/warehouse-service/warehouse-client"
    )


def test_normalisation_restores_the_paths_and_the_confidence(tmp_path):
    """A consequence not in the report, and the reason this is not cosmetic.

    `paths_by_repo` is keyed by repo id, so a module-path target matched no
    paths — and `_confidence` returns `high` only when paths are present. The bad
    key was capping these candidates at `medium` as a side effect.
    """
    def resolve(_coord: str) -> str:
        return "commerce/warehouse-service/warehouse-client"

    candidate = _run(tmp_path, resolve)[0]
    assert candidate["paths"] == ["/stock/items"], "paths must resolve with the repo id"
    assert candidate["confidence"] == "high"


def test_the_alias_names_the_service_not_the_build_module(tmp_path):
    """`service_aliases` is `target.split("/")[-1]`.

    Under the defect that yielded `warehouse-client` — the module — and alias
    matching is how a hand-rolled caller is recognised, so it misdirected the
    very pass the alias exists for.
    """
    def resolve(_coord: str) -> str:
        return "commerce/warehouse-service/warehouse-client"

    assert _run(tmp_path, resolve)[0]["service_aliases"] == ["warehouse-service"]


def test_an_unresolvable_coordinate_says_so(tmp_path):
    """The 8 empty targets arrived as candidates with nothing explaining them.

    Emitted rather than dropped — dropping loses information — but carrying a
    warning, because a candidate that would promote to an edge pointing at
    nothing must not look like an ordinary weak one (`OI-36`).
    """
    def resolve(_coord: str) -> None:
        return None

    supply = [c for c in _run(tmp_path, resolve) if c["discovery_method"] != "call-site"]
    assert supply, "the candidate is kept, not silently dropped"
    warnings = " ".join(w for c in supply for w in c.get("warnings", []))
    assert "did not resolve" in warnings
    assert supply[0]["confidence"] == "low"


# --- reviewer state must survive the key change ------------------------------


def test_a_reviewed_candidate_keeps_its_status_across_the_fix(tmp_path):
    """The migration hazard, and the one that would lose real human work.

    Candidates reviewed before this fix are stored under the *module* path. The
    new key is the repo id, so a naive lookup finds nothing and every accept or
    reject silently reverts to pending.
    """
    (tmp_path / acd.DISCOVERED_FILE).write_text(json.dumps({
        "candidates": [{
            "target_repo": "commerce/warehouse-service/warehouse-client",  # old shape
            "maven_artifact": "warehouse-service-client",
            "import_prefix": "com.example.tightened",   # a reviewer's edit
            "paths": ["/stock/items"],
            "payload_fields": ["sql"],
            "service_aliases": ["warehouse-service"],
            "class_patterns": [],
            "status": acd.STATUS_ACCEPTED,
        }],
    }), encoding="utf-8")

    def resolve(_coord: str) -> str:
        return "commerce/warehouse-service/warehouse-client"

    candidate = _run(tmp_path, resolve)[0]
    assert candidate["status"] == acd.STATUS_ACCEPTED, "a reviewer's decision was lost"
    assert candidate["import_prefix"] == "com.example.tightened", "their edit was lost"


@pytest.mark.parametrize("stored", ["commerce/warehouse-service", ""])
def test_other_stored_shapes_still_load(tmp_path, stored):
    """The migration must not break candidates already stored correctly."""
    (tmp_path / acd.DISCOVERED_FILE).write_text(json.dumps({
        "candidates": [{"target_repo": stored, "maven_artifact": "a-client"}],
    }), encoding="utf-8")
    loaded = acd._load_discovered(tmp_path / acd.DISCOVERED_FILE, KNOWN)
    assert acd._key(stored, "a-client") in loaded
