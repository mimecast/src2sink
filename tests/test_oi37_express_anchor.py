"""OI-37: any `.get("…")` in JavaScript was an inbound HTTP endpoint.

Found on the first completed fleet-wide trace batch. Inbound endpoints across a
746-repo, predominantly JVM estate:

```
express  10,245  66.5%     <-- the tell
jax-rs    4,671  30.3%
spring      456   3.0%
```

Resolving each Express node against its actual source line: **20 were routes.
10,225 were not.**

The pattern began at the dot — `\\.(get|post|put|delete|patch)\\(` — so it matched
a verb-named call on *any* receiver, in the one language where `.get(key)` is
ubiquitous for reasons unrelated to HTTP. Every sibling in `HTTP_IN_RX` is
anchored to something meaning "route declaration": Flask to `@app.route`,
FastAPI to `@router.`, Spring and JAX-RS to an annotation. This one had no
anchor, and reported `confidence: "high"` regardless.

What the 10,225 were: Angular reactive-form access (4,391), Cypress selectors
(1,104), header and cache lookups (646), and — the one that matters most — **605
outbound client calls recorded as doors into the service**.

`test_an_outbound_call_is_not_an_inbound_endpoint` is the one to read first. The
rest are noise a reviewer learns to skip; a direction-inverted edge is a wrong
answer that survives into everything built on the entry-point set.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import extract_from_file


def _http_in(source: str, language: str = "javascript", rel: str = "src/app.js"):
    return [
        n for n in extract_from_file(
            repo_id="g/r", rel_path=rel, language=language, source=source,
        )[0]
        if n.family == "http-in"
    ]


def _paths(source: str, **kw) -> list[str]:
    return [n.detail["path"] for n in _http_in(source, **kw)]


# --- the direction test, first ------------------------------------------------


@pytest.mark.parametrize("source", [
    "$http.post('/orders', body)",
    "httpClient.get('/stock/items')",
    "this.http.put('/stock/adjust', payload)",
])
def test_an_outbound_call_is_not_an_inbound_endpoint(source):
    """A call the service *makes* is not a door into it.

    605 of these in the observed fleet. Not merely a spurious node — a wrong
    edge, pointing the wrong way, feeding the set reachability is computed from.
    """
    assert _paths(source) == []


# --- what a route actually looks like ----------------------------------------


@pytest.mark.parametrize(("source", "want"), [
    ("router.get('/stock', handler)", ["/stock"]),
    ("app.post('/stock/adjust', handler)", ["/stock/adjust"]),
    ("server.put('/x', h)", ["/x"]),
    ("api.delete('/items/:id', h)", ["/items/:id"]),
    ("fastify.patch('/y', h)", ["/y"]),
    ("stockRouter.get('/items', h)", ["/items"]),
    ("_router.post('/z', h)", ["/z"]),
    ("app . get ( '/spaced' , h )", ["/spaced"]),
])
def test_a_real_route_is_still_detected(source, want):
    """The 20 that were genuine must survive; anchoring must not cost recall."""
    assert _paths(source) == want


# --- the populations that were wrong ------------------------------------------


@pytest.mark.parametrize(("what", "source"), [
    ("angular reactive form", "this.stockAdjustmentForm.get('quantity').setValue(0)"),
    ("cypress selector", "cy.get('[data-test=\"stock-total\"]')"),
    ("template cache write", '$templateCache.put("template/banner/banner.html", tpl)'),
    ("header lookup", "req.headers.get('x-request-id')"),
    ("map lookup", "cache.get('some-key')"),
    ("params lookup", "route.params.get('id')"),
    ("form data", "formData.get('file')"),
])
def test_the_false_populations_are_gone(what, source):
    """Each row of the report's breakdown, as its own case."""
    assert _paths(source) == [], f"{what} must not be an inbound endpoint"


def test_the_worked_example_from_the_report():
    """A vendored framework's template cache, recorded as an inbound PUT.

    The clearest single case: a third-party library the team does not own,
    reported as a `PUT` endpoint at high confidence, from a cache write.
    """
    source = '$templateCache.put("template/banner/banner.html", "<div></div>");'
    assert _http_in(source, rel="vendor/framework.js") == []


# --- confidence must reflect the anchor ---------------------------------------


def test_an_anchored_match_earns_high():
    """A required router receiver is a real claim."""
    assert _http_in("router.get('/stock', h)")[0].confidence == "high"


def test_an_unanchored_pattern_cannot_claim_high():
    """Confidence was one hardcoded literal for every language.

    An annotation-anchored Spring route and a bare pattern were indistinguishable
    downstream. Gin still relies on Go's uppercase-verb convention rather than an
    anchor, so it must not claim what an annotation earns — and the next
    unanchored pattern has to declare itself rather than inherit `high`.
    """
    from src2sink.extractors.patterns import http_in_confidence

    assert http_in_confidence("gin") == "medium"
    assert http_in_confidence("spring") == "high"
    assert http_in_confidence("jax-rs") == "high"

    nodes = _http_in('r.GET("/x", h)', language="go", rel="main.go")
    assert nodes and nodes[0].confidence == "medium"


# --- auditability -------------------------------------------------------------


def test_raw_now_contains_the_receiver():
    """`raw` began at the dot, so all 10,245 looked identical in the output.

    The distinction was recoverable only by re-reading the original source, which
    is how the fleet numbers were obtained at all. Anchoring the pattern fixes
    this for free — the receiver is part of the match.
    """
    raw = _http_in("router.get('/stock', h)")[0].detail["raw"]
    assert raw.startswith("router."), f"a reviewer cannot audit {raw!r}"


# --- the ratio that was the real symptom --------------------------------------


def test_express_does_not_dominate_a_mixed_file():
    """The tell was a ratio, long before anyone read a node.

    Two thirds of a JVM estate's inbound endpoints attributed to Express is not a
    finding, it is a bug report. A file mixing one route with ordinary JavaScript
    should yield one endpoint, not seven.
    """
    source = """
    const app = express();
    app.get('/health', (req, res) => res.send('ok'));

    const value = form.get('quantity');
    cy.get('[data-test="x"]');
    cache.get('k');
    headers.get('h');
    $http.post('/orders', body);
    $templateCache.put('tpl/a.html', s);
    """
    assert _paths(source) == ["/health"]
