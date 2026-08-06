"""OI-33: api-client discovery rescanned the whole fleet per class.

Reported from the field: *"discovery is a quadratic scan."* It was, and in the
worst shape rather worse than quadratic.

`_apply_demand_side` iterated targets, and each target's candidate classes, and
asked `_repos_containing_class(records, cls)` about every one — and that function
walked every node of every record. So the cost was
``targets x classes x records x nodes``. On a synthetic fleet where all four
scale together, node visits grew about **15x for every doubling** of the
repository count:

```
 repos   nodes    node visits
     8      20            320
    16      72          4,608   14.4x
    32     272         69,632   15.1x
    48     600        345,600
```

Nothing about the answer needed that. "Which repos contain a file called
`StockClient`" is corpus-wide and target-independent, so it is asked once for the
whole fleet and answered from an index.

The count test below is the one that would catch a regression, but the
equivalence test is the one that makes the change safe: an index that is faster
and answers differently is not a fix.
"""

from __future__ import annotations

from typing import Any

import pytest

import src2sink.aggregators.api_client_discovery as acd


def _record(group: str, name: str, files: list[str]) -> dict[str, Any]:
    """A repo record whose nodes sit in the named files."""
    return {
        "schema_version": 2, "group": group, "name": name,
        "nodes": [
            {"family": "http-out", "kind": "sink", "file": f, "line": 1,
             "detail": {"path": "/x", "raw": "call"}}
            for f in files
        ],
        "edges": [], "dependencies_internal": [],
    }


def _reference_scan(records: list[dict[str, Any]], class_name: str) -> set[str]:
    """The implementation this replaced, kept as an oracle.

    Deliberately the *old* code rather than a restatement of the new one: the
    claim is that the index answers identically, and a test written against the
    new implementation could not detect that it does not.
    """
    from pathlib import Path

    hits: set[str] = set()
    for data in records:
        rid = f"{data['group']}/{data['name']}"
        for node in data.get("nodes", []):
            if Path(str(node.get("file", ""))).stem == class_name:
                hits.add(rid)
                break
    return hits


_FLEET = [
    _record("app", "one", ["src/StockClient.java", "src/Helper.java"]),
    _record("app", "two", ["src/StockClient.kt", "src/Other.java"]),
    _record("svc", "three", ["src/Unrelated.java"]),
    _record("svc", "four", ["src/Helper.java"]),
]


@pytest.mark.parametrize(
    "class_name",
    ["StockClient", "Helper", "Other", "Unrelated", "NotPresentAnywhere", ""],
)
def test_the_index_answers_exactly_as_the_scan_did(class_name):
    """A faster answer that differs is not a fix."""
    index = acd._repos_by_class(_FLEET)
    assert index.get(class_name, set()) == _reference_scan(_FLEET, class_name)


def test_a_class_in_several_repos_lists_them_all():
    """The count is what decides the 'too generic to be safe' warning."""
    index = acd._repos_by_class(_FLEET)
    assert index["StockClient"] == {"app/one", "app/two"}
    assert index["Helper"] == {"app/one", "svc/four"}


def test_a_repo_is_listed_once_however_many_files_match():
    """The old scan `break`s after the first hit; a set must behave the same."""
    fleet = [_record("app", "one", ["src/Thing.java", "other/Thing.java"])]
    assert acd._repos_by_class(fleet)["Thing"] == {"app/one"}


def test_the_fleet_is_walked_once_not_once_per_class(monkeypatch):
    """The fix, asserted structurally.

    A timing assertion would be flaky; the node-visit count is exact. One pass
    means visits equal the fleet's node count however many targets and classes
    the demand-side pass turns up.
    """
    visits = [0]
    original = acd._repos_by_class

    def counted(records):
        visits[0] += sum(len(r.get("nodes", [])) for r in records)
        return original(records)

    monkeypatch.setattr(acd, "_repos_by_class", counted)

    records = _demand_side_fleet(6)
    total_nodes = sum(len(r["nodes"]) for r in records)
    acd._apply_demand_side({}, records)

    assert visits[0] == total_nodes, (
        f"expected one pass ({total_nodes} node visits), got {visits[0]}"
    )


def _demand_side_fleet(n: int) -> list[dict[str, Any]]:
    """``n`` services and ``n`` consumers calling all of them.

    Shaped so targets, classes, records and nodes all grow together — the case
    where the four nested factors compounded.
    """
    records: list[dict[str, Any]] = []
    for t in range(n):
        records.append({
            "schema_version": 2, "group": "svc", "name": f"target{t}",
            "nodes": [{
                "family": "http-in", "kind": "source", "file": f"src/Api{t}.java",
                "line": 1, "detail": {"path": f"/thing{t}/items", "method": "GET"},
            }],
            "edges": [], "dependencies_internal": [],
        })
    for c in range(n):
        records.append({
            "schema_version": 2, "group": "app", "name": f"consumer{c}",
            "nodes": [{
                "family": "http-out", "kind": "sink",
                "file": f"src/Client{c}_{t}.java", "line": 1,
                "detail": {"path": f"/thing{t}/items", "raw": "call"},
            } for t in range(n)],
            "edges": [], "dependencies_internal": [],
        })
    return records


def test_cost_does_not_grow_with_target_count(monkeypatch):
    """The curve, not one point. This is what "quadratic" meant.

    Node visits must track the fleet's *size*, not the product of its targets
    and classes — so the ratio between two fleet sizes must match their node
    ratio, not its square.
    """
    visits = [0]
    original = acd._repos_by_class
    monkeypatch.setattr(acd, "_repos_by_class", lambda r: (
        visits.__setitem__(0, visits[0] + sum(len(x.get("nodes", [])) for x in r)),
        original(r),
    )[1])

    measured = []
    for n in (4, 8):
        records = _demand_side_fleet(n)
        visits[0] = 0
        acd._apply_demand_side({}, records)
        measured.append((sum(len(r["nodes"]) for r in records), visits[0]))

    (nodes_small, visits_small), (nodes_big, visits_big) = measured
    assert visits_small == nodes_small
    assert visits_big == nodes_big
    # Linear in fleet size. Under the old code this ratio was the square.
    assert visits_big / visits_small == pytest.approx(nodes_big / nodes_small)


def test_an_over_generic_class_still_warns():
    """The warning this scan exists to produce must survive the rewrite.

    A class pattern appearing in too many repos is unsafe to accept, and that
    judgement is the only reason the corpus is consulted at all.
    """
    n = acd.MAX_PATTERN_REPOS + 2
    records = [
        _record("svc", "target", ["src/Api.java"]),
    ]
    records[0]["nodes"] = [{
        "family": "http-in", "kind": "source", "file": "src/Api.java", "line": 1,
        "detail": {"path": "/things/items", "method": "GET"},
    }]
    for c in range(n):
        records.append({
            "schema_version": 2, "group": "app", "name": f"consumer{c}",
            "nodes": [{
                "family": "http-out", "kind": "sink", "file": "src/Shared.java",
                "line": 1, "detail": {"path": "/things/items", "raw": "call"},
            }],
            "edges": [], "dependencies_internal": [],
        })

    cands: dict[str, dict[str, Any]] = {}
    acd._apply_demand_side(cands, records)

    warnings = [w for c in cands.values() for w in c.get("warnings", [])]
    assert any("too" in w and "generic" in w for w in warnings), (
        f"the over-generic warning must survive the index rewrite; got {warnings}"
    )
