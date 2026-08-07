"""OI-39: which files are the deployed service, and which are not.

Two defects in one predicate, found while closing out `OI-37`'s deferred
"exclude test and vendored trees" item.

`TEST_PATH_RX` gates **all** extraction — `extract_from_file` returns `[], []`
for a match — so it decides what the tool can see at all. It matched whole path
*segments*, which left two holes in opposite directions.

**Too narrow.** A test file living beside the code it tests is invisible to
segment matching: `routes.spec.ts`, `handler_test.go`, `test_views.py`. 64% of
`OI-37`'s 10,225 false endpoints were in files shaped like these, and a route
declared only in a test is not an entry point of the deployed service.

**Too wide, and worse.** The camelCase branch `[a-z][a-zA-Z0-9]*Tests?` sat under
`re.IGNORECASE`, so it read as *any segment ending in "test"* — silently
excluding `latest/`, `protest/`, `contest/`, `attest/` and `greatest/` from every
pass. `api/latest/` is a perfectly ordinary versioned API directory, and a repo
laid out that way contributed **nothing** to the metabase with no note, no
warning and no count. `OI-36` in the direction that costs findings.

The two halves are tested together because they are one predicate and a change to
either can reopen the other.
"""

from __future__ import annotations

import pytest

from src2sink.extractors.unified import _is_test_path, extract_from_file


# --- too wide: production code was being dropped ------------------------------


@pytest.mark.parametrize("path", [
    "api/latest/handler.go",        # a versioned API directory — the realistic one
    "src/protest/handler.go",
    "src/contest/service.js",
    "src/attest/signer.py",
    "src/greatest/hits.ts",
])
def test_a_word_ending_in_test_is_not_a_test_directory(path):
    """`[a-z][a-zA-Z0-9]*Tests?` under IGNORECASE matched English words.

    Whole repositories laid out under `api/latest/` produced nothing at all, and
    said nothing about it. The branch is now case-sensitive: a literal capital
    `T` distinguishes `FooTest` from `latest`.
    """
    assert not _is_test_path(path)


def test_a_dropped_directory_would_have_been_silent():
    """The reason this is `OI-36` and not merely a false positive.

    Extraction returns `[], []` for a test path — no note, no count, no warning.
    A repo excluded this way is indistinguishable from one that is genuinely
    clean, which is the failure mode the whole class is about.
    """
    nodes, edges = extract_from_file(
        repo_id="g/r", rel_path="api/latest/Api.java", language="java",
        source='@RestController class Api { @GetMapping("/stock") String s() { return ""; } }',
    )
    assert nodes, "production code under api/latest/ must be extracted"
    assert any(n.family == "http-in" for n in nodes)


# --- still too narrow: test files beside the code ------------------------------


@pytest.mark.parametrize("path", [
    "src/routes.spec.js",
    "src/routes.spec.ts",
    "src/routes.test.ts",
    "src/routes.test.tsx",
    "src/api.cy.js",
    "internal/handler_test.go",
    "app/test_views.py",
    "app/views_test.py",
    "src/main/java/StockControllerTest.java",
    "src/main/kotlin/StockControllerTests.kt",
    "cypress/support/commands.js",
])
def test_a_test_file_beside_its_code_is_excluded(path):
    """Segment matching could not see these, and 64% of `OI-37`'s false
    endpoints were in files shaped like them."""
    assert _is_test_path(path)


def test_a_route_declared_only_in_a_test_is_not_an_entry_point():
    """The point of the exclusion, at the level `OI-37` cared about.

    A mock server in a spec file is not a door into the deployed service, and
    reachability computed from one is answering about code that does not ship.
    """
    nodes, _ = extract_from_file(
        repo_id="g/r", rel_path="src/routes.spec.js", language="javascript",
        source="app.get('/mock-stock', handler);",
    )
    assert [n for n in nodes if n.family == "http-in"] == []


# --- the boundary cases that make both halves honest ---------------------------


@pytest.mark.parametrize("path", [
    "api/openapi.spec.yaml",     # an API *specification*, not a test
    "config/app.spec.json",
    "src/spectrum.js",
    "src/latest.ts",             # a file, not a directory
    "manifest/deploy.yaml",
])
def test_a_specification_is_not_a_test(path):
    """`*.spec.*` is a test convention in JS/TS and a document elsewhere.

    Anchoring the filename rule to source extensions is what keeps an OpenAPI
    spec — which genuinely describes the service's endpoints — from being
    excluded as though it were a test.
    """
    assert not _is_test_path(path)


@pytest.mark.parametrize("path", [
    "loadtest/run.js",
    "smoke-test/a.js",
    "perf_test/b.py",
    "acceptance-tests/c.ts",
])
def test_lowercase_compound_test_directories_are_named_explicitly(path):
    """These were caught by the over-broad suffix rule that had to go.

    Listing them costs a line each and cannot swallow `latest`. A rule broad
    enough to catch them by shape is exactly the rule that caused the defect
    above.
    """
    assert _is_test_path(path)


@pytest.mark.parametrize("path", [
    "src/test/java/FooTest.java",
    "__tests__/server.js",
    "e2e/flow.js",
    "src/StockControllerTest/x.java",
])
def test_the_directory_conventions_still_work(path):
    """The half that was already right must not regress while fixing the rest."""
    assert _is_test_path(path)


def test_the_predicate_is_the_gate_for_every_pass():
    """Worth stating, because the blast radius is what makes this high severity.

    This is not an inbound-endpoint filter. A match means the file contributes
    no nodes of any family — no sinks, no PII, no dependencies.
    """
    nodes, edges = extract_from_file(
        repo_id="g/r", rel_path="src/thing.spec.ts", language="typescript",
        source="const q = 'SELECT * FROM users WHERE id = ' + id;",
    )
    assert (nodes, edges) == ([], [])
