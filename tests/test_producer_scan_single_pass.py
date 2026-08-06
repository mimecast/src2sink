"""OI-30: the producer scan read the fleet once per binding.

Reported from the field: `payload-endpoint-producers` is the slowest part of a
scan apart from fleet-wide traces, at **70 minutes**.

`build_producer_indices` looped over bindings on the outside, and inside each
iteration walked every repo and read every source file. So a 34 GB checkout was
read from disk once per binding, and the only thing that differed between passes
was which regex ran over text already in memory. Instrumented before the fix:

```
     1 bindings ->  1x the fleet read
    10 bindings -> 10x the fleet read
```

Aggregation then called it **twice** — once for the catalogue and once for the
`OI-15` fleet index — so ten bindings meant reading the fleet twenty times.

This is a pure performance change, which makes the output tests the important
ones. The scan's dedup state is per binding, and collapsing the loops is exactly
the kind of change that would quietly share it — so most of what follows checks
that two bindings still see each other's repos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src2sink.aggregators.payload_producers as pp
import src2sink.known_api_clients as kac
from src2sink.known_api_clients import ApiClientBinding

_BINDINGS = (
    ApiClientBinding(
        target_repo="commerce/warehouse-service",
        maven_artifact="warehouse-service-client",
        import_prefix="com.example.commerce.warehouse.client",
        paths=("/stock",),
    ),
    ApiClientBinding(
        target_repo="pricing/price-index",
        maven_artifact="price-index-client",
        import_prefix="com.example.pricing.client",
        paths=("/price",),
    ),
)


@pytest.fixture
def bindings():
    """Configure two bindings and restore the global afterwards."""
    kac.configure_api_client_bindings(_BINDINGS)
    try:
        yield list(_BINDINGS)
    finally:
        kac.configure_api_client_bindings(())


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def fleet(tmp_path):
    """A checkout where each of two repos uses one client, and one uses both."""
    root = tmp_path / "repos"
    _write(
        root / "fulfilment/order-service/src/A.java",
        "import com.example.commerce.warehouse.client.StockClient;\nclass A { }\n",
    )
    _write(
        root / "fulfilment/order-service/pom.xml",
        "<project><dependencies><dependency>"
        "<artifactId>warehouse-service-client</artifactId>"
        "</dependency></dependencies></project>",
    )
    _write(
        root / "billing/invoice-service/src/B.java",
        "import com.example.pricing.client.PriceClient;\nclass B { }\n",
    )
    # Uses both clients — the case a shared dedup set would break.
    _write(
        root / "shipping/label-service/src/C.java",
        "import com.example.commerce.warehouse.client.StockClient;\n"
        "import com.example.pricing.client.PriceClient;\nclass C { }\n",
    )
    return root


def _by_binding(hits_per_binding, bindings):
    return {
        b.target_repo: sorted((h.source_repo, h.kind) for h in hits)
        for b, hits in zip(bindings, hits_per_binding, strict=True)
    }


def test_the_fleet_is_read_once_not_once_per_binding(fleet, bindings, monkeypatch):
    """The whole point. Reads must not scale with the number of bindings."""
    reads: list[Path] = []
    original = pp._read_capped
    monkeypatch.setattr(
        pp, "_read_capped", lambda p: (reads.append(p), original(p))[1],
    )

    pp.scan_repos_for_bindings(fleet, bindings)

    assert len(reads) == len(set(reads)), "no file may be read twice"
    scannable = [
        p for p in fleet.rglob("*")
        if p.suffix.lower() in pp._BINDING_SCAN_SUFFIXES
        and not any(s in p.parts for s in pp._BINDING_SCAN_SKIP)
    ]
    assert len(reads) == len(scannable)


def test_read_count_is_flat_as_bindings_are_added(fleet, monkeypatch):
    """The curve, not just one point — this is what took 70 minutes."""
    reads: list[Path] = []
    original = pp._read_capped
    # Patched once. Setting it inside the loop would wrap the previous wrapper,
    # so each pass would count every read n times and the test would "fail"
    # against a correct implementation.
    monkeypatch.setattr(
        pp, "_read_capped", lambda p: (reads.append(p), original(p))[1],
    )

    counts = []
    for n in (1, 2, 4, 8):
        many = [
            ApiClientBinding(
                target_repo=f"acme/svc-{i}", maven_artifact=f"art-{i}",
                import_prefix=f"com.acme.svc{i}", paths=("/x",),
            )
            for i in range(n)
        ]
        reads.clear()
        pp.scan_repos_for_bindings(fleet, many)
        counts.append(len(reads))

    assert len(set(counts)) == 1, (
        f"reads must not grow with binding count, got {counts}"
    )


def test_every_binding_still_finds_its_own_consumers(fleet, bindings):
    """The output claim. A faster scan that finds less is not a fix."""
    found = _by_binding(pp.scan_repos_for_bindings(fleet, bindings), bindings)

    assert found["commerce/warehouse-service"] == [
        ("fulfilment/order-service", "build-dep-scan"),
        ("fulfilment/order-service", "import-scan"),
        ("shipping/label-service", "import-scan"),
    ]
    assert found["pricing/price-index"] == [
        ("billing/invoice-service", "import-scan"),
        ("shipping/label-service", "import-scan"),
    ]


def test_one_repo_using_two_clients_is_found_by_both(fleet, bindings):
    """The subtle one, and the reason dedup state stayed per binding.

    `seen` keys on (repo, kind). Collapsing the per-binding loops into one walk
    invites sharing a single set — and then the first binding to match a repo
    silently suppresses every other binding's hit in it.
    """
    found = _by_binding(pp.scan_repos_for_bindings(fleet, bindings), bindings)
    for target in ("commerce/warehouse-service", "pricing/price-index"):
        assert ("shipping/label-service", "import-scan") in found[target], (
            f"{target} lost the repo that uses both clients"
        )


def test_a_binding_does_not_report_its_own_target_repo(tmp_path, bindings):
    """A service importing its own client is not a consumer of itself."""
    root = tmp_path / "repos"
    _write(
        root / "commerce/warehouse-service/src/Own.java",
        "import com.example.commerce.warehouse.client.StockClient;\nclass Own { }\n",
    )
    found = _by_binding(pp.scan_repos_for_bindings(root, bindings), bindings)
    assert found["commerce/warehouse-service"] == []


def test_a_build_dependency_is_still_detected(fleet, bindings):
    """The pom match runs per binding over one read of the file."""
    found = _by_binding(pp.scan_repos_for_bindings(fleet, bindings), bindings)
    assert ("fulfilment/order-service", "build-dep-scan") in found[
        "commerce/warehouse-service"
    ]
    assert ("fulfilment/order-service", "build-dep-scan") not in found[
        "pricing/price-index"
    ], "the pom names only one client"


def test_no_bindings_walks_nothing(fleet, monkeypatch):
    """An unconfigured fleet must not pay for a walk that can find nothing."""
    reads: list[Path] = []
    original = pp._read_capped
    monkeypatch.setattr(
        pp, "_read_capped", lambda p: (reads.append(p), original(p))[1],
    )
    assert pp.scan_repos_for_bindings(fleet, []) == []
    assert reads == []


def test_a_missing_checkout_is_not_an_error(tmp_path, bindings):
    """The scan is optional, so its absence degrades rather than raises."""
    assert pp.scan_repos_for_bindings(tmp_path / "nope", bindings) == [[], []]


def test_aggregation_builds_the_producer_indices_once(tmp_path, bindings, monkeypatch):
    """`OI-15`'s fleet index rebuilt them, doubling the slowest step of the run.

    Both consumers want the same fleet-wide value, and computing it is a full
    source scan — so it is computed once and handed on.
    """
    from src2sink.aggregators import graphs

    (tmp_path / "repos").mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 2, "group": "g", "name": "r",
        "nodes": [], "edges": [], "dependencies_internal": [],
    }
    d = tmp_path / "repos" / "g"
    d.mkdir(parents=True, exist_ok=True)
    (d / "r.json").write_text(json.dumps(record), encoding="utf-8")

    calls: list[int] = []
    original = pp.build_producer_indices

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(pp, "build_producer_indices", counted)
    monkeypatch.setattr(graphs, "build_producer_indices", counted)

    graphs.aggregate_graphs_v2(tmp_path, [d / "r.json"], phase3=False)

    assert len(calls) == 1, (
        f"the producer indices must be built once per run, not {len(calls)} times"
    )
