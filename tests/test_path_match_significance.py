"""Regression tests for OI-24 and OI-25: matching on segments that name nothing.

`OI-1` established the rule: confidence reflects how much *meaning* matched, and
a path reducing to no significant segments names a version or a layer, not a
route. Two ways that rule is still being broken:

* **The equality shortcut runs first.** ``if o == i: return "high"`` fires before
  the significance filter is computed, so two repos both exposing a bare ``/v1``
  match at `high` — the exact defect `OI-1` removed, surviving through the one
  path that never reaches the guard.
* **The filter's idea of "names nothing" is too narrow.** It drops version and
  layer segments, but not path *placeholders* (`/{id}`), which name nothing at
  all.
* **Operation verbs are treated as destinations.** `/search` says what you are
  doing, not what you are addressing. They cannot simply be dropped — `/query`
  really is the route of a query service — so a match resting *only* on verbs is
  weak evidence rather than none, and must be graded `low`.

Both surface as confident, wrong cross-repo edges, which is the failure class
this project has spent most of its effort removing.
"""

from __future__ import annotations

import pytest

from src2sink.graph_common import (
    _significant_segments,
    normalize_path_template,
    path_templates_match,
)


@pytest.mark.parametrize(
    "path",
    ["/v1", "/v2", "/api", "/rest", "/internal", "/public", "/service", "/services"],
)
def test_a_path_that_names_nothing_never_matches_itself(path):
    """Identical strings are not evidence when neither names a destination (OI-24).

    Two services each exposing a bare ``/v1`` have nothing to do with each other.
    The segment filter already knows this; the equality shortcut returned before
    asking it.
    """
    assert _significant_segments(normalize_path_template(path)) == ()
    assert path_templates_match(path, path) is None


@pytest.mark.parametrize(
    ("outbound", "inbound"),
    [
        ("/{id}", "/{name}"),
        ("/{id}", "/{id}"),
        ("/{tenant}", "/{org}"),
    ],
)
def test_a_bare_placeholder_names_no_destination(outbound, inbound):
    """`/{id}` matching `/{name}` is a coincidence of shape, not a route (OI-25)."""
    assert path_templates_match(outbound, inbound) is None


@pytest.mark.parametrize(
    "verb", ["create", "update", "delete", "get", "list", "search", "find", "save"]
)
def test_a_match_resting_only_on_an_operation_verb_is_weak(verb):
    """`/search` says what you are doing, not what you are addressing (OI-25).

    Deliberately `low` rather than dropped. `/query` genuinely is the route of a
    query service — the fixture in `test_sql_payload_out` uses `/v1/query` — so
    removing verbs outright would delete real routes. Two services both exposing
    `/search` is weak evidence, not no evidence, and `low` is what the confidence
    ladder already means by that.
    """
    assert path_templates_match(f"/{verb}", f"/{verb}") == "low"


def test_a_verb_qualified_by_a_resource_keeps_its_full_strength():
    """A verb alongside a resource is not a verb-only match, so it is not capped."""
    assert _significant_segments(normalize_path_template("/orders/create")) == (
        "orders",
        "create",
    )
    assert path_templates_match("/orders/create", "/orders/create") == "high"
    # Different operations on the same resource are different endpoints.
    assert path_templates_match("/orders/create", "/orders/delete") is None
    # And two resources never match merely by sharing a verb.
    assert path_templates_match("/orders/create", "/users/create") is None


def test_a_real_route_that_looks_like_a_verb_survives():
    """`/v1/query` is the query service's route, not an operation name (OI-25)."""
    assert _significant_segments(normalize_path_template("/v1/query")) == ("query",)
    assert path_templates_match("/v1/query", "/query") == "low"


def test_real_routes_still_match_exactly():
    """The fix must not cost the matches that carry meaning."""
    assert path_templates_match("/stock", "/stock") == "high"
    assert path_templates_match("/v1/stock", "/stock") == "medium"
    assert path_templates_match("/orders/{id}", "/orders/{ref}") == "high"
    assert path_templates_match("/stock/dispatch", "/stock") == "medium"


def test_placeholders_are_still_ignorable_within_a_real_route():
    """`/{}` carries no meaning, but the segments around it do."""
    assert _significant_segments(normalize_path_template("/orders/{id}/lines")) == (
        "orders",
        "lines",
    )
    assert path_templates_match("/orders/{id}/lines", "/orders/{ref}/lines") == "high"
