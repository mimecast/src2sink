"""Regression tests for cross-repo caller detection.

A service reached through a published client library, a named path constant, or a
hand-rolled HTTP wrapper used to be invisible to the graphs: the metabase
reported a successful run while almost every real caller was absent. The whole
point of the tool is to carry a path from an input in one repo through to a sink
in another, so a dropped hop is a correctness bug, not a missing nicety.

Each *shape* of caller gets a test, and the repo ids below name the shape under
test (`apps/client-lib-caller`, `apps/const-path-caller`, …) rather than any real
service. Fixture names follow the sanitised placeholder set in
`api-clients.example.json` — never a real repo, package, or class name, which in
a scanner's own test data would leak exactly the internal topology the tool is
built to map.
"""

from __future__ import annotations

import json

import pytest

from src2sink import known_api_clients as kac
from src2sink.aggregators.service_call_collect import collect_service_edges
from src2sink.aggregators.openapi_match import (
    build_openapi_inbound_index,
    match_http_out_to_openapi,
)
from src2sink.aggregators.openapi_models import OpenApiSpec
from src2sink.extractors.http_out import (
    build_path_symbol_table,
    configure_http_out_client_patterns,
    get_binding_call_patterns,
)
from src2sink.extractors.regex_extractors import (
    extract_http_outbound,
    extract_path_constants,
)
from src2sink.extractors.file_context import FileExtractionContext
from src2sink.graph_common import build_repo_alias_index, match_path_in_inbound_index
from src2sink.known_api_clients import ApiClientBinding

TARGET = "acme/sql-runner-api"
CLIENT_ARTIFACT = "sql-runner-api-client"
IMPORT_PREFIX = "com.example.acme.sqlrunner.client"
PATH_SUBMIT = "/v1/query"
PATH_SYNC = "/v1/query/sync"

BINDING = ApiClientBinding(
    target_repo=TARGET,
    maven_artifact=CLIENT_ARTIFACT,
    import_prefix=IMPORT_PREFIX,
    paths=(PATH_SUBMIT, PATH_SYNC),
    payload_fields=("sql",),
    service_aliases=("sql-runner-api",),
    class_patterns=("SqlRunnerApiClient", "AcmeSqlRunnerClient"),
)


@pytest.fixture
def bindings():
    """Configure the api-client binding registry for the duration of one test."""
    kac.configure_api_client_bindings((BINDING,))
    configure_http_out_client_patterns((BINDING,))
    yield (BINDING,)
    kac.configure_api_client_bindings(())
    configure_http_out_client_patterns(())


def _nodes(source: str, language: str = "java", rel_path: str = "Caller.java"):
    """Run just the outbound + path-constant regex passes over one source string."""
    ctx = FileExtractionContext(
        repo_id="apps/caller", rel_path=rel_path, language=language, source=source,
    )
    extract_http_outbound(ctx)
    extract_path_constants(ctx)
    return ctx.nodes


def _record(group: str, name: str, nodes: list[dict]) -> dict:
    return {
        "schema_version": 2, "group": group, "name": name,
        "nodes": nodes, "edges": [],
    }


def _provider() -> dict:
    """The target service, exposing the two inbound routes the binding declares."""
    return _record("acme", "sql-runner-api", [
        {
            "family": "http-in", "kind": "source", "file": "QueryResource.java",
            "line": 12, "detail": {"method": "POST", "path": PATH_SUBMIT},
        },
        {
            "family": "http-in", "kind": "source", "file": "QueryResource.java",
            "line": 18, "detail": {"method": "POST", "path": PATH_SYNC},
        },
    ])


def _callers_of(records: list[dict], target: str = TARGET) -> set[str]:
    edges, _broken = collect_service_edges(records)
    return {e.source_repo for e in edges if e.target_repo == target}


# ---------------------------------------------------------------------------
# The regression that made every other fix inert: binding class_patterns were
# imported by value into regex_extractors, so reconfiguring them had no effect.
# ---------------------------------------------------------------------------


