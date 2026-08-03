"""Regression tests for OI-2 — a fully custom HTTP wrapper must not be invisible.

The broad receiver pattern `\\w*[Cc]lient.post(` is only trusted when the file
also shows HTTP evidence, and that guard's vocabulary listed Spring and JDK types
only. A repo calling a service through an in-house abstraction names no such
type — the HTTP concern lives in another module entirely — so the call site
produced no `http-out` node and the caller was invisible to the graphs.

The guard itself is right: without it, `\\w*[Cc]lient.post(` matches any
Mapping-like helper fleet-wide. What was wrong is that its evidence vocabulary
only recognised *direct* use of a known HTTP library.

Note the issue document proposes satisfying the guard from `ctx.nodes` when a
`path-constant` node is present, on the grounds that `extract_path_constants`
runs first. It does not — `extractors/unified.py` runs `extract_http_outbound`
*before* it, so `ctx.nodes` holds no path constants at guard time and that fix
would be a no-op. The evidence is therefore derived from the source text, which
is also order-independent by construction; `test_guard_evidence_does_not_depend_on_pass_order`
pins that.

Fixture names follow the sanitised placeholder set used across the suite.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.file_context import FileExtractionContext
from src2sink.extractors.regex_extractors import extract_http_outbound
from src2sink.extractors.unified import extract_from_file


def _http_out(source: str, *, language: str = "java", rel_path: str = "src/Sample.java"):
    """Return the `http-out` nodes from the full per-file pipeline."""
    nodes, _edges = extract_from_file(
        repo_id="test/sample", rel_path=rel_path, language=language, source=source,
    )
    return [n for n in nodes if n.family == "http-out"]


# --------------------------------------------------------------------------
# The reported symptom
# --------------------------------------------------------------------------

JAVA_CUSTOM_WRAPPER = """
package com.example.fulfilment.commons;

import com.example.fulfilment.commons.transport.ApiClient;

public class StockRequestProcessor {
    private static final String STOCK_SUBMIT_URL = "/v1/stock";
    private final ApiClient client;

    public StockSubmitResponse submit(StockRequest request) {
        return client.post(STOCK_SUBMIT_URL, request, StockSubmitResponse.class);
    }
}
"""

PYTHON_CUSTOM_WRAPPER = """
STOCK_SUBMIT_URL = "/v1/stock"


class StockRequestProcessor:
    def __init__(self, client):
        self._client = client

    def submit(self, request):
        return self._client.post(STOCK_SUBMIT_URL, json=request)
"""


def test_custom_wrapper_with_route_constant_yields_http_out() -> None:
    """OI-2: a route constant plus `client.post(...)` is HTTP, with no library named.

    This file mentions no Spring or JDK HTTP type at all — the wrapper hides
    them — so the 1.1.0 guard rejected it and the caller vanished.
    """
    nodes = _http_out(JAVA_CUSTOM_WRAPPER, rel_path="src/StockRequestProcessor.java")
    assert len(nodes) == 1, f"expected exactly one http-out, got {len(nodes)}"
    assert nodes[0].detail.get("path") == "/v1/stock", nodes[0].detail


def test_python_custom_wrapper_with_route_constant_yields_http_out() -> None:
    """The Python guard has the same blind spot and the same remedy."""
    assert _http_out(PYTHON_CUSTOM_WRAPPER, language="python", rel_path="processor.py")


# --------------------------------------------------------------------------
# Precision — widening a guard is what makes the broad pattern unsafe
# --------------------------------------------------------------------------

JAVA_CACHE_CLIENT = """
public class StockCache {
    private final CacheClient cacheClient;

    public Stock lookup(String key) {
        return cacheClient.get(key);
    }
}
"""

JAVA_ROUTE_CONSTANT_ONLY = """
public class StockRoutes {
    public static final String STOCK_SUBMIT_URL = "/v1/stock";
    public static final String STOCK_CANCEL_URL = "/v1/stock/cancel";
}
"""

JAVA_FILE_PATH_CONSTANT = """
public class StockTemplates {
    private static final String TEMPLATE = "/config/app.yml";
    private final TemplateClient client;

    String render(Model model) {
        return client.get(TEMPLATE, model);
    }
}
"""


def test_guard_still_rejects_a_non_http_client_call() -> None:
    """`cacheClient.get(key)` with no route constant is not an HTTP call.

    The guard exists precisely to keep `\\w*[Cc]lient.get(` from matching every
    Mapping-like helper in the fleet; widening its evidence must not cost that.
    """
    assert _http_out(JAVA_CACHE_CLIENT, rel_path="src/StockCache.java") == []


def test_route_constant_alone_does_not_emit_http_out() -> None:
    """Guards gate; they do not emit. A constants file makes no outbound call."""
    assert _http_out(JAVA_ROUTE_CONSTANT_ONLY, rel_path="src/StockRoutes.java") == []


def test_a_file_path_constant_is_not_route_evidence() -> None:
    """`/config/app.yml` is a resource path, not a route.

    Reusing the existing route-like predicate rather than "any string starting
    with a slash" is what keeps this out; a looser test would admit every
    filesystem constant in the fleet.
    """
    assert _http_out(JAVA_FILE_PATH_CONSTANT, rel_path="src/StockTemplates.java") == []


# --------------------------------------------------------------------------
# The evidence must be a property of the source, not of pass ordering
# --------------------------------------------------------------------------

def test_guard_evidence_does_not_depend_on_pass_order() -> None:
    """Running the outbound pass alone, with no prior passes, must still work.

    `ctx.nodes` is empty here. An implementation that looked for a
    `path-constant` node would find nothing and emit nothing — which is exactly
    what the issue document's proposed fix would have done, since
    `extract_http_outbound` runs *before* `extract_path_constants`.
    """
    ctx = FileExtractionContext(
        repo_id="test/sample",
        rel_path="src/StockRequestProcessor.java",
        language="java",
        source=JAVA_CUSTOM_WRAPPER,
    )
    assert ctx.nodes == []
    extract_http_outbound(ctx)
    assert [n for n in ctx.nodes if n.family == "http-out"], (
        "guard must be satisfiable from the source text alone"
    )


# --------------------------------------------------------------------------
# Transport-agnostic vocabulary (2a)
# --------------------------------------------------------------------------

JAVA_STATUS_EVIDENCE = """
public class StockSender {
    private final TransportClient client;

    Response send(StockRequest request) {
        Response response = client.post(request);
        if (response.status() == HttpStatus.ACCEPTED) {
            return response;
        }
        throw new IllegalStateException();
    }
}
"""


@pytest.mark.parametrize("token", ["HttpStatus", "MediaType", "Authorization", "Bearer"])
def test_transport_agnostic_tokens_are_http_evidence(token: str) -> None:
    """An in-house wrapper still names statuses, media types and auth headers.

    These say "this file speaks HTTP" without naming any particular library, so
    they extend the guard without tying it to a vocabulary that needs updating
    every time a new client appears.
    """
    source = JAVA_STATUS_EVIDENCE.replace("HttpStatus", token)
    assert _http_out(source, rel_path="src/StockSender.java")
