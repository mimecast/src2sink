"""OI-40: the binding named the client library instead of the service it calls.

Found reviewing the 191 candidates from the first trustworthy discovery run,
against the 99 hand-authored bindings as ground truth.

**42 of 191 named a client library as `target_repo`. 0 of 99 hand-authored ones
do.** A binding's contract is that `target_repo` is *the service that receives
the calls* and `maven_artifact` is the library; these invert it.

Nothing about the record looks wrong — the repo id is real, the artifact is real,
the consumers are real, `confidence` is unaffected. Only the semantics are wrong,
which is why it survives every existing check and why it is `P1`: these
candidates **pass review by looking correct**.

This is `OI-33`'s fix behaving exactly as designed, meeting a case its design did
not consider. `resolve()` names the repo that *declares* the artifact;
`_canonical_repo_id` maps that to the repo owning it. Both correct. But when the
client library is published from its own repository, the repo owning the
declaration **is** the library. `OI-33` fixed the *shape* of the identity; this
is the *referent*, and no path normalisation reaches it.

The two tests that matter most are the last two: a rule that rewrites targets can
do more damage than the defect if it fires on a real service, and one that drops
endpointless targets would turn `OI-17`-class blindness into an invisible filter.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import src2sink.aggregators.api_client_discovery as acd

# repo -> the inbound endpoint paths it declares. A service has some; a client
# library does not, and that is the whole discriminator.
FLEET: dict[str, set[str]] = {
    "commerce/warehouse-service": {"/stock", "/stock/adjust"},
    "commerce/warehouse-service-client": set(),
}


# --- the correction -----------------------------------------------------------


def test_a_client_repo_resolves_to_the_service_it_fronts():
    """The 42, in one assertion."""
    assert acd._service_for_client_repo(
        "commerce/warehouse-service-client", FLEET,
    ) == "commerce/warehouse-service"


def test_the_service_suffix_variant_resolves():
    """`warehouse-client` fronting `warehouse-service` — the stem needs the suffix."""
    fleet = {"commerce/warehouse-service": {"/x"}, "commerce/warehouse-client": set()}
    assert acd._service_for_client_repo(
        "commerce/warehouse-client", fleet,
    ) == "commerce/warehouse-service"


@pytest.mark.parametrize("suffix", ["-client", "-clients", "-sdk", "_client", "_sdk"])
def test_the_conventional_suffixes_are_recognised(suffix):
    """The name supplies the stem to search for, after the endpoint test."""
    fleet = {"acme/thing-service": {"/x"}, f"acme/thing{suffix}": set()}
    assert acd._service_for_client_repo(f"acme/thing{suffix}", fleet) == "acme/thing-service"


# --- the two that matter most -------------------------------------------------


def test_a_real_service_is_never_rewritten():
    """A rule that rewrites targets can do more damage than the defect.

    A service is left alone whatever it is called — the test is that it receives
    calls, not what its name looks like. A repo named `*-client` that genuinely
    declares endpoints is a service.
    """
    assert acd._service_for_client_repo("commerce/warehouse-service", FLEET) is None

    # A library-shaped name that nonetheless serves traffic.
    odd = {"acme/payments-client": {"/pay"}, "acme/payments-service": {"/x"}}
    assert acd._service_for_client_repo("acme/payments-client", odd) is None


def test_an_absent_service_is_not_guessed():
    """31 of the 42 had no sibling, because the service is not in the fleet.

    Inventing a target for those would manufacture exactly the broken edges
    `OI-33` was about.
    """
    assert acd._service_for_client_repo(
        "commerce/warehouse-client", {"other/thing": {"/x"}},
    ) is None


def test_an_ambiguous_stem_is_not_guessed():
    """Two candidates is not an answer; it is two answers."""
    fleet = {
        "a/thing-service": {"/x"},
        "b/thing-service": {"/y"},
        "a/thing-client": set(),
    }
    assert acd._service_for_client_repo("a/thing-client", fleet) is None


# --- end to end, through the discovery pass ------------------------------------


def _records(*, target_endpoints: list[str], client_repo: str) -> list[dict[str, Any]]:
    """A service, its client library repo, and a consumer depending on the client."""
    group, name = client_repo.split("/", 1)
    return [
        {
            "schema_version": 2, "group": "commerce", "name": "warehouse-service",
            "nodes": [
                {"family": "http-in", "kind": "source", "file": "src/Api.java",
                 "line": 1, "detail": {"path": p, "method": "GET"}}
                for p in target_endpoints
            ],
            "edges": [], "dependencies_internal": [],
        },
        {
            "schema_version": 2, "group": group, "name": name,
            "nodes": [], "edges": [], "dependencies_internal": [],
        },
        {
            "schema_version": 2, "group": "fulfilment", "name": "order-service",
            "nodes": [], "edges": [],
            "dependencies_internal": [
                {"groupId": "com.example", "artifactId": "warehouse-service-client"},
            ],
        },
    ]


def _run(tmp_path, records, resolve) -> list[dict[str, Any]]:
    acd.discover_api_clients_from_records(tmp_path, records, resolve)
    return json.loads(
        (tmp_path / acd.DISCOVERED_FILE).read_text(encoding="utf-8")
    )["candidates"]


def test_the_candidate_names_the_service_not_the_library(tmp_path):
    """The defect and its fix, at the level the review found it."""
    records = _records(
        target_endpoints=["/stock"], client_repo="commerce/warehouse-service-client",
    )
    candidates = _run(
        tmp_path, records,
        lambda _c: "commerce/warehouse-service-client",   # the library's own repo
    )
    supply = [c for c in candidates if c["maven_artifact"]]
    assert supply, "the dependency-side candidate must exist"
    assert supply[0]["target_repo"] == "commerce/warehouse-service"


def test_the_substitution_is_visible_to_a_reviewer(tmp_path):
    """A rewrite a reviewer cannot see is a rewrite they cannot check."""
    records = _records(
        target_endpoints=["/stock"], client_repo="commerce/warehouse-service-client",
    )
    supply = [
        c for c in _run(tmp_path, records, lambda _c: "commerce/warehouse-service-client")
        if c["maven_artifact"]
    ][0]
    assert supply["evidence"]["client_repo"] == "commerce/warehouse-service-client"
    assert any("corrected from" in w for w in supply.get("warnings", []))


def test_an_endpointless_target_is_flagged_not_dropped(tmp_path):
    """The one that would do real harm if it were a filter.

    Zero inbound endpoints is *also* what a detection gap looks like — `OI-17`
    left a whole language half of a fleet returning none. The two causes are
    indistinguishable from the outside, so a candidate must survive with a
    warning rather than vanish. Dropping it would turn our own blind spot into an
    invisible data-quality filter, which is `OI-36` done to ourselves.
    """
    records = _records(
        target_endpoints=[],                       # the service detected nothing
        client_repo="commerce/warehouse-service-client",
    )
    candidates = _run(tmp_path, records, lambda _c: "commerce/warehouse-service")
    supply = [c for c in candidates if c["maven_artifact"]]

    assert supply, "the candidate must survive"
    warnings = " ".join(w for c in supply for w in c.get("warnings", []))
    assert "no inbound endpoints" in warnings
    assert "may be a client library" in warnings and "not detected" in warnings, (
        "the warning must name both causes; picking one would assert something "
        "the tool cannot know"
    )


def test_a_normal_candidate_is_not_annotated(tmp_path):
    """The common case stays quiet, or the warnings stop being read."""
    records = _records(
        target_endpoints=["/stock"], client_repo="commerce/warehouse-service-client",
    )
    supply = [
        c for c in _run(tmp_path, records, lambda _c: "commerce/warehouse-service")
        if c["maven_artifact"]
    ][0]
    assert not supply.get("warnings")
    assert supply["evidence"]["client_repo"] == ""