def test_binding_class_patterns_reach_the_extractor(bindings) -> None:
    """`class_patterns` must be visible to the extractor after configuration.

    `regex_extractors` used to do `from .http_out import _BINDING_CLASS_RX`, which
    snapshots the empty list at import time; `configure_http_out_client_patterns`
    rebinds the module global, so the extractor's copy stayed empty forever and
    every configured `class_patterns` binding was dead code.
    """
    assert [p.target_repo for p in get_binding_call_patterns()] == [TARGET]

    nodes = _nodes('''
class Facade {
  private final SqlRunnerApiClient runnerClient;
  Result run(String sql) { return runnerClient.executeQuery(sql); }
}
''')
    http_out = [n for n in nodes if n.family == "http-out"]
    assert http_out, "class_patterns produced no call-site node"
    assert any(n.detail.get("target_repo") == TARGET for n in http_out), (
        "binding-derived node must name the target repo — the consumer's own "
        "source contains no host or path to infer it from"
    )


# ---------------------------------------------------------------------------
# Shape 1 — published client library, no URL anywhere in the source.
# ---------------------------------------------------------------------------


def test_api_client_import_alone_produces_a_call_edge() -> None:
    """An `api-client-consumer` import node must become a cross-repo edge.

    These nodes already carried `target_repo` and `paths` but were only read by
    the payload-producers report, so a client-library caller could never appear in
    service-call-edges.jsonl.
    """
    consumer = _record("apps", "client-lib-caller", [
        {
            "family": "api-client-consumer", "kind": "propagator",
            "file": "Facade.java", "line": 3, "confidence": "high",
            "detail": {
                "client": CLIENT_ARTIFACT,
                "target_repo": TARGET,
                "import": f"import {IMPORT_PREFIX}.SqlRunnerApiClient",
                "paths": [PATH_SUBMIT, PATH_SYNC],
            },
        },
    ])
    edges, _ = collect_service_edges([consumer, _provider()])
    client_edges = [e for e in edges if e.source_repo == "apps/client-lib-caller"]
    assert client_edges, "client-library caller produced no edge"
    e = client_edges[0]
    assert e.target_repo == TARGET
    assert e.confidence == "high"
    # An import proves the hop, not the route, so the declared paths are reported
    # as evidence rather than fabricated as separate route-level edges.
    assert e.target_path == "*"
    assert PATH_SUBMIT in e.evidence


def test_single_declared_path_binding_yields_a_route_edge() -> None:
    """One declared path is specific enough to emit as a route, not `*`."""
    consumer = _record("apps", "client-lib-caller", [
        {
            "family": "api-client-consumer", "kind": "propagator",
            "file": "Facade.java", "line": 3,
            "detail": {
                "client": CLIENT_ARTIFACT,
                "target_repo": TARGET,
                "paths": [PATH_SYNC],
            },
        },
    ])
    edges, _ = collect_service_edges([consumer, _provider()])
    assert [(e.target_repo, e.target_path) for e in edges] == [(TARGET, PATH_SYNC)]


def test_api_client_consumer_matches_openapi_specs() -> None:
    """OpenAPI matching must consider api-client-consumer paths, not only http-out."""
    consumer = _record("apps", "client-lib-caller", [
        {
            "family": "api-client-consumer", "kind": "propagator",
            "file": "Facade.java", "line": 3,
            "detail": {"target_repo": TARGET, "paths": [PATH_SYNC]},
        },
    ])
    inbound = build_openapi_inbound_index([
        OpenApiSpec(
            target_repo=TARGET,
            spec_path="openapi.yaml",
            paths=[PATH_SUBMIT, PATH_SYNC],
        ),
    ])
    rows = match_http_out_to_openapi([consumer], inbound)
    assert [(r["source_repo"], r["target_repo"]) for r in rows] == [
        ("apps/client-lib-caller", TARGET),
    ]


def test_openapi_match_respects_a_declared_target_repo() -> None:
    """A binding-declared target must not fan out to every service sharing a route."""
    consumer = _record("apps", "client-lib-caller", [
        {
            "family": "api-client-consumer", "kind": "propagator",
            "file": "Facade.java", "line": 3,
            "detail": {"target_repo": TARGET, "paths": [PATH_SUBMIT]},
        },
    ])
    inbound = build_openapi_inbound_index([
        OpenApiSpec(target_repo=TARGET, spec_path="a.yaml", paths=[PATH_SUBMIT]),
        OpenApiSpec(
            target_repo="acme/unrelated-service",
            spec_path="b.yaml",
            paths=[PATH_SUBMIT],
        ),
    ])
    rows = match_http_out_to_openapi([consumer], inbound)
    assert {r["target_repo"] for r in rows} == {TARGET}


