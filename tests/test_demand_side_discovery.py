"""Regression tests for OI-4 — discovery mines only one direction.

`api_client_discovery` works **supply-side**: a consumer declares a dependency on
an artifact whose id looks like a client library, that coordinate resolves to the
publishing repo, and the target's `http-in` nodes supply candidate paths. Two
consequences:

* `class_patterns` is hardcoded `[]` and never proposed — yet it is the mechanism
  that catches call sites carrying no URL, so discovery cannot generate the field
  that most needs generating;
* a caller that hand-rolls HTTP has no `*-client` dependency to mine, so it is
  invisible to supply-side discovery **by construction**. No amount of dependency
  parsing finds a dependency that does not exist.

The demand-side pass mines the opposite direction: a call site that resolves to a
known service, in a repo that declares no client library for it. It runs *after*
the supply-side pass so it can do a keyed lookup and enrich rather than merge.

Two safeguards are load-bearing and have their own tests. A proposed
`class_pattern` runs in an unguarded, language-agnostic substring tier, so a
generic one manufactures phantom edges fleet-wide. And demand-side evidence must
exclude call sites whose target a *binding* stamped, or every run re-ingests its
own output as fresh evidence and confidence inflates.

Names follow the sanitised placeholder set used across the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

from src2sink.aggregators import api_client_discovery as acd


def _repo(group: str, name: str, *, nodes=None, deps=None) -> dict:
    """Build a v2 repo record."""
    return {
        "schema_version": 2,
        "group": group,
        "name": name,
        "nodes": nodes or [],
        "edges": [],
        "dependencies_internal": deps or [],
    }


def _http_out(file: str, line: int, path: str, **detail) -> dict:
    """An outbound call node with a resolved path."""
    return {
        "family": "http-out", "kind": "sink", "file": file, "line": line,
        "detail": {"path": path, "raw": f"client.post({path!r}, body)", **detail},
    }


def _provider() -> dict:
    """The target service, exposing one inbound route."""
    return _repo("commerce", "warehouse-service", nodes=[
        {"family": "http-in", "kind": "source", "file": "StockResource.java", "line": 20,
         "detail": {"method": "POST", "path": "/stock"}},
    ])


def _discovered(tmp_path: Path) -> dict:
    """Read the candidate file written by a discovery run."""
    return json.loads((tmp_path / acd.DISCOVERED_FILE).read_text(encoding="utf-8"))


def _run(tmp_path: Path, records: list[dict]) -> dict:
    """Run discovery over in-memory records and return the candidate file."""
    acd.discover_api_clients_from_records(tmp_path, records, resolve=lambda coord: None)
    return _discovered(tmp_path)


# --------------------------------------------------------------------------
# The capability gap: a hand-rolled caller is invisible supply-side
# --------------------------------------------------------------------------

def test_hand_rolled_caller_becomes_a_candidate(tmp_path) -> None:
    """OI-4: a repo with no client dependency still calls the service.

    Supply-side discovery cannot reach this repo by construction — there is no
    `*-client` artifact to mine — so before the demand-side pass it contributed
    nothing at all.
    """
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/stock"),
    ])
    out = _run(tmp_path, [consumer, _provider()])
    targets = {c["target_repo"] for c in out["candidates"]}
    assert "commerce/warehouse-service" in targets, out["candidates"]


def test_hand_rolled_candidate_proposes_the_enclosing_class(tmp_path) -> None:
    """`class_patterns` is the field supply-side discovery can never fill.

    It is the mechanism that catches call sites carrying no URL, so a discovery
    pass that always emits `[]` cannot generate the one field a reviewer most
    needs.
    """
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/stock"),
    ])
    out = _run(tmp_path, [consumer, _provider()])
    cand = next(c for c in out["candidates"] if c["target_repo"] == "commerce/warehouse-service")
    assert "StockRequestProcessor" in cand["class_patterns"]


def test_hand_rolled_candidate_is_flagged_as_call_site_only(tmp_path) -> None:
    """There is no artifact, so the entry must not pretend otherwise."""
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/stock"),
    ])
    out = _run(tmp_path, [consumer, _provider()])
    cand = next(c for c in out["candidates"] if c["target_repo"] == "commerce/warehouse-service")
    assert cand["maven_artifact"] == ""
    assert cand["import_prefix"] == ""
    assert cand["discovery_method"] == "call-site"
    assert cand["status"] == acd.STATUS_PENDING


# --------------------------------------------------------------------------
# Agreement between the two directions is stronger than either alone
# --------------------------------------------------------------------------

def test_both_directions_agreeing_is_recorded_as_such(tmp_path) -> None:
    """A declared dependency *and* an observed call site are independent evidence."""
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/stock"),
    ], deps=[{"groupId": "com.example.commerce.warehouse",
              "artifactId": "warehouse-service-client", "version": "3.0.2", "kind": "internal"}])
    acd.discover_api_clients_from_records(
        tmp_path, [consumer, _provider()],
        resolve=lambda coord: "commerce/warehouse-service",
    )
    cands = _discovered(tmp_path)["candidates"]
    assert len(cands) == 1, f"the two directions must enrich one candidate, not two: {cands}"
    assert cands[0]["discovery_method"] == "both"
    assert "StockRequestProcessor" in cands[0]["class_patterns"], (
        "the demand-side pass must enrich the supply-side candidate, not replace it"
    )
    assert cands[0]["maven_artifact"] == "warehouse-service-client", (
        "supply-side fields must survive enrichment"
    )


# --------------------------------------------------------------------------
# Safeguard 1: a proposed class_pattern runs unguarded, fleet-wide
# --------------------------------------------------------------------------

def test_a_class_pattern_seen_across_many_repos_is_flagged(tmp_path) -> None:
    """`ApiClient` in five repos would manufacture phantom edges everywhere.

    Binding class patterns run in an unguarded, language-agnostic tier and are
    matched as a plain substring, so a generic proposal is not merely noisy — it
    invents cross-repo hops that do not exist.
    """
    consumers = [
        _repo("fulfilment", f"service-{i}", nodes=[_http_out("src/ApiClient.java", 10, "/stock")])
        for i in range(5)
    ]
    out = _run(tmp_path, [*consumers, _provider()])
    cand = next(c for c in out["candidates"] if c["target_repo"] == "commerce/warehouse-service")
    assert cand.get("warnings"), "a fleet-wide class name must be flagged"
    assert any("ApiClient" in w for w in cand["warnings"])
    assert any("5" in w for w in cand["warnings"]), "the warning should name the count"


def test_a_distinctive_class_pattern_is_not_flagged(tmp_path) -> None:
    """The safeguard must not warn on the normal case."""
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/stock"),
    ])
    out = _run(tmp_path, [consumer, _provider()])
    cand = next(c for c in out["candidates"] if c["target_repo"] == "commerce/warehouse-service")
    assert not cand.get("warnings"), cand.get("warnings")


# --------------------------------------------------------------------------
# Safeguard 2: never re-ingest your own output as evidence
# --------------------------------------------------------------------------

def test_binding_stamped_call_sites_are_not_demand_side_evidence(tmp_path) -> None:
    """A hop a binding already created must not be evidence for that binding.

    Demand-side discovery resolves targets against routes and aliases that
    promoted bindings influence. Counting those hops as fresh observation means
    confidence climbs on every run for no new reason.
    """
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out(
            "src/StockRequestProcessor.java", 42, "/stock",
            target_repo="commerce/warehouse-service",
            target_repo_evidence="api-client class warehouse-service-client",
            target_repo_confidence="high",
        ),
    ])
    out = _run(tmp_path, [consumer, _provider()])
    assert out["candidates"] == [], (
        "a binding-stamped hop is the tool's own output, not an observation"
    )


def test_repeated_runs_are_idempotent(tmp_path) -> None:
    """Running twice must not strengthen a candidate — the anti-inflation property."""
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/stock"),
    ])
    first = _run(tmp_path, [consumer, _provider()])
    second = _run(tmp_path, [consumer, _provider()])
    assert first == second


# --------------------------------------------------------------------------
# Reviewer decisions survive regeneration
# --------------------------------------------------------------------------

def test_reviewer_edits_survive_demand_side_regeneration(tmp_path) -> None:
    """An accepted candidate keeps its hand-tuned class_patterns."""
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/stock"),
    ])
    _run(tmp_path, [consumer, _provider()])

    path = tmp_path / acd.DISCOVERED_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["candidates"][0]["status"] = acd.STATUS_ACCEPTED
    data["candidates"][0]["class_patterns"] = ["StockRequestProcessor", "StockDispatcher"]
    path.write_text(json.dumps(data), encoding="utf-8")

    out = _run(tmp_path, [consumer, _provider()])
    cand = out["candidates"][0]
    assert cand["status"] == acd.STATUS_ACCEPTED
    assert cand["class_patterns"] == ["StockRequestProcessor", "StockDispatcher"]


# --------------------------------------------------------------------------
# Precision — the pass must not invent targets
# --------------------------------------------------------------------------

def test_a_call_site_resolving_to_nothing_yields_no_candidate(tmp_path) -> None:
    """An outbound call to an unknown route is not evidence of anything."""
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        _http_out("src/StockRequestProcessor.java", 42, "/unrelated-route"),
    ])
    assert _run(tmp_path, [consumer, _provider()])["candidates"] == []


def test_a_repo_calling_itself_yields_no_candidate(tmp_path) -> None:
    """A self-edge is not a cross-repo client relationship."""
    provider = _provider()
    provider["nodes"].append(_http_out("src/StockResource.java", 30, "/stock"))
    assert _run(tmp_path, [provider])["candidates"] == []


def test_accepting_a_candidate_removes_unmatched_call_sites(tmp_path) -> None:
    """The regression metric OI-4 says the discovery pass currently lacks.

    `service-call-unmatched.jsonl` is both the input to demand-side discovery and
    the natural measure of its success: every accepted candidate should remove
    entries from it. That gives the pass something to be judged on — unmatched
    call sites trending to zero — rather than only a count of candidates
    produced, which rewards proposing more of them.
    """
    from src2sink import known_api_clients as kac
    from src2sink.aggregators.service_call_collect import collect_service_edges
    from src2sink.extractors.http_out import configure_http_out_client_patterns
    from src2sink.known_api_clients import ApiClientBinding

    # A call site with no path and no host: invisible until a binding names it.
    consumer = _repo("fulfilment", "fulfilment-commons", nodes=[
        {"family": "http-out", "kind": "sink", "file": "src/StockRequestProcessor.java",
         "line": 42, "detail": {"raw": "client.post(request)"}},
    ])
    records = [consumer, _provider()]

    _edges, before = collect_service_edges(records)
    assert before, "fixture must start with an unmatched call site"

    # Accepting the demand-side candidate is what a reviewer does: the binding
    # stamps the target onto the call site.
    consumer["nodes"][0]["detail"].update(
        target_repo="commerce/warehouse-service",
        target_repo_evidence="api-client class warehouse-service-client",
        target_repo_confidence="high",
    )
    kac.configure_api_client_bindings((ApiClientBinding(
        target_repo="commerce/warehouse-service",
        maven_artifact="warehouse-service-client",
        import_prefix="com.example.commerce.warehouse.client",
        paths=("/stock",),
        payload_fields=("sql",),
        service_aliases=("warehouse-service",),
        class_patterns=("StockRequestProcessor",),
    ),))
    configure_http_out_client_patterns(())
    try:
        _edges, after = collect_service_edges(records)
    finally:
        kac.configure_api_client_bindings(())

    assert len(after) < len(before), (
        f"accepting a candidate must reduce unmatched call sites: "
        f"{len(before)} -> {len(after)}"
    )