# ---------------------------------------------------------------------------
# Shape 2 — direct HTTP, but with indirection at the call site.
# ---------------------------------------------------------------------------


def test_lowercase_rest_template_instance_call_is_detected() -> None:
    """`restTemplate.exchange(...)` — the common instance call — must be captured.

    The pattern was anchored to the class name `RestTemplate.`, case-sensitively,
    so every real Spring call site through an injected field was dropped.
    """
    nodes = _nodes(f'''
class Fetcher {{
  private static final String PATH_QUERY = "{PATH_SYNC}";
  private final RestTemplate restTemplate = new RestTemplate();
  Result fetch(String sql) {{
    return restTemplate.exchange(host + PATH_QUERY, HttpMethod.POST, entity, Result.class);
  }}
}}
''')
    out = [n for n in nodes if n.family == "http-out"]
    assert out, "lowercase restTemplate instance call not detected"
    # The path arrives via the class constant, not a literal in the window.
    assert any(n.detail.get("path") == PATH_SYNC for n in out)


def test_java_custom_client_wrapper_needs_file_level_http_evidence() -> None:
    """`client.post(CONST)` is only trusted in a file that looks like an HTTP client."""
    body = f'''
class Wrapper {{
  private static final String SUBMIT_URL = "{PATH_SUBMIT}";
  Handle submit(String sql) {{ return client.post(SUBMIT_URL, sql); }}
}}
'''
    with_evidence = _nodes("import org.springframework.http.HttpEntity;\n" + body)
    assert any(
        n.family == "http-out" and n.detail.get("path") == PATH_SUBMIT
        for n in with_evidence
    ), "custom Java client wrapper not detected"

    # No HTTP stack referenced anywhere in the file -> the broad receiver pattern
    # must stay quiet rather than flagging every `x.post(` in the fleet.
    without = _nodes(body)
    assert not [n for n in without if n.family == "http-out"]


def test_python_self_post_wrapper_needs_file_level_http_evidence() -> None:
    """`self.post(url)` in a Python service client resolves through an enum member."""
    body = f'''
class ApiPaths:
    QUERY_SYNC = "{PATH_SYNC}"

class Client:
    def run(self, sql):
        url = ApiPaths.QUERY_SYNC
        return self.post(url, json={{"sql": sql}})
'''
    with_evidence = _nodes(
        "import requests\n" + body, language="python", rel_path="client.py",
    )
    assert any(
        n.family == "http-out" and n.detail.get("path") == PATH_SYNC
        for n in with_evidence
    ), "python custom client call site not detected"

    without = _nodes(body, language="python", rel_path="mapping.py")
    assert not [n for n in without if n.family == "http-out"]


def test_self_get_on_a_mapping_class_is_not_an_http_call() -> None:
    """The file-level guard is what keeps `self.get(` off dict-like helpers."""
    nodes = _nodes('''
class Config(dict):
    def lookup(self, key):
        return self.get(key, None)
''', language="python", rel_path="config.py")
    assert not [n for n in nodes if n.family == "http-out"]


# ---------------------------------------------------------------------------
# Shape 3 — constant / enum / config indirection.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "name", "value"),
    [
        ('private static final String PATH_QUERY = "/v1/query/sync";', "PATH_QUERY", "/v1/query/sync"),
        ('const val PATH_QUERY = "/v1/query"', "PATH_QUERY", "/v1/query"),
        ('QUERY_SYNC = "/v1/query/sync"', "QUERY_SYNC", "/v1/query/sync"),
        ('QUERY_SYNC("/v1/query/sync"),', "QUERY_SYNC", "/v1/query/sync"),
        ('BASE: str = "https://sql-runner-api.internal.example.com/v1"', "BASE", "https://sql-runner-api.internal.example.com/v1"),
    ],
)
def test_symbol_table_captures_endpoint_constants(source, name, value) -> None:
    assert build_path_symbol_table(source).get(name) == value


def test_symbol_table_ignores_non_endpoint_literals() -> None:
    """Only URL/path-shaped literals are remembered, so nothing else can leak in."""
    table = build_path_symbol_table('MESSAGE = "hello world"\nSQL = "select 1"')
    assert table == {}


def test_path_constant_nodes_recover_the_cross_file_case() -> None:
    """A route constant declared in another file still yields an edge.

    In-file uses are resolved by the symbol table; when the enum lives in a
    separate module the declaration itself has to become a node so the
    aggregators' path-reference scan can match it to an inbound route.
    """
    endpoints = _nodes(
        f'class ApiPaths:\n    QUERY_SYNC = "{PATH_SYNC}"\n',
        language="python", rel_path="paths.py",
    )
    consts = [n for n in endpoints if n.family == "path-constant"]
    assert [(n.kind, n.detail["path"]) for n in consts] == [("reference", PATH_SYNC)]

    caller = _record("apps", "py-crossfile-caller", [
        {
            "family": n.family, "kind": n.kind, "file": n.file, "line": n.line,
            "detail": n.detail,
        }
        for n in consts
    ])
    assert _callers_of([caller, _provider()]) == {"apps/py-crossfile-caller"}


def test_path_constants_skip_file_paths_and_generic_one_word_routes() -> None:
    """Precision guards: resource paths and bare single segments are not routes."""
    nodes = _nodes(
        'TEMPLATE = "/templates/report.html"\n'
        'NAME = "/queries"\n'
        'QUERY_PATH = "/queries"\n',
        language="python", rel_path="paths.py",
    )
    paths = {n.detail["path"] for n in nodes if n.family == "path-constant"}
    # report.html is a resource; "/queries" only qualifies via the endpoint-ish
    # constant name (QUERY_PATH), not the generic NAME.
    assert paths == {"/queries"}
    symbols = {n.detail["symbol"] for n in nodes if n.family == "path-constant"}
    assert symbols == {"QUERY_PATH"}


def test_service_alias_in_call_context_resolves_the_target(bindings) -> None:
    """A base-URL helper name is often the only clue to which service is called."""
    nodes = _nodes(f'''
import requests

class Client:
    def __init__(self):
        self.base_url = get_sql_runner_api_base_url()

    def run(self, sql):
        return self.post(self.base_url + "{PATH_SYNC}", json={{"sql": sql}})
''', language="python", rel_path="client.py")
    out = [n for n in nodes if n.family == "http-out"]
    assert any(n.detail.get("target_repo") == TARGET for n in out), (
        "service alias in the call context did not resolve to the target repo"
    )


def test_config_key_resolves_to_a_repo_via_the_alias_index(bindings) -> None:
    """`${sql-runner-api.base-url}` names the service even with no host literal."""
    consumer = _record("apps", "config-host-caller", [
        {
            "family": "http-out", "kind": "sink", "file": "Fetcher.java",
            "line": 20,
            "detail": {
                "purpose": "client-call",
                "raw": "restTemplate.exchange(",
                "config_key": "sql-runner-api.base-url",
            },
        },
    ])
    assert _callers_of([consumer, _provider()]) == {"apps/config-host-caller"}


# ---------------------------------------------------------------------------
# Binding service aliases in the host index.
# ---------------------------------------------------------------------------


def test_binding_aliases_join_the_host_index(bindings) -> None:
    """Config-declared service aliases must resolve hosts, not just repo names."""
    index = build_repo_alias_index([_record("acme", "other-repo", [])])
    assert index["sql-runner-api"] == TARGET


def test_repo_records_win_over_binding_aliases(bindings) -> None:
    """A real repo record is stronger evidence than a configured alias."""
    records = [_record("acme", "sql-runner-api", [])]
    assert build_repo_alias_index(records)["sql-runner-api"] == TARGET


# ---------------------------------------------------------------------------
# Path matching correctness.
# ---------------------------------------------------------------------------


def test_inbound_match_prefers_the_best_confidence_candidate() -> None:
    """The fuzzy pass must not return the first dict-order match.

    `/api/v1/queries` overlaps `/queries` only at the segment level (low) but is
    a genuine prefix match for `/api/v1` (medium); returning whichever came first
    in iteration order pointed the edge at the wrong service.
    """
    inbound = {
        "/queries": [("acme/wrong-service", "/queries", "POST", "A.java:1")],
        "/api/v1": [("acme/right-service", "/api/v1", "POST", "B.java:1")],
    }
    rows, conf = match_path_in_inbound_index("/api/v1/queries", inbound)
    assert conf == "medium"
    assert [r[0] for r in rows] == ["acme/right-service"]


def test_inbound_match_memo_is_consistent() -> None:
    """A supplied memo must return the same result as an unmemoised lookup."""
    inbound = {PATH_SUBMIT: [(TARGET, PATH_SUBMIT, "POST", "A.java:1")]}
    memo: dict = {}
    first = match_path_in_inbound_index(PATH_SUBMIT, inbound, memo=memo)
    second = match_path_in_inbound_index(PATH_SUBMIT, inbound, memo=memo)
    assert first == second == match_path_in_inbound_index(PATH_SUBMIT, inbound)
    assert memo


# ---------------------------------------------------------------------------
# Negative coverage signal.
# ---------------------------------------------------------------------------


def _write_records(tmp_path, records):
    jsons = []
    for rec in records:
        d = tmp_path / "repos" / rec["group"]
        d.mkdir(parents=True, exist_ok=True)
        jp = d / f"{rec['name']}.json"
        jp.write_text(json.dumps(rec), encoding="utf-8")
        jsons.append(jp)
    return jsons


def test_unmatched_outbound_sites_are_written_and_bindings_reconciled(tmp_path, bindings) -> None:
    """Lost coverage must be machine-readable, not just sampled into markdown."""
    from src2sink.aggregators.service_call_report import write_service_call_graph

    orphan = _record("apps", "orphan", [
        {
            "family": "http-out", "kind": "sink", "file": "Orphan.java", "line": 7,
            "detail": {
                "purpose": "client-call",
                "raw": 'restTemplate.getForObject("https://unknown-thing.example.net/x")',
            },
        },
    ])
    jsons = _write_records(tmp_path, [orphan, _provider()])
    write_service_call_graph(tmp_path, jsons)

    unmatched = tmp_path / "graphs" / "service-call-unmatched.jsonl"
    rows = [json.loads(line) for line in unmatched.read_text().splitlines()]
    assert any(r["source_repo"] == "apps/orphan" and r["reason"] for r in rows)

    md = (tmp_path / "graphs" / "service-call-graph.md").read_text()
    # The binding is configured but no repo in this fixture calls it — the run
    # must say so rather than presenting an empty graph as a clean result.
    assert "API-client binding coverage" in md
    assert "no callers at all" in md


def test_binding_coverage_flags_a_missing_bindings_config(tmp_path) -> None:
    """With no bindings at all, the report says client detection is off."""
    from src2sink.aggregators.service_call_report import write_service_call_graph

    jsons = _write_records(tmp_path, [_provider()])
    write_service_call_graph(tmp_path, jsons)
    md = (tmp_path / "graphs" / "service-call-graph.md").read_text()
    assert "No api-client bindings configured" in md


def test_binding_coverage_distinguishes_binding_derived_callers(tmp_path, bindings) -> None:
    """A service can look covered while the client-library path is entirely broken.

    Here a caller is found via a URL literal, so the target has callers — but no
    binding-derived caller, which is precisely the state that hid the original
    defect behind a non-empty graph.
    """
    from src2sink.aggregators.service_call_report import write_service_call_graph

    caller = _record("apps", "url-literal-caller", [
        {
            "family": "http-out", "kind": "sink", "file": "Direct.java", "line": 9,
            "detail": {
                "purpose": "client-call",
                "raw": f'URI.create("https://sql-runner-api.internal.example.com{PATH_SUBMIT}")',
                "path": PATH_SUBMIT,
            },
        },
    ])
    jsons = _write_records(tmp_path, [caller, _provider()])
    write_service_call_graph(tmp_path, jsons)
    md = (tmp_path / "graphs" / "service-call-graph.md").read_text()
    assert "no binding-derived callers" in md


def test_alias_hint_edges_are_medium_not_high(bindings) -> None:
    """A service name near a call site is a strong hint, not a declaration."""
    consumer = _record("apps", "alias-hint-caller", [
        {
            "family": "http-out", "kind": "sink", "file": "client.py", "line": 12,
            "detail": {
                "purpose": "client-call",
                "raw": "self.post(",
                "target_repo": TARGET,
                "target_repo_evidence": "service alias 'sql-runner-api' in call context",
                "target_repo_confidence": "medium",
            },
        },
    ])
    edges, _ = collect_service_edges([consumer, _provider()])
    hinted = [e for e in edges if "service alias" in e.evidence]
    assert hinted and {e.confidence for e in hinted} == {"medium"}


def test_class_pattern_edges_are_high(bindings) -> None:
    """A configured api-client class match *is* a declaration of the hop."""
    consumer = _record("apps", "client-lib-caller", [
        {
            "family": "http-out", "kind": "sink", "file": "Facade.java", "line": 6,
            "detail": {
                "purpose": "api-client-consumer",
                "raw": "SqlRunnerApiClient",
                "target_repo": TARGET,
                "target_repo_evidence": f"api-client class {CLIENT_ARTIFACT}",
                "target_repo_confidence": "high",
            },
        },
    ])
    edges, _ = collect_service_edges([consumer, _provider()])
    declared = [e for e in edges if "api-client class" in e.evidence]
    assert declared and {e.confidence for e in declared} == {"high"}


# ---------------------------------------------------------------------------
# A bindings file that loads nothing must not look like a clean run.
# ---------------------------------------------------------------------------


def _empty_bindings_file(tmp_path):
    p = tmp_path / "api-clients.json"
    p.write_text(json.dumps({"bindings": []}), encoding="utf-8")
    return p


def test_configure_from_path_rejects_an_empty_bindings_file(tmp_path) -> None:
    with pytest.raises(kac.ApiClientConfigError) as exc:
        kac.configure_from_path(_empty_bindings_file(tmp_path))
    assert "0 bindings" in str(exc.value)
    assert "--allow-empty-api-clients" in str(exc.value)


def test_configure_from_path_allows_empty_when_opted_in(tmp_path) -> None:
    assert kac.configure_from_path(
        _empty_bindings_file(tmp_path), allow_empty=True,
    ) == ()
    assert kac.get_bindings() == ()
    assert get_binding_call_patterns() == []


def test_configure_from_path_wires_both_consumers(tmp_path) -> None:
    """One entry point, so no CLI can configure the registry but not the patterns."""
    p = tmp_path / "api-clients.json"
    p.write_text(json.dumps({"bindings": [{
        "target_repo": TARGET,
        "maven_artifact": CLIENT_ARTIFACT,
        "import_prefix": IMPORT_PREFIX,
        "paths": [PATH_SUBMIT],
        "service_aliases": ["sql-runner-api"],
        "class_patterns": ["SqlRunnerApiClient"],
    }]}), encoding="utf-8")
    try:
        assert len(kac.configure_from_path(p)) == 1
        assert kac.get_bindings()[0].target_repo == TARGET
        assert [c.target_repo for c in get_binding_call_patterns()] == [TARGET]
    finally:
        kac.configure_api_client_bindings(())
        configure_http_out_client_patterns(())


@pytest.mark.watchdog(60)
def test_build_cli_fails_on_empty_api_clients(tmp_path, monkeypatch) -> None:
    """The build must stop rather than silently produce a client-blind metabase."""
    import sys

    from src2sink.build_metabase_v2 import main

    repo = tmp_path / "repos" / "grp" / "svc"
    repo.mkdir(parents=True)
    (repo / "A.java").write_text("class A {}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "src2sink-build",
        "--repos-root", str(tmp_path / "repos"),
        "--metabase-root", str(tmp_path / "mb"),
        "--workers", "1",
        "--api-clients", str(_empty_bindings_file(tmp_path)),
    ])
    with pytest.raises(SystemExit) as exc:
        main()
    assert "0 bindings" in str(exc.value)


@pytest.mark.watchdog(60)
def test_manifest_records_the_real_binding_count(tmp_path, monkeypatch, bindings) -> None:
    """`api_clients_configured` only said a path was passed; the count is the signal."""
    import argparse

    from src2sink.build_metabase_v2 import _write_run_manifest

    args = argparse.Namespace(
        repos_root="/abs/repos", metabase_root="/abs/mb", workers=1,
        repo_timeout=60, max_files_per_repo=100, max_file_bytes=1000,
        max_line_bytes=1000, force=False, repo=None, limit=0,
        api_clients="/etc/secrets/api-clients.json", prescreen_indicators=None,
    )
    _write_run_manifest(
        tmp_path, args, [], skipped=0, timed_out=0,
        started_at="T0", finished_at="T1",
    )
    m = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert m["invocation"]["api_clients_binding_count"] == 1
