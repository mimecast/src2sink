# src2sink — Closed Detection Issues

Issues that have been **fixed and verified**, moved here from
[`src2sink-open-issues.md`](src2sink-open-issues.md). That file is the backlog;
this one is the record. Nothing is ever deleted from here — a closed issue is
evidence about how a detection path once failed, which is exactly the context
someone needs when the same area breaks again.

**Anonymisation notice:** as in the open-issues document — every repository,
package, artifact id, service, class, constant and URL path is fictitious.
References to `src2sink`'s own source (file:line) and to third-party library
names are real.

---

## How an issue is closed

1. **Verify.** The fix is merged, `make ci` is green, and the issue's own
   regression test exists and fails against the pre-fix code. An issue is not
   closed by a fix that has no test.
2. **Move the section verbatim.** Cut the whole `## n. Title  \`OI-n\`` section
   out of the open-issues file and paste it below, under the same `OI-n` id.
   Do not rewrite it — the original symptom and root-cause text is the record.
   Renumber the heading to `## OI-n — Title`, since section ordering no longer
   applies.
3. **Prepend a `### Resolution` block** to the moved section, with:
   * **Fixed in** — the release version;
   * **Commit** — the sha(s) of the fix, short form, and the PR number if there
     was one;
   * **Tests** — the test ids that now guard it, so the link from issue to test
     is greppable in both directions;
   * **What changed** — two or three sentences of what was actually done,
     including where it **deviated** from the fix proposed in the original
     section. A proposed fix that was amended during implementation is the most
     valuable thing on the page; record why.
   * **Behaviour change** — any output that a consumer would see differ, or
     "none".
4. **Add a row to the index below.**
5. **Update the open-issues §5 priority table** — remove the row.

**On the commit sha:** the sha of the fix is not knowable inside the fix commit
itself, so the move is a *follow-up* commit — normally the release-prep commit,
which can close several issues at once. Do not leave a `TBD` in the sha column;
an entry without a sha is not a record of anything.

**`OI-5` and `OI-6` do not exist**, and never did — the ids were minted from
section numbers, two of which were the Priority table and the Cross-cutting
principle rather than issues. The gap in this index is an artefact of that, not a
withdrawn issue. See the open-issues lifecycle section.

**Do not repeat the issue text in `CHANGELOG.md`.** The changelog says what
changed for a user; this file says why the detection was wrong. They serve
different readers.

---

## Index

| id | Issue | Fixed in | Commit | Behaviour change for consumers |
|---|---|---|---|---|
| `OI-1` | Version prefixes outrank real route names in path matching | 2.0.0 | `ccf471d`, `759235d` (PR #5) | Edges resolved through a bare `/v1` or `/api` disappear; edges that ranked `low` through a version prefix are now `medium`. |
| `OI-2` | Context guards suppress fully custom HTTP wrappers | 2.0.0 | `b56df04`, `22bf572` (PR #7) | In-house wrapper call sites now produce `http-out` nodes, so those callers appear in the graphs. |
| `OI-4` | Client discovery is single-direction and never proposes `class_patterns` | 2.0.0 | `1bd90d5`, `636476c` (PR #9) | A new `discovery_method` field, and candidates for callers that declare no client library. |
| `OI-3` | Dependency parsing misses Gradle version catalogs | 2.0.0 | `2585fe0`, `099d018` (PR #6) | Repos using version catalogs now report `dependencies_internal`, so they contribute candidates to api-client discovery for the first time. |
| `OI-7` | The `sql` family matches on method name alone | 2.0.0 | `fbe967f`, `1339b60` (PR #5) | `sql` and `raw-code-payload` counts fall fleet-wide. |
| `OI-8` | SQL built by formatting produces no node at all | 2.0.0 | `08fd682`, `37d753d` (PR #5) | More `sql` source nodes, including the embedded-quote concatenation shape that is the canonical injection form. |
| `OI-9` | An outbound request carrying a SQL payload has no home family | 2.0.0 | `9be4d7c`, `63674c7` (PR #8) | New `sql-payload-out` family, its own taint catalogue, and a `sql_payload_out` index count. |
| `OI-10` | `parameterised` is reported as a safety property it cannot establish | 2.0.0 | `33554b1` (PR #5) | `detail.parameterised` changes from boolean to a posture string; old metabases still load. |
| `OI-11` | A base-query constant hides the concatenation appended to it | 2.0.0 | `ce7e6fd`, `3d10d27` (PR #5) | The base-query-plus-clause shape now produces a finding and reports `mixed` rather than `parameterised`. |
| `OI-12` | Four unused runtime dependencies pull in sixty packages | 2.0.0 | `b54f74a` (PR #4, then #5) | Runtime closure falls from 68 packages to 8, all MIT or PSFL — no copyleft of any kind. |
| `OI-14` | Trace rebuilds the whole fleet graph for every target | 2.0.0 | `651683a` (PR #15) | None — same reports, materially faster. `run_trace` gains an optional `service_edges` argument. |
| `OI-16` | A detection fix never reaches a repo that has not changed | 2.0.0 | `6779191` (PR #19) | Records gain `detection_version`; the first run after upgrading rescans the whole fleet, and findings from superseded detectors disappear. |
| `OI-24` | The equality shortcut bypasses the significant-segment filter | 2.1.0 | `45c2ca3` (PR #23) | Edges resolved through two identical meaningless paths (`/v1` to `/v1`) disappear. |
| `OI-25` | Placeholder and operation-verb segments treated as destinations | 2.1.0 | `45c2ca3` (PR #23) | `/{id}` no longer matches `/{name}`; verb-only matches drop from `high` to `low`; `/orders/create` and `/orders/delete` stop matching each other. |
| `OI-26` | File-scoped SQL evidence admits every sink-named call in the file | 2.1.0 | `feba873` (PR #28) | `sql` sinks disappear where the receiver reads as another kind of boundary; the fabricated `raw-code-payload` endpoints built on them go with them. |
| `OI-19` | Dependency parsing covers 2 of 9 ecosystems, reads no lockfile | 2.1.0 | `a42c487` (PR #30) | Go and Python repos report dependencies for the first time; every dependency gains `version_kind`; unparsed ecosystems say so in notes. |
| `OI-18` | Dependency versions are recorded unresolved | 2.1.0 | `a20a0c5` (PR #31) | `${property}` and empty versions are replaced by resolved values or an explicit unresolved; BOM entries stop appearing as dependencies. |
| `OI-21` | Entry points are HTTP-annotation-only | 3.0.0 | `1f6edad` (PR #33) | New `entry-point` and `entry-marker` families. Queue consumers, gRPC, GraphQL, scheduled jobs and CLI entry points appear as ways in for the first time. |
| `OI-13` | Kotlin call sites are invisible to the AST pass | 3.0.0 | `9456ead` (PR #34) | Kotlin repos gain `sql` sinks, `script-exec` sinks and `raw-code-payload` findings from the AST tier for the first time. |
| `OI-28` | The index fast path bypasses the significant-segment filter | 3.0.0 | `75c5422` (PR #37) | Cross-repo edges resolved through a bare `/v1`, `/api` or `/{id}` disappear — the edges `OI-24` was meant to remove but did not reach. |

---

## OI-1 — Version prefixes outrank real route names in path matching

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `ccf471d`, `759235d` (PR #5)  
**Tests:** `tests/test_graph_common.py` (13-row confidence table, both directions), `tests/test_cross_repo_caller_coverage.py::test_version_prefix_does_not_outrank_a_route_name`, `tests/test_trace_render.py::test_trace_path_filter_keeps_version_prefix_semantics`

**What changed:** Segments naming a version (`/v1`) or a layer (`/api`) are dropped before comparison, and a side reducing to nothing matches nothing. The remaining significant segments are compared symmetrically: exact equality `high`, same significant segments `medium`, child route `medium`, tail-only overlap `low`. `match_path_in_inbound_index` additionally ranks equal-confidence candidates by specificity and sorts the result, so output cannot depend on index build order.

**Deviation from the proposed fix:** **Two.** The proposed implementation kept only equality and suffix relations, which would have regressed `/queries/{handle}` against `/queries` from `medium` to `None` — the most common real match there is. Prefix matching was kept over *significant* segments instead, which also closes the residual the section declared out of scope (`/v1/reservations` vs `/reservations/{ref}` now matches). Second, the section did not note that `path_templates_match` also backs the user-facing `trace --path` filter; a separate `path_filter_matches` predicate was split out, or the fix would have silently emptied that filter.

**Behaviour change:** Edges resolved through a bare `/v1` or `/api` disappear; edges that ranked `low` through a version prefix are now `medium`. `trace --path` filtering is unchanged.

---

_The original issue, verbatim:_


**Severity:** High — silently produces *wrong* edges, not merely missing ones.

### Symptom

A consumer declares its route as a constant:

```java
// fulfilment/fulfilment-commons — StockRequestProcessor.java
private static final String STOCK_SUBMIT_URL = "/v1/stock";
```

The target service exposes `POST /stock`. Instead of one edge to `commerce/warehouse-service`, the scan produced three edges to unrelated repositories:

```
fulfilment/fulfilment-commons -> pricing/price-index   /v1  medium  path/url reference in path-constant
fulfilment/fulfilment-commons -> shipping/label-store  /v1  medium  path/url reference in path-constant
fulfilment/fulfilment-commons -> billing/tax-service   /v1  medium  path/url reference in path-constant
```

The correct target was not merely ranked lower — it was **discarded**.

### Root cause

`graph_common.py:87-102`, `path_templates_match`:

```python
if o == i:
    return "high"
if o.startswith(i + "/") or i.startswith(o + "/"):
    return "medium"                      # "/v1/stock" vs "/v1"    -> medium
o_parts = [s for s in o.split("/") if s]
i_parts = [s for s in i.split("/") if s]
if len(o_parts) >= 2 and len(i_parts) >= 1 and o_parts[-len(i_parts):] == i_parts:
    return "low"                         # "/v1/stock" vs "/stock" -> low
return None
```

Confidence encodes **which structural rule fired**, not **how much meaning matched**. Any repo exposing a bare `/v1` route wins a `medium` prefix match against every `/v1/...` path in the fleet, beating the `low` suffix match that identifies the actual service.

`match_path_in_inbound_index` (`graph_common.py:185+`) then returns only the best-confidence group, so the correct candidate is dropped rather than ranked second.

### Proposed fix

Treat version and generic segments as carrying no routing information:

```python
# graph_common.py
_VERSION_SEGMENT_RX = re.compile(r"^v\d+$", re.I)
_GENERIC_SEGMENTS = frozenset({"api", "rest", "internal", "public", "service"})


def _significant(parts: list[str]) -> list[str]:
    """Drop segments that identify no route — /v1 and /api are not destinations."""
    return [
        p for p in parts
        if not _VERSION_SEGMENT_RX.match(p) and p.lower() not in _GENERIC_SEGMENTS
    ]


def path_templates_match(outbound: str, inbound: str) -> str | None:
    """Return confidence label if templates align, else None."""
    o, i = normalize_path_template(outbound), normalize_path_template(inbound)
    if not o or not i:
        return None
    if o == i:
        return "high"
    op = _significant([s for s in o.split("/") if s])
    ip = _significant([s for s in i.split("/") if s])
    # A side that reduces to nothing (a bare "/v1", "/api") matches nothing:
    # it names a version, not a destination.
    if not op or not ip:
        return None
    if op == ip:
        return "medium"
    if len(op) > len(ip) and op[-len(ip):] == ip:
        return "low"
    if len(ip) > len(op) and ip[-len(op):] == op:
        return "low"
    return None
```

Verified behaviour change (paths illustrative):

| outbound | inbound | 1.1.0 | proposed |
|---|---|---|---|
| `/v1/stock` | `/stock` | low | **medium** |
| `/v1/stock` | `/v1` | medium | **None** |
| `/v1/stock/dispatch` | `/stock/dispatch` | low | **medium** |
| `/api/v2/pallets` | `/pallets` | low | **medium** |
| `/stock` | `/stock` | high | high |
| `/v1/stock` | `/v1/reservations` | None | None |

**Deliberately not promoted to `high`.** Two services may legitimately version differently and both expose `/stock`; path-constant reference edges carry no host to disambiguate. `medium` is sufficient to win now that the `/v1` match is eliminated, and it keeps these edges out of consumers that admit only `high`/`openapi`.

### Companion change

In `match_path_in_inbound_index`, break ties **within** the winning confidence group by specificity — prefer the candidate with more matched significant segments. The 1.1.0 best-confidence rule removed dict-iteration nondeterminism but still resolves equal-confidence ties arbitrarily.

### Suggested tests

```python
assert path_templates_match("/v1/stock", "/v1") is None
assert path_templates_match("/v1/stock", "/stock") == "medium"
assert path_templates_match("/api/v2/orders", "/orders") == "medium"
assert path_templates_match("/orders", "/orders") == "high"
assert path_templates_match("/v1/orders", "/v1/invoices") is None
```

### Residual not covered

`/v1/reservations/` vs `/reservations/{ref}` still returns `None`: the constant lacks the templated tail. Matching a parameterised inbound route against a bare prefix needs separate handling.

---

---

## OI-2 — Context guards suppress fully custom HTTP wrappers

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `b56df04`, `22bf572` (PR #7)  
**Tests:** `tests/test_http_guard_evidence.py` (10 cases incl. three precision guards), snapshot `java-wrapper-caller`

**What changed:** The file-level guard gained transport-agnostic tokens (`MediaType`, `HttpStatus`, `Authorization`, `Bearer`; `status_code` and friends in Python) and, more importantly, accepts a route-like constant declared in the file as evidence in its own right. The route predicate reuses `_is_route_like_constant`, so `/config/app.yml` stays a resource path.

**Deviation from the proposed fix:** **Yes.** §2b proposed satisfying the guard from `ctx.nodes` on the stated grounds that `extract_path_constants` runs before `extract_http_outbound`. It runs *after*, so that fix would have been a no-op. Evidence is derived from the source text instead, which also makes the guard independent of pass order — `test_guard_evidence_does_not_depend_on_pass_order` fails against any `ctx.nodes`-based implementation.

**Behaviour change:** In-house wrapper call sites now produce `http-out` nodes, so those callers appear in the graphs. Two 1.1.0 tests were re-based on fixtures carrying neither a library name nor a route, preserving their original intent.

---

_The original issue, verbatim:_


**Severity:** Medium — misses a whole class of caller, silently.

### Symptom

A repo calls the service through an in-house REST abstraction:

```java
// fulfilment/fulfilment-commons — StockRequestProcessor.java
import com.example.fulfilment.commons.transport.ApiClient;

public class StockRequestProcessor {
    private static final String STOCK_SUBMIT_URL = "/v1/stock";
    private final ApiClient client;

    ...
    client.post(STOCK_SUBMIT_URL, request, StockSubmitResponse.class);
}
```

No `http-out` node is produced for this call site.

### Root cause

`extractors/http_out.py:92-97` pairs the broad receiver pattern with a file-level guard:

```python
(
    re.compile(r"\b\w*[Cc]lient\s*\.\s*(get|post|put|delete|patch|call|send|execute)\s*\("),
    "java",
    "client-call",
    _JAVA_HTTP_FILE_RX,
),
```

where (`http_out.py:65`):

```python
_JAVA_HTTP_FILE_RX = re.compile(
    r"\b(?:RestTemplate|WebClient|OkHttpClient|HttpClient|HttpEntity|HttpHeaders"
    r"|ResponseEntity|HttpMethod|FeignClient|WebTarget)\b"
)
```

The call-site pattern **does** match `client.post(`. The guard does **not** match the file, because a fully custom wrapper names no Spring or JDK HTTP type — the HTTP concern is encapsulated in another module entirely. The guard is correct in intent (it is what makes a `\b\w*[Cc]lient\.` pattern safe to run fleet-wide) but its evidence vocabulary only recognises *direct* use of a known HTTP library.

The Python guard (`http_out.py:61`) has the same shape and the same blind spot:

```python
_PY_HTTP_FILE_RX = re.compile(
    r"\b(?:requests|httpx|aiohttp|urllib3|urlopen|HTTPConnection)\b"
    r"|base_url|raise_for_status|\bSession\s*\("
)
```

### Proposed fixes

Three options, best combined:

**2a. Broaden the guard vocabulary with transport-agnostic HTTP evidence.** A file that references a route literal, HTTP status handling, or auth headers is doing HTTP regardless of the library underneath:

```python
_JAVA_HTTP_FILE_RX = re.compile(
    r"\b(?:RestTemplate|WebClient|OkHttpClient|HttpClient|HttpEntity|HttpHeaders"
    r"|ResponseEntity|HttpMethod|FeignClient|WebTarget"
    # Transport-agnostic signals: an in-house wrapper still names routes,
    # status codes and auth headers even when it hides the HTTP library.
    r"|MediaType|HttpStatus|Authorization|Bearer)\b"
    r'|["\']/(?:v\d+/)?[a-z][\w\-]*["\']'      # a quoted route literal
)
```

**2b. Let a path-constant node in the same file satisfy the guard.** `extract_path_constants` already runs before `extract_http_outbound` in `extractors/unified.py`, so `ctx.nodes` is populated by the time the guard is evaluated. A file containing a route-like constant *and* a `<receiver>.post(` call is strong combined evidence, and this reuses work already done rather than adding another regex:

```python
def _file_has_route_constant(ctx) -> bool:
    return any(n.family == "path-constant" for n in ctx.nodes)
```

Treat the guard as satisfied when either the regex matches or a path constant is present. **This is the option to prefer:** it is evidence-based rather than vocabulary-based, so it does not need extending every time a new HTTP library appears.

**2c. Document the manual escape hatch.** A known wrapper can be declared as a binding `class_patterns` entry, which runs in the *unguarded* tier (see §4). This works today with no code change and should be documented as the intended remedy for in-house wrappers.

### Suggested test

A fixture file containing only a route constant, a `client.post(ROUTE, ...)` call, and no HTTP-library identifier should yield exactly one `http-out` node.

---

---

## OI-3 — Dependency parsing misses Gradle version catalogs

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `2585fe0`, `099d018` (PR #6)  
**Tests:** `tests/test_gradle_version_catalogs.py` (15 cases), fixture repo `fulfilment/catalog-consumer`

**What changed:** Both catalog forms are parsed — the `[libraries]` TOML table and the `library(...)` settings DSL — and `libs.<alias>` references resolved against them, with the resolved pair going through the existing `is_internal_coordinate` classification. `_collect_dependencies` now returns `(deps, notes)`; an unresolved reference appends a note naming the count.

**Deviation from the proposed fix:** **Three, all hardening.** Every quantifier is bounded (the proposed `\{[^}]*module` was not); reads go through `safe_read_text` and `is_skipped_path`; catalog files are capped per repo.

**Behaviour change:** Repos using version catalogs now report `dependencies_internal`, so they contribute candidates to api-client discovery for the first time.

---

_The original issue, verbatim:_


**Severity:** Medium — silently zeroes the input to client discovery for affected repos.

### Symptom

A repo whose imports clearly show it consuming an internal client library reports `"dependencies_internal": []`. Because the discovery pass (§4) is driven entirely by `dependencies_internal`, such repos contribute no candidates at all.

### Root cause

Two gaps combine.

**The regex only recognises inline coordinate strings.** `build_metabase_v2.py:133`:

```python
_GRADLE_DEP_RX  # matches: implementation("group:artifact:version")
```

Version-catalog usage is an accessor reference with no coordinate in the file:

```kotlin
// build.gradle.kts
implementation(libs.warehouseserviceclient)
```

**The file holding the coordinates is never read.** `_collect_dependencies` (`build_metabase_v2.py:149-165`) globs `pom.xml`, `build.gradle`, `build.gradle.kts` and `package.json`. Catalogs live in `gradle/libs.versions.toml`, or are declared inline in `settings.gradle.kts`:

```kotlin
// settings.gradle.kts
library("warehouseserviceclient", "com.example.commerce.warehouse", "warehouse-service-client")
    .version("3.0.2")
```

```toml
# gradle/libs.versions.toml
[libraries]
warehouseserviceclient = { module = "com.example.commerce.warehouse:warehouse-service-client", version = "3.0.2" }
```

Neither form is parsed.

### Proposed fix

Add a catalog resolution step, then map aliases used in `build.gradle*` back to coordinates:

```python
_CATALOG_TOML_RX = re.compile(
    r'^\s*([A-Za-z0-9_\-]+)\s*=\s*\{[^}]*module\s*=\s*["\']'
    r'([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+)["\']',
    re.MULTILINE,
)
_CATALOG_DSL_RX = re.compile(
    r'library\(\s*["\']([A-Za-z0-9_\-]+)["\']\s*,\s*["\']([A-Za-z0-9_.\-]+)["\']'
    r'\s*,\s*["\']([A-Za-z0-9_.\-]+)["\']',
)
_CATALOG_REF_RX = re.compile(
    r"\b(?:implementation|api|compile|runtimeOnly)\s*\(\s*libs\.([A-Za-z0-9_.]+)\s*\)"
)


def _parse_version_catalog(repo_root: Path) -> dict[str, tuple[str, str]]:
    """Map catalog alias -> (groupId, artifactId) from TOML and settings DSL."""
    catalog: dict[str, tuple[str, str]] = {}
    for toml in repo_root.rglob("*.versions.toml"):
        text = safe_read_text(toml) or ""
        for alias, gid, aid in _CATALOG_TOML_RX.findall(text):
            catalog[_normalise_alias(alias)] = (gid, aid)
    for settings in repo_root.rglob("settings.gradle.kts"):
        text = safe_read_text(settings) or ""
        for alias, gid, aid in _CATALOG_DSL_RX.findall(text):
            catalog[_normalise_alias(alias)] = (gid, aid)
    return catalog


def _normalise_alias(alias: str) -> str:
    """Gradle exposes `my-lib` / `my.lib` in the catalog as `libs.myLib`."""
    return alias.replace("-", "").replace(".", "").replace("_", "").lower()
```

Call it once per repo in `_collect_dependencies` and resolve any `libs.<alias>` references found in `build.gradle*` against it. Apply the existing `is_internal_coordinate` classification to the resolved pair so internal/external tagging is unchanged.

Note the alias normalisation: Gradle maps `warehouse-service-client` in the catalog to `libs.warehouseServiceClient` in the build script, so a case- and separator-insensitive key is required.

### Related observation

This and the original empty-bindings defect share a failure mode: **a detection input degrading to empty without saying so.** Consider emitting a repo `note` when a `build.gradle*` contains `libs.` references but no catalog was resolved — the same "no silent caps" principle already applied to oversized-file skips.

---

---

## OI-4 — Client discovery is single-direction and never proposes `class_patterns`

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `1bd90d5`, `636476c` (PR #9)  
**Tests:** `tests/test_demand_side_discovery.py` (12 cases, incl. both safeguards and the unmatched-call-site metric)

**What changed:** A demand-side pass mines call sites that resolve to a known service in a repo declaring no client library for it, enriching the supply-side candidate where one exists and creating a call-site-only candidate where none does. It reuses the OI-1-corrected path matcher, proposes `class_patterns` from the enclosing class, and records `discovery_method` so agreement between the two directions is visible.

**Deviation from the proposed fix:** None on the design — sequential, enriching, both safeguards implemented. One limitation the section did not anticipate: the enclosing class is taken from the *file stem*, because the aggregation phase holds the metabase rather than the sources. Exact for Java and Kotlin, a proposal elsewhere.

**Behaviour change:** `api-clients.discovered.json` gains `discovery_method` and, where a proposed `class_pattern` is too common across the fleet, a `warnings` list. Candidates now appear for repos that declare no client dependency at all. Nothing is auto-merged; every candidate is still reviewed before promotion.

---

_The original issue, verbatim:_


**Severity:** Medium (capability gap). This section also answers "could discovery run from the other direction?" — yes, and the two directions are complementary rather than redundant.

### Current behaviour

`aggregators/api_client_discovery.py` mines in one direction only, which can be called **supply-side**:

> a consumer declares a dependency on an artifact whose id looks like a client library → resolve that coordinate to the publishing repo → take candidate paths from that repo's `http-in` nodes.

Two consequences:

1. **`class_patterns` is always empty.** `_collect_candidates` hardcodes `"class_patterns": []`. The field appears in `_TUNABLE_FIELDS` — it is preserved once a reviewer edits it, but never proposed. Since `class_patterns` is the mechanism that catches call sites carrying no URL, discovery cannot generate the field that most needs generating.
2. **A caller with no client library is structurally invisible.** A repo that hand-rolls HTTP (§2) has no `*-client` dependency to mine. Supply-side discovery cannot reach it by construction — no amount of dependency parsing finds a dependency that does not exist.

### Proposed: add demand-side discovery

Mine the opposite direction:

> a call site that resolves to a known service, but whose repo declares no client library for it → propose a binding, or enrich an existing one, describing how that call site is recognised.

Evidence already present in the metabase — no new extractor required:

| Signal | Source | Yields |
|---|---|---|
| Unmatched outbound call sites | `graphs/service-call-unmatched.jsonl` | the work queue |
| Route constants | `path-constant` nodes | candidate `paths` |
| Resolvable hosts | `http-out` node `detail.host` | candidate `service_aliases` |
| Deployment hostnames | `graphs/helm-service-hosts.jsonl` | candidate `service_aliases` |
| Config base-URLs | config extractor nodes | candidate `service_aliases` |
| **Service name as a literal** | any string literal equal to a known repo name or alias | high-confidence `target_repo` |
| Enclosing class of the call site | `http-out` node `file` + nearest class declaration | candidate `class_patterns` |

The last two rows are the valuable ones. A string constant whose value equals a known service name — a token audience, a config key, a queue name — is unusually strong evidence, and it is exactly the sort of marker that survives in hand-rolled clients which have no other identifying feature. The enclosing class name is precisely the `class_patterns` value a reviewer would otherwise have to derive by hand.

### Parallel or sequential?

**Sequential, with the demand-side pass enriching the supply-side output.** The two passes are not symmetric competitors; they produce *different fields for the same candidate*:

| Field | Supply-side | Demand-side |
|---|---|---|
| `target_repo` | coordinate → identity index | route / host / name-literal match |
| `maven_artifact` | authoritative | may not exist |
| `import_prefix` | from groupId | — |
| `paths` | target's `http-in` nodes | caller's route constants |
| `service_aliases` | derived from repo name | observed hosts |
| `class_patterns` | **always empty** | enclosing class |

Running them in parallel yields two candidate sets that must be merged anyway, and the merge key is ambiguous for demand-side-only candidates — there is no artifact id to key on. Running demand-side second lets it do a keyed lookup:

```
supply-side pass
  → candidates keyed by (target_repo, artifact)

demand-side pass
  → for each unmatched or weakly-matched call site:
      resolve target_repo
      if a candidate exists for that target:   enrich it
          - append observed service_aliases
          - append proposed class_patterns
          - union observed paths
          - upgrade confidence: both directions agree
      else:                                    create a new candidate
          - key (target_repo, "<hand-rolled>")
          - maven_artifact: "" and import_prefix: "" (there is none)
          - status: pending, flagged as call-site-only
```

Neither pass is expensive — both run in the aggregation phase with the fleet already in memory — so parallelism buys little wall-clock and costs merge complexity. Sequence for correctness, not speed.

### Confidence from agreement

Record how each candidate was found and score agreement explicitly:

```python
entry["discovery_method"] = "dependency" | "call-site" | "both"
```

`both` is materially stronger than either alone: a declared dependency *and* an observed call site resolving to the same service are independent lines of evidence. Conversely, `call-site` alone should sort lowest, since it rests on the path matching that §1 shows can be wrong.

### Two safeguards this needs

**Proposed `class_patterns` must be checked for distinctiveness.** Binding class patterns run in an **unguarded, language-agnostic tier** (`extractors/regex_extractors.py:257-259` — `language="any"`, no file guard, plain substring match after `re.escape`). A proposal such as `Client`, `ApiClient` or `ServiceGateway` would match across the fleet and manufacture phantom edges. Discovery should compute each proposal's corpus-wide occurrence and refuse or flag broad ones:

```python
MAX_PATTERN_REPOS = 3

occurrences = _repos_containing_literal(records, proposed_class)
if len(occurrences) > MAX_PATTERN_REPOS:
    entry.setdefault("warnings", []).append(
        f"class_pattern {proposed_class!r} appears in {len(occurrences)} repos; "
        "too generic to be safe — narrow it before accepting"
    )
```

**Guard against self-confirmation.** Demand-side discovery resolves targets by matching against routes and aliases that promoted bindings already influence. Once a binding is promoted, the edges it creates must not be re-ingested as fresh evidence for itself, or confidence inflates on every run. Record evidence provenance and exclude nodes whose `target_repo` was stamped by a binding — `detail.target_repo_evidence` already distinguishes these — from the demand-side input set.

### Why this closes a loop

`service-call-unmatched.jsonl` (added in 1.1.0) is both the input to demand-side discovery and the natural measure of its success: every accepted candidate should remove entries from it. That gives the discovery pass a regression metric it currently lacks — *unmatched call sites trending to zero* — rather than only a count of candidates produced.

---

---

## OI-7 — The `sql` family matches on method name alone

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `fbe967f`, `1339b60` (PR #5)  
**Tests:** `tests/test_sql_sink_evidence.py` (24 cases), snapshot `java-stock-proxy` (asserts an *absence*)

**What changed:** `extract_call_receiver` surfaces the receiver every grammar already exposes but that was being discarded. A SQL-verb match now needs one positive signal: a database receiver (matched on the trailing identifier's word tokens and adjacent pairs), a library hint in the call text, or file-level SQL evidence — a SQL keyword in a string literal, or a database import.

**Deviation from the proposed fix:** None on the fix itself. Note that file-level evidence is deliberately *never* the bare token `sql`: the proxy fixture has a `sql` field and no SQL, and a looser reading re-admits exactly the case the issue exists to eliminate.

**Behaviour change:** `sql` and `raw-code-payload` counts fall fleet-wide. This is withdrawn false positives, not lost coverage — it reads as a regression on a dashboard. `detail.receiver` added to `sql` sinks.

---

_The original issue, verbatim:_


**Severity:** High — produces *wrong* output, and unlike §1 the wrong output is a security finding.

Sections 7–9 come from a separate review of the SQL families rather than the fleet-scan investigation in §0. Every output quoted below is real `extract_from_file` output on 1.1.0, not a reconstruction.

### Symptom

Calls that have nothing to do with SQL are catalogued as unparameterised SQL execution sinks at `high` confidence:

```
httpClient.execute(request)     -> sql sink, confidence=high, execution=True, parameterised=False
messageDigest.update(data)      -> sql sink, confidence=high, execution=True, parameterised=False
call.execute()                  -> sql sink, confidence=high, execution=True, parameterised=False
```

### Root cause

`extractors/patterns.py:9-10` places the bare verbs `execute`, `query` and `update` in `SQL_SINK_NAMES`, and `ts_extractors.py:17` decides on the method name alone:

```python
is_sql_call = name in SQL_SINK_NAMES or any(
    hint in call_text for hint in SQL_EXECUTION_CALL_HINTS
)
```

The receiver is not unavailable — it is discarded. `call_name_java_kotlin` (`ast_walk.py:32`) reads the AST node's `name` field and drops the sibling `object` field that holds `httpClient` / `messageDigest`.

`parameterised` compounds it (`ts_extractors.py:35`):

```python
"parameterised": "?" in call_text or ":" in call_text,
```

That is a substring test, not a placeholder test, so a call containing no SQL whatsoever is reported as **unparameterised**.

### Why this is worse than a noisy family

`execution=True` appends the node to `ctx.sql_execution_sinks`, one of the three inputs to `link_raw_code_payload_endpoints` (`ts_extractors.py:87`). A plain HTTP proxy that happens to carry a field named `sql` therefore manufactures a **`raw-code-payload` node at `high` confidence**:

```java
// fulfilment/stock-proxy — StockForwarder.java
@RestController
public class StockForwarder {
    private String sql;
    @PostMapping("/v1/forward")
    public Response forward(@RequestBody StockRequest req) throws Exception {
        return httpClient.execute(req.toHttpRequest());   // not SQL
    }
}
```

```
http-in          source high
sql              sink   high   {'symbol': 'execute', 'execution': True, 'parameterised': False}
raw-code-payload source high   {'endpoint_path': '/v1/forward', 'sink_symbol': 'execute'}
EDGE intra-file: sql payload field (line 4) on /v1/forward → execute (line 7)
```

That fabricated finding flows into `taint/raw-code-payload-endpoints.jsonl`, the `trace_batch` reports and the index counts — the tool's highest-value output. A wrong service edge misleads; a fabricated injection endpoint sends someone to audit code that was never vulnerable.

### Proposed fix

Surface the receiver (`method_invocation` carries an `object` field in the Java/Kotlin grammar; `attribute` carries one in Python), then admit a bare `SQL_SINK_NAMES` hit only on positive evidence:

* **(a) receiver vocabulary** — `jdbcTemplate`, `entityManager`, `em`, `session`, `sqlSession`, `cursor`, `conn`/`connection`, `stmt`/`statement`/`preparedStatement`, `db`, `dao`, `repository`, `tx`; case-insensitive, matched on the trailing identifier so `this.userDao` and `readOnlyJdbcTemplate` both hit;
* **(b) call-text hint** — the existing `SQL_EXECUTION_CALL_HINTS`, unchanged;
* **(c) file-level SQL evidence** — a SQL keyword **inside a string literal** (`SELECT|INSERT|UPDATE|DELETE|MERGE|UPSERT|CREATE TABLE`) **or** a database import (`java.sql`, `javax.sql`, `jakarta.persistence`, `org.springframework.jdbc`, `org.hibernate`, `mybatis`, `sqlalchemy`, `psycopg`, `pymysql`, `sqlite3`, `database/sql`, `gorm`).

**(c) must mean SQL text or a DB import — never the bare token `sql`.** The proxy above has a field named `sql` and no SQL anywhere; a looser (c) re-admits precisely the case this fix exists to eliminate.

Make `parameterised` tri-state: `True`/`False` only when a SQL literal is in scope, otherwise `"unknown"`.

### Suggested tests

* The three call sites above, in a file with no SQL evidence, yield **zero** `sql` nodes.
* `jdbcTemplate.query(SQL, …)` still yields one at `high` with `execution=True` — the recall guard.
* A bare `execute` **with** a `SELECT` literal in the file, and again with only a JDBC import, each yield one.
* The `StockForwarder` proxy yields zero `sql` **and** zero `raw-code-payload` nodes.
* One Python (`cursor.execute`) and one Go (`db.Query`) case, so the gate is not silently Java-only.

### Residual not covered

A SQL statement assembled in one file and executed in another is still invisible; signal (c) is file-scoped by design. Cross-file SQL provenance is a separate problem.

---

---

## OI-8 — SQL built by formatting produces no node at all

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `08fd682`, `37d753d` (PR #5)  
**Tests:** `tests/test_sql_source_construction.py` (14 cases), snapshot `java-stock-dao`

**What changed:** Format-function coverage added (`String.format`, `MessageFormat.format`, `.formatted`, Python `%` and `.format`), the concatenation patterns' literal body rewritten to exclude only the delimiter in use, and the template pattern now matches keyword and interpolation in either order. Patterns are generated per quote style from one bounded-literal helper.

**Deviation from the proposed fix:** **One, deliberate.** The plan proposed `high` confidence where a SQL keyword co-occurs with interpolation. Kept at `medium` for every shape: a regex pass cannot distinguish `"SELECT … " + CONSTANT` from `"SELECT … " + userInput`, and OI-10 is a standing reminder of what overstating confidence costs.

**Behaviour change:** More `sql` source nodes, including the embedded-quote concatenation shape that is the canonical injection form. At most one node per line, so overlapping patterns cannot inflate one statement into a cluster.

---

_The original issue, verbatim:_


**Severity:** High — a confirmed injection is invisible to the scan.

### Symptom

```java
// fulfilment/stock-dao — StockQueryBuilder.java
String sql = String.format("SELECT * FROM stock WHERE ref = '%s'", ref);
```

No `sql` node of any kind is produced. Measured across the common formatting constructs:

| Construct | 1.1.0 |
|---|---|
| `String.format("SELECT * FROM stock WHERE ref = '%s'", ref)` | **no node** |
| Kotlin `"SELECT * FROM stock WHERE ref = $ref"` | **no node** |
| Python `"SELECT … '%s'" % ref` | **no node** |
| Python `"SELECT … '{}'".format(ref)` | **no node** |
| `"SELECT * FROM stock WHERE ref = " + ref` | sql source, `concatenated` ✓ |
| `"SELECT * FROM stock WHERE ref = '" + ref + "'"` | **no node** |

### Root cause

`SQL_SOURCE_RX` (`patterns.py:36-41`) holds four patterns — concatenation in either direction, Python f-strings, and `${…}` templates. Three distinct defects:

1. **No format-function coverage at all.** `String.format`, `.formatted(`, `MessageFormat.format`, Python `%` and `.format` are absent.

2. **The concatenation patterns break on an embedded quote.** Their literal body is `[^"\']*`, excluding *both* quote characters, so a double-quoted literal containing `'` cannot be spanned:

   ```python
   (re.compile(r'["\'][^"\']*(?:SELECT|INSERT|UPDATE|DELETE)\b[^"\']*["\']\s*\+'), "concatenated"),
   ```

   `WHERE ref = '" + ref + "'` is the canonical injection shape, so the pattern misses exactly the case it exists for. This is likely the defect behind the reported miss, rather than the format-function gap.

3. **The template pattern requires the interpolation first.** `\$\{[^}]+\}.*(?:SELECT|INSERT|UPDATE|DELETE)` demands `${…}` *before* the SQL keyword; real templates interpolate after it (`"SELECT … WHERE ref = ${ref}"`), so it rarely fires.

### Proposed fix

Add format-function patterns (each requiring a SQL keyword inside the format string); rewrite the concatenation literal body per delimiter — `"(?:[^"\\\n]{0,400})"` and `'(?:[^'\\\n]{0,400})'` — so a double-quoted literal may contain `'`; and match keyword-and-interpolation in **either** order within one literal. Rate `high` where a SQL keyword co-occurs with interpolation of a variable, `medium` for the existing shapes.

Every pattern stays length-bounded and joins the TA-005 bounded-regex test.

### Suggested tests

* One case per row of the table above, each asserting the `pattern` label.
* `String.format("Hello %s", name)` yields nothing — widening a source pattern needs its negative.
* A SQL keyword inside a comment yields nothing.

### Residual not covered

SQL assembled across multiple statements (`sb.append("SELECT …"); sb.append(ref);`) is still missed — the patterns are single-expression.

---

---

## OI-9 — An outbound request carrying a SQL payload has no home family

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `9be4d7c`, `63674c7` (PR #8)  
**Tests:** `tests/test_sql_payload_out.py` (10 cases), fixture repo `fulfilment/query-forwarder`

**What changed:** New `sql-payload-out` family from `link_sql_payload_out`, the outbound mirror of `link_raw_code_payload_endpoints`. Requires both a payload field being *bound* — setter, builder, assignment or JSON key, not merely declared — and an outbound call in the same file to carry it. Threaded through `taint_buckets`, `taint_writers` (its own catalogue), `index_v2`, the worker counts and `SCHEMA.md`.

**Deviation from the proposed fix:** None. Both halves being required is what keeps every DTO in the fleet out of the family.

**Behaviour change:** New family in the per-repo JSON, a new `taint/sql-payload-out.{jsonl,md}` catalogue, and a `sql_payload_out` count in the index.

---

_The original issue, verbatim:_


**Severity:** Medium — a whole sink class is unrepresented.

### Symptom

```java
// fulfilment/fulfilment-commons — StockQueryForwarder.java
public class StockQueryForwarder {
    private static final String SUBMIT_URL = "/v1/query";
    public Result submit(String sqlText) {
        QueryRequest body = new QueryRequest();
        body.setSql(sqlText);
        return restTemplate.postForObject(SUBMIT_URL, body, Result.class);
    }
}
```

```
http-out       sink      high   {'path': '/v1/query', …}
path-constant  reference medium
data-class-field source  medium {'field_name': 'query'}
```

An ordinary HTTP call and nothing more, though this is a repo shipping arbitrary SQL to another service over the wire.

### Root cause

Not a bug — a missing family. `raw-code-payload` is structurally *inbound*: `link_raw_code_payload_endpoints` (`ts_extractors.py:87`) requires `ctx.http_sources`, an `http-in` node, so it only ever fires on the service that **receives** SQL. The dual — the service that **sends** it — has no representation. Such a call is neither a local `sql` sink (nothing executes here) nor an ordinary `http-out` (the payload is executable code at the far end).

Note also that `body.setSql(sqlText)` contributed no `sql` field marker: the field-name passes recognise declarations, not setter, builder or JSON-key forms.

### Proposed fix

A `sql-payload-out` family, emitted by a cross-pass linker mirroring `link_raw_code_payload_endpoints` on the outbound side: an `http-out` node in the file **and** a SQL-payload field bound into that request.

The vocabulary already exists and should be reused rather than reinvented — `RAW_SQL_PAYLOAD_FIELD_NAMES` (`vocabulary.py:130`, the strict set) ∪ the `payload_fields` of the binding that stamped the `http-out` node, where one did (`known_api_clients.py:32`, default `("sql",)`).

```
family:     sql-payload-out
kind:       sink
data_class: raw-sql-payload
detail:     {field_name, http_out_line, path, target_repo, client, evidence}
```

Confidence `high` when a binding's own `payload_fields` matched — the binding is a declaration that this service takes SQL over the wire — and `medium` on vocabulary alone.

Field detection must cover setters (`setSql(`), builders (`.sql(`), assignment (`.sql =`, `sql:`) and JSON keys (`"sql":`), not only declarations.

**This is not merely an extractor change.** A new family must be threaded through `taint_buckets.FAMILY_TO_BUCKET`, `taint_writers.py`, `index_v2.py`, `renderers/markdown.py`, the `build_metabase_v2` family counts, `trace.py` and `SCHEMA.md`. That plumbing is the bulk of the work.

### Suggested tests

* The forwarder above yields one `sql-payload-out` node referencing the `http-out` line and `/v1/query`.
* A binding declaring `payload_fields: ["dql"]` promotes a `dql` field to `high`.
* An ordinary POST with no SQL-ish field yields nothing — the precision guard.
* A data class with a `sql` field but no outbound call in the file yields nothing.
* The family reaches the aggregated taint catalogue, not just `ctx.nodes` — the test that catches half-finished plumbing.

### Residual not covered

`raw-code-payload` and `sql-payload-out` are the two ends of one cross-repo hop — *"this service accepts SQL"* and *"this service sends SQL"*. Joining them across repos in the aggregation phase is the obvious follow-on and is deliberately out of scope here.

---

---

## OI-10 — `parameterised` is reported as a safety property it cannot establish

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `33554b1` (PR #5)  
**Tests:** `tests/test_sql_sink_evidence.py` (posture cases incl. mixed, operand-order, legacy boolean)

**What changed:** `parameterised` stopped being a safety verdict and became a posture over two independent facts about the statement executed at the call site: `parameterised`, `mixed`, `raw`, `static`, `unknown`. The file-level fallback is trusted only when the file holds exactly one candidate statement, and placeholders are looked for inside string literals only.

**Deviation from the proposed fix:** **Yes.** A fifth posture, `static`, was added beyond the plan's four — a constant `DELETE FROM stock WHERE expired = true` is neither parameterised nor raw, and calling it raw would misfile a statement no input reaches.

**Behaviour change:** `detail.parameterised` changes type from boolean to string. `SCHEMA_VERSION` stays at `2`: old metabases still load and a legacy `true`/`false` is reported as `unknown` rather than translated, since that boolean came from the heuristic this removes.

---

_The original issue, verbatim:_


**Severity:** High — a false *safe* label is more dangerous than a false finding.

**Introduced by:** the OI-7 fix (commit `1339b60`, 2.0.0-dev). The 1.1.0 field was differently wrong (`"?" in call_text or ":" in call_text`); this section is about the replacement, not the original.

### Symptom

A placeholder somewhere in the file is taken as evidence that *this* call site is parameterised:

```java
// fulfilment/stock-dao — StockDao.java
public class StockDao {
    private static final String SAFE = "SELECT ref FROM stock WHERE id = ?";

    List<Stock> search(String clause) {
        String sql = "SELECT * FROM stock WHERE " + clause;   // injectable
        return jdbcTemplate.query(sql, mapper);
    }
}
```

```
sql source pattern=concatenated          <- the danger is detected ...
sql sink   parameterised=True            <- ... and then contradicted
```

The scan reports both facts about the same file and they disagree. A reviewer filtering the SQL catalogue for raw statements never sees this call site.

### Root cause

`patterns.sql_parameterisation`:

```python
statements = [m.group(1) for m in SQL_LITERAL_RX.finditer(call_text)]
if not statements:
    statements = [m.group(1) for m in SQL_LITERAL_RX.finditer(source)]   # (1)
if not statements:
    return "unknown"
return any(SQL_PLACEHOLDER_RX.search(s) for s in statements)             # (2)
```

Three distinct errors:

1. **(1) file-level fallback.** When the call executes a variable, any SQL literal anywhere in the file stands in for the statement actually executed. Unrelated code certifies this call site.
2. **(2) `any`.** One parameterised statement among several marks the call parameterised, even when the executed one is not.
3. **Concatenation is ignored entirely.** A statement may be *both* concatenated and parameterised — `"... WHERE ref = '" + ref + "' AND id = ?"` — which is injectable despite the placeholder. The presence of a placeholder is not the absence of concatenation, and only the second is a safety property.

A fourth, milder case: a `?` with no bound arguments is reported parameterised. That is usually a runtime error rather than a vulnerability, but it is the same overclaim.

Note also that error 3 is currently masked by luck. In the mixed example the literal fragments split around the concatenation, so `SQL_LITERAL_RX` matches only `"SELECT * FROM stock WHERE ref = '"` — which has no placeholder, giving the right answer for the wrong reason. Reverse the operands (`"... AND id = ? AND ref = '" + ref`) and it reports `True`.

### Proposed fix

`parameterised` should stop being a safety verdict and become a *posture* derived from two independent facts about the statement executed at the call site:

| Posture | Placeholders | Concatenation / interpolation |
|---|---|---|
| `parameterised` | yes | no |
| `mixed` | yes | yes | 
| `raw` | no | yes |
| `unknown` | — | statement not identifiable at the call site |

`mixed` is the case this section exists for and must not collapse into either neighbour.

The governing rule: **weak evidence may downgrade a posture, never upgrade it.** File-level evidence can move a call to `mixed` or `raw`; it can never establish `parameterised`, which requires the statement to be identified at the call site. Concretely, drop the file-level fallback at (1), replace `any` at (2) with a per-statement judgement, and consult the concatenation evidence the `sql` *source* pass already produces for the same file.

### Dependency

The concatenation half of this is only as good as `SQL_SOURCE_RX`, which `OI-8` shows misses `String.format`, template interpolation, and — most relevantly here — concatenation containing an embedded quote, which is exactly the shape of the mixed example. **This issue should be fixed with `OI-8`, not before it**: a posture built on today's concatenation detection would mark genuinely mixed statements `parameterised` and re-create the defect one level up.

### Why the fallback cannot simply be deleted

Constant-mediated SQL is the normal shape in Java, and the fallback is what handles it:

```java
private static final String FIND = "SELECT ref FROM stock WHERE id = ?";
List<Stock> find(long id) {
    return jdbcTemplate.query(FIND, mapper, id);   // call text holds no literal
}
```

Deleting (1) makes this `unknown` and loses a correct verdict. The proper fix is **symbol resolution** — the same shape as `build_path_symbol_table` in `extractors/http_out.py`, which already resolves `host + SUBMIT_PATH` for outbound paths — mapping the identifier at the call site to the literal it was assigned. That is what turns a file-level guess into a statement-level fact.

### Interim

A narrower change gets the false *safe* label out without waiting for symbol resolution: **keep the fallback only when the file shows no concatenation evidence.** If a file contains a concatenated SQL statement, no unattributed literal in it may certify a call site, so the posture becomes `unknown`. The `search` example above is then `unknown` while the `FIND` example stays `parameterised`.

### Suggested tests

* The `search` example above: posture is not `parameterised`. This is the symptom test.
* `"... WHERE ref = '" + ref + "' AND id = ?"` → `mixed`, and the same statement with the operands reversed → also `mixed` (the luck-masking case).
* A file containing one safe constant and one built statement: each call site is judged on its own statement.
* A call executing a variable that cannot be resolved → `unknown`, never `parameterised`.
* A genuine `jdbcTemplate.query("SELECT ... WHERE id = ?", mapper, id)` → `parameterised`. The recall guard.

### Residual not covered

Whether the bound arguments actually match the placeholders, and whether a `PreparedStatement`'s `setX` calls are ever made, both need dataflow the extractor does not have. `mixed` and `unknown` are the honest labels for what a regex pass can establish.

A statement whose SQL keyword lives in a constant and whose *appended* fragments carry the user input is missed entirely — see `OI-11`.

---

---

## OI-11 — A base-query constant hides the concatenation appended to it

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `ce7e6fd`, `3d10d27` (PR #5)  
**Tests:** `tests/test_sql_source_construction.py` and `tests/test_sql_sink_evidence.py` (constant-mediated cases)

**What changed:** A per-file identifier → SQL-literal map, built by the mechanism `http_out.build_path_symbol_table` already used for constant-mediated URLs. A SQL constant taking part in a concatenation now counts as construction, which emits the missing `sql` source node *and* moves the posture to `mixed` — one resolution feeding both consumers. The shared machinery moved to `extractors/symbols.py` behind a predicate rather than being copied.

**Deviation from the proposed fix:** None. Resolution fires only on constants joined by `+`, which is what preserves OI-10's constant-mediated `parameterised` posture.

**Behaviour change:** The base-query-plus-clause shape now produces a finding and reports `mixed` rather than `parameterised`.

---

_The original issue, verbatim:_


**Severity:** High — misses a common injection shape *and* labels it safe.

**Found:** while fixing `OI-10`; not closed by `OI-8` or `OI-10`.

### Symptom

The "base query plus conditional clause" shape, which is how most hand-written Java DAOs build a filtered query:

```java
// fulfilment/stock-dao — StockDao.java
public class StockDao {
    private static final String SAFE = "SELECT ref FROM stock WHERE id = ?";

    List<Stock> find(String ref, long id) {
        String sql = SAFE + " AND ref = '" + ref + "'";
        return jdbcTemplate.query(sql, mapper, id);
    }
}
```

Measured:

```
keyword-bearing literals in file: ['SELECT ref FROM stock WHERE id = ?']
file detected as constructing SQL: False
sql source nodes:                 []
sink posture:                     parameterised
```

Both halves are wrong. The concatenation produces **no `sql` source node**, and the sink is then labelled **`parameterised`** — the safe posture — on the strength of the base constant's `?`.

### Root cause

One cause, two symptoms. Every pattern in `SQL_SOURCE_RX` anchors on a SQL keyword *inside the literal adjacent to the operator*:

```python
(re.compile(rf"{lit}{q}\s*\+"), "concatenated"),   # "SELECT …" +
(re.compile(rf"\+\s*{lit}"), "concatenated"),      # + "SELECT …"
```

Here the keyword is in `SAFE`, and the fragments actually concatenated — `" AND ref = '"`, `"'"` — carry none. Nothing matches, so:

* no `sql` source node is emitted (`OI-8`'s widening does not help: the shapes it added are also keyword-anchored); and
* `sql_parameterisation` sees no construction, finds exactly one candidate statement, and attributes it — yielding `parameterised` (`OI-10`'s single-candidate rule is satisfied, because the *other* fragments were never candidates).

`OI-10` is therefore correct as specified and still wrong in this case: its guard against misattribution counts only keyword-bearing literals.

### Proposed fix

**Symbol resolution**, which `OI-10` already identifies as the proper remedy for its own fallback. `extractors/http_out.py` has the pattern to copy: `build_path_symbol_table` maps identifier → endpoint-like literal per file, and `_resolved_symbol_text` substitutes the identifiers referenced near a call so a constant-mediated path resolves.

The SQL equivalent:

1. build a per-file map of identifier → SQL-shaped string literal (the same shape as `build_path_symbol_table`, with a SQL-literal predicate in place of `_ENDPOINTISH_RX`);
2. when an expression concatenates an identifier that resolves to a SQL statement, treat the whole expression as the statement — so `SAFE + " AND ref = '" + ref` is a constructed SQL statement;
3. feed that resolved statement to both `extract_sql_string_sources` (emitting the missing `sql` source) and `sql_parameterisation` (yielding `mixed`, since the base constant's `?` and the appended concatenation are both present).

Bound the map as `build_path_symbol_table` does (`_MAX_SYMBOLS_PER_FILE`), and record only SQL-shaped literals so the table stays small.

### Suggested tests

* The example above: one `sql` source node, and sink posture `mixed`.
* The same with no placeholder in the base constant → posture `raw`.
* A constant that is *not* concatenated (`jdbcTemplate.query(SAFE, mapper, id)`) → still `parameterised`. The recall guard for `OI-10`'s constant-mediated case.
* An identifier that resolves to a non-SQL literal must not make an unrelated concatenation into SQL. The precision guard.
* A constant defined in another file is out of reach — assert `unknown`, not a guess.

### Residual not covered

Cross-file constants, and a base query assembled through a `StringBuilder` across several statements, both need more than a per-file symbol map. `OI-8`'s residual (multi-statement `sb.append` construction) is the same gap seen from the other side.

---

---

## OI-12 — Four unused runtime dependencies pull in sixty packages

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `b54f74a` (PR #4, then #5)  
**Tests:** `tests/test_dependency_pinning.py::test_every_runtime_dependency_is_imported_somewhere`

**What changed:** `ipykernel`, `jupyterlab`, `openpyxl` and `pandas` removed from `[project.dependencies]` and the lock regenerated. A guard test asserts every declared runtime dependency is imported somewhere under `src2sink/`, and was confirmed to fail against the previous `pyproject.toml` before being relied on.

**Deviation from the proposed fix:** None.

**Behaviour change:** Runtime closure falls from 68 packages to 8, all MIT or PSFL — no copyleft of any kind. `fqdn` (MPL-2.0) leaves the tree entirely; `certifi` and `pathspec` remain dev-only.

---

_The original issue, verbatim:_


**Severity:** Medium — no known vulnerability today; this is attack-surface and licence-surface reduction.

**Not a detection defect.** This is the one supply-chain entry in a document otherwise about detection, kept here so the `OI-n` record stays in one place.

### Symptom

`pyproject.toml` declares `ipykernel`, `jupyterlab`, `openpyxl` and `pandas` as **runtime** dependencies. None of them is imported anywhere:

```
$ grep -rn "import pandas|import openpyxl|import jupyter|import ipykernel|import IPython" src2sink/ scripts/ tests/
(no matches)
```

There are no notebooks in the repository and no documentation referring to a notebook or spreadsheet workflow.

Measured closure of the installed environment:

| Set | Packages |
|---|---|
| Deps `src2sink` actually imports (`defusedxml`, `tree-sitter*`) | **8** |
| Closure of the four never-imported deps | **68** |

Every consumer of the library installs roughly sixty packages the tool never touches.

### Why it matters for this project in particular

1. **Audited surface.** `pip-audit` (control SC-1, artifact TA-011) audits the lockfile. Sixty unnecessary packages are sixty more advisories that can turn the build red, and sixty more chances that a compromised transitive dependency ends up inside a *security scanner* — a tool whose output people act on.
2. **Licence surface.** The only non-permissive licences in the tree arrive this way. All three MPL-2.0 packages — `certifi` (via `httpx`/`requests`), `fqdn` (via `jupyterlab`'s `jsonschema` format extras) and `pathspec` (via `mypy`, dev-only) — are outside the real closure. The eight packages actually shipped against are MIT or PSFL, with no copyleft of any kind.
3. **Install cost.** `jupyterlab` alone is a large install for a CLI that parses source files.

MPL-2.0 is weak, file-level copyleft: using an unmodified dependency imposes nothing on an MIT project, so there is no licence *problem* today. The point is that the exposure is gratuitous.

### Proposed fix

Remove all four from `[project.dependencies]` and regenerate `uv.lock`. Not moved to an optional extra: nothing in the repository uses them, so there is no feature to keep working. Should a notebook workflow appear later, `[project.optional-dependencies]` is the right home for it, not the default install.

### Suggested tests

* The existing suite must pass unchanged — nothing imports them, so nothing should move.
* `uv sync --locked` and `pip-audit --frozen` must both succeed against the regenerated lock.
* A guard test asserting the runtime dependency set stays minimal would prevent a silent re-introduction; the natural form is to assert that every distribution in `[project.dependencies]` is imported somewhere under `src2sink/`.

### Residual not covered

Package metadata does not cover the C grammars vendored inside the `tree-sitter-*` wheels, whose upstream licences have not been verified here. Dev dependencies are unaudited beyond noting that `pathspec` (MPL-2.0) arrives via `mypy`.

---

## OI-14 — Trace rebuilds the whole fleet graph for every target

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `cf38c3d` (red), `651683a` (fix) — PR #15  
**Tests:** `tests/test_trace_fleet_scaling.py` — six cases: precomputed edges change nothing, supplied edges are not rebuilt, a batch of three targets builds the graph once, the memoised segment split is immutable, both path helpers memoise, and the cache is bounded. Mutants `OI14-M1`..`OI14-M5`.

**What changed.** `_find_upstream_from_graph` now takes the edge list rather than
the records it is built from, and `run_trace` accepts a `service_edges` argument
alongside the `records` and `producer_indices` it already accepted; `trace_batch`
builds all three once. Separately, `normalize_path_template` and
`_significant_segments` are memoised into a bounded LRU, and the latter returns a
tuple because a cached result is shared between callers and must not be mutable.
The three "compute if not supplied" branches moved into
`_resolve_fleet_derivations`, both to keep `run_trace` under its frozen
complexity score and so that an unknown target still fails before paying for
either build.

**Deviation.** The obvious framing — "trace walks every repo on every
invocation" — turned out to be wrong, and measuring first is what caught it.
Loading all 300 repo records took **0.02s**; `collect_service_edges` took
**22.64s**. The file walk was never the problem. Fixing the walk would have
bought nothing.

**Behaviour change.** None. Identical reports, and the new argument is optional.

---

_This issue was found and fixed in the same PR, so unlike the entries above it
was never staged in the open-issues file. What follows was written from the
measurements rather than moved verbatim._

### Symptom

`src2sink-trace` was slow over a real fleet, and a batch run disproportionately
so — enough that batch tracing was effectively not run.

### Root cause

Two independent costs, neither visible in the output.

**Per-target recomputation.** `run_trace` called `collect_service_edges(records)`
through `_find_upstream_from_graph`, which then discarded every edge not arriving
at the target. The graph is fleet-wide and target-independent, so a batch of N
targets built the identical graph N times. `trace_batch` had already hoisted
record loading and producer indices out of its loop, which is why this one was
easy to miss — the loop *looked* hoisted.

**A quadratic graph build.** Each path lookup in
`match_path_in_inbound_index` linearly scans every indexed inbound route, and
each comparison re-normalised both route strings from scratch. Measured on a
synthetic fleet of N repos x 40 nodes:

| repos | before | after |
|---|---|---|
| 50 | 0.60s | 0.14s |
| 100 | 2.41s | 0.55s |
| 200 | 9.45s | 2.19s |
| 400 | 38.28s | 9.15s |

A clean 4x per doubling in both columns. `cProfile` at 150 repos attributed it to
`path_templates_match` (2.25M calls) driving `normalize_path_template` (4.5M
calls) over the same few thousand route strings.

### Measured effect

10 traces over a 200-repo fleet: **22.05s → 0.04s**. The graph build itself is
**4.2x** faster and *still quadratic* — memoisation makes each comparison
cheaper, it does not remove the scan. Removing the scan needs the keyed index
described in `OI-15`.

---


## OI-16 — A detection fix never reaches a repo that has not changed

### Resolution

**Fixed in:** 2.0.0  
**Commit:** `f17fcbc` (red), `6779191` (fix) — PR #19  
**Tests:** `tests/test_detection_version.py` (record names its detector; unchanged repo still skipped; older *and* absent detector both force a rescan; the pre-`OI-7` false sink is replaced), `tests/test_detection_fingerprint_gate.py` (the gate itself), `tests/test_build_internals.py::test_existing_record_is_current`. Mutants `OI16-M1`, `OI16-M2`.

**What changed.** `DETECTION_VERSION` joins `SCHEMA_VERSION` in `schema.py` and is
written onto every record; `_read_existing_sha` became
`_existing_record_is_current`, which requires the sha **and** the detector to
match before a repo is skipped. A record with no `detection_version` is treated
as stale rather than current — it predates the field, so what produced it is
unknowable, and assuming the running detector is exactly how this survived six
releases.

`scripts/detection_version_check.py` is what makes the version trustworthy: it
fingerprints the detection inputs by content and freezes them against the
version, so changing an extractor without a bump fails the build. Without it the
fix degrades to "remember to bump it", which is the same silent-discipline
failure in a new place.

**Deviation.** The issue proposed a CI gate keyed on `git diff` against the base
branch. Rejected on inspection of the workflow: `actions/checkout` runs at
depth 1, so a diff-based gate would either need a full fetch or degrade to
passing when it could not resolve a base ref — and a gate that silently passes
is worse than no gate, because it is believed. The content fingerprint behaves
identically locally and in CI, and reuses the freeze/`--update` shape the
complexity ratchet already established.

Also extended beyond the issue: `scripts/` now enters the mutation sandbox. The
gates are load-bearing code, and a mutated gate that still passes means the gate
was decorative.

**Behaviour change.** The first build after upgrading rescans **every**
repository, because no existing record carries a `detection_version`. That is a
one-off cost and should be planned rather than discovered. After it, findings
produced by superseded detectors disappear — most visibly the false `sql` sinks
from `OI-7`, which until now persisted on any repo that had not committed since.

---

_The original issue, verbatim:_

**Severity:** High — every detection fix this project has shipped is affected,
and the failure is silent in both directions: the stale finding looks current,
and the fix looks applied.

**Found:** while designing metabase versioning
([`docs/plans/metabase-versioning-design.md`](../plans/metabase-versioning-design.md)),
not by anyone reporting a wrong result — which is the point.

### Symptom

Measured. A repo containing `httpClient.execute(req)`, with a prior record
holding the false `sql` sink that `OI-7` removed, scanned by a build that
*contains* the `OI-7` fix:

```
scan result: {'_skipped': True, 'group': 'grp', 'name': 'svc'}
nodes still on disk: [('sql', 'execute')]
analysed_at still: 2025-01-01T00:00:00+00:00
record names the tool that made it: False
```

The false sink survives the fix that removed it, indefinitely, for as long as the
repository does not happen to commit.

### Root cause

`build_metabase_v2.py:409-412` skips re-analysis when the repo's current git sha
matches the sha in the existing JSON:

```python
if not force:
    current_sha = detect_git_sha(repo_root)
    if current_sha and current_sha == _read_existing_sha(json_path):
        return {"_skipped": True, "group": group, "name": name}
```

The skip is keyed on **what was scanned** and not at all on **what scanned it**.
A record's content is a function of both, so the cache key is missing half its
inputs. Nothing detects this afterwards, because the record does not record which
version of `src2sink` produced it — `tool_version` exists only in
`run-manifest.json`, describing the *run*, not the contents.

`schema_version` is not a substitute. It is checked on load, so a schema bump does
force a rebuild — but every detection fix so far (`OI-1`, `OI-2`, `OI-7`..`OI-12`)
changed extraction output *within* schema 2 and therefore did not.

### What is lost

* **Detection fixes do not land fleet-wide.** The improvements recorded against
  each closed issue describe the repos that were rescanned, not the fleet.
* **Detector semantics mix silently within one metabase.** A record written
  before `OI-10` carries `parameterised: false`; one written after carries
  `parameterised: "mixed"`. Aggregations run across both without noticing.
* **The manifest misleads.** It stamps the current `tool_version` over a fleet
  that was mostly produced by earlier versions.

This is the cross-cutting shape §6 already names — a detection input that
resolves to nothing without saying so. Here the input is "the detector that
produced this record", and it resolves to unknown.

### Proposed fix

1. Record the detector identity on every repo record — a `detection_version`
   field, distinct from the package version so that a docs-only release does not
   invalidate the fleet.
2. Include it in the skip key: skip only when the sha **and** the detection
   version both match.
3. Treat a record with no `detection_version` as stale, since we genuinely cannot
   know what produced it. This forces one full rescan on upgrade, which should be
   announced rather than discovered.
4. Gate the version in CI, in the same family as the existing ratchets: fail the
   build when anything under `src2sink/extractors/` (or the pattern, vocabulary
   and binding inputs) changes without a `detection_version` bump. Without the
   gate this fix degrades to "remember to bump it", which is the failure mode it
   exists to remove.

See §6 of the design document for why a hand-maintained version plus a gate is
preferred to hashing the extractor sources.

### Suggested tests

* A repo whose sha is unchanged but whose record carries an older
  `detection_version` **is** rescanned.
* A repo whose sha and detection version both match is skipped — the existing
  incremental behaviour must survive.
* A record with no `detection_version` is treated as stale.
* The CI gate fires on an extractor change with no bump, and does not fire on a
  change elsewhere. This is the test that matters: the gate is the fix.
* Regression: a record holding a pre-`OI-7` false `sql` sink is replaced, not
  preserved, by a build containing the fix — the exact scenario measured above.

### Residual not covered

Version-skew between a consumer and the provider it pins is a different problem
with a different fix; see §7 of the design document. This issue is only about the
metabase drifting from the *tool*.

## OI-24 — The equality shortcut bypasses the significant-segment filter

### Resolution

**Fixed in:** 2.1.0  
**Commit:** `e09215f` (red), `45c2ca3` (fix) — PR #23  
**Tests:** `tests/test_path_match_significance.py::test_a_path_that_names_nothing_never_matches_itself` (all eight version and layer segments), plus the preservation cases. Mutant `OI24-M1`.

**What changed.** The significant segments are computed and the emptiness guard
applied **before** the equality check, so `high` now means "the same meaningful
route" rather than "the same string". The ladder itself moved into
`_structural_match`, unchanged.

**Deviation.** None — the fix is the one the report implied. Worth recording why
it survived `OI-1` at all: `OI-1` added the guard, and the equality shortcut sat
above it, so the guard was correct and simply never reached on the one path where
both sides were identical.

**Behaviour change.** Edges resolved through two identical meaningless paths
disappear. Real routes are unaffected; `/stock` to `/stock` is still `high`.

---

_The original issue, verbatim:_

**Severity:** High — reinstates `OI-1` for every pair of identical meaningless
paths, at `high` confidence.

**Found:** reported from a separate review session; confirmed by measurement.

### Symptom

```
  /v1       vs /v1       -> high    significant: () / ()
  /api      vs /api      -> high    significant: () / ()
  /service  vs /service  -> high    significant: () / ()
  /v1       vs /v1/stock -> None    significant: () / ('stock',)
```

Two repositories each exposing a bare `/v1` produce a `high`-confidence
cross-repo edge — the exact defect `OI-1` was raised to remove. The last line
shows the guard working when the paths are *not* identical, which is what hid
this: only the equality case leaks.

### Root cause

`path_templates_match` returns before it asks whether either side names
anything:

```python
if o == i:
    return "high"          # <-- returns here

op = _significant_segments(o)
ip = _significant_segments(i)
if not op or not ip:       # <-- the guard OI-1 added, never reached
    return None
```

The shortcut was presumably added as an optimisation — identical strings are
obviously the same route. They are not: `/v1` is the same *string*, and no route
at all.

### Proposed fix

Compute the significant segments and apply the emptiness guard **before** the
equality check. Equality then means "the same meaningful route", which is what
`high` is supposed to assert.

### Suggested tests

* Every generic and version segment fails to match itself: `/v1`, `/v2`, `/api`,
  `/rest`, `/internal`, `/public`, `/service`, `/services`.
* Real routes still match exactly: `/stock` vs `/stock` stays `high`.
* `/orders/{id}` vs `/orders/{ref}` stays `high` — normalisation makes them the
  same route, and that is a genuine equality.

## OI-25 — Placeholder and operation-verb segments are treated as destinations

### Resolution

**Fixed in:** 2.1.0  
**Commit:** `e09215f` (red), `45c2ca3` (fix) — PR #23  
**Tests:** `tests/test_path_match_significance.py` — placeholder cases, the verb cap, and `test_a_real_route_that_looks_like_a_verb_survives`. Mutants `OI25-M1`, `OI25-M2`.

**What changed.** The collapsed path parameter `{}` is filtered exactly like
`/api`, because it names nothing under any circumstances. Operation verbs are
**not** filtered; instead a match whose significant segments are entirely verbs
is capped at `low`.

**Deviation — and it is the substance of the fix.** The report named
"template-only and CRUD-verb segments" as one problem. Measurement split it in
two: `/v1/query` reduces to `('query',)` and is a real route in the
`test_sql_payload_out` fixtures, so filtering verbs out would have traded false
edges for missing ones. Capping preserves recall while removing the false
confidence — which is also the right shape for an indicator, per
[`observe-then-classify.md`](../plans/observe-then-classify.md) §7.

**Behaviour change.** `/{id}` no longer matches `/{name}`. Verb-only matches drop
from `high` to `low` rather than disappearing. `/orders/create` and
`/orders/delete` stop matching each other — different endpoints, and conflating
them was never intended.

---

_The original issue, verbatim:_

**Severity:** High — same class as `OI-24`, and the two compound: both defects
surface through the equality shortcut.

**Found:** reported from a separate review session; confirmed by measurement.

### Symptom

```
  /{id}     vs /{name}   -> high    significant: ('{}',)     / ('{}',)
  /create   vs /create   -> high    significant: ('create',) / ('create',)
  /search   vs /search   -> high    significant: ('search',) / ('search',)
```

A placeholder names nothing at all; an operation verb names what you are doing
rather than what you are addressing. Both are exactly the argument that removed
`/api` and `/service` in `OI-1`.

### Root cause

`_significant_segments` drops version segments and a fixed set of layer names.
It does not drop the collapsed path parameter `{}` that
`normalize_path_template` produces, and it has no notion of an operation name.

### Proposed fix — and why the two halves differ

**Placeholders are dropped.** `{}` names nothing under any circumstances, so it
is filtered exactly like `/api`.

**Operation verbs are *not* dropped.** This is the part that needs care:
`/v1/query` reduces to `('query',)` and is a real route of a real query service —
the fixture in `tests/test_sql_payload_out.py` uses it. Filtering verbs out would
delete legitimate endpoints, replacing false edges with missing ones.

Instead, a match whose significant segments are **entirely** operation verbs is
capped at `low`. Two services both exposing `/search` is weak evidence, not no
evidence, and `low` is what the confidence ladder already means by that.

### Behaviour change worth stating

`/orders/create` and `/orders/delete` stop matching each other. They are
different endpoints on the same service, and conflating them was never intended —
but any consumer relying on the previous behaviour would see those edges
disappear.

### Suggested tests

* `/{id}` vs `/{name}` does not match; `/orders/{id}/lines` still reduces to
  `('orders', 'lines')` and matches its equivalent at `high`.
* Each bare operation verb matches itself at `low`, never `high`.
* `/v1/query` still resolves to `('query',)` and still matches `/query`.
* A verb alongside a resource keeps full strength: `/orders/create` vs
  `/orders/create` is `high`, and `/orders/create` vs `/users/create` is `None`.

### Residual not covered

The operation-verb list is a fixed vocabulary and will not cover every project's
naming. A verb that is genuinely a resource name in some service (`/search` as a
search *service*) is capped to `low` rather than lost, which is the deliberate
trade: recall is preserved, confidence is not overstated.

## OI-26 — File-scoped SQL evidence admits every sink-named call in the file

### Resolution

**Fixed in:** 2.1.0  
**Commit:** `13c0444` (red), `feba873` (fix) — PR #28  
**Tests:** `tests/test_oi26_receiver_scope.py` (13 cases: four non-database receivers, the `ps`/`pstmt` vocabulary, the receiver-less rescue, the library-hint override, and the fabricated endpoint). Mutants `OI26-M1`..`OI26-M3`.

**What changed.** The three evidence signals are no longer interchangeable.
`_has_sql_evidence` orders them by how *local* they are: a library hint settles
it outright, a database receiver is evidence about this call, and file evidence —
a fact about other code in the same file — rescues only a call whose receiver is
**unknown**. A receiver recognised as another kind of boundary is negative local
evidence, and a fact about the neighbours cannot overturn it.

**Deviation, and it matters.** The issue proposed that file evidence "should
rescue only calls with no receiver". Implementing that failed two existing tests
by name — `test_sql_literal_in_file_admits_an_unknown_receiver` and
`test_database_import_in_file_admits_an_unknown_receiver`. `runner.execute(STATEMENT)`
has an unknown receiver and *should* be rescued; that recall was deliberate.

The distinction the fix actually needs is between **unknown** and **known to be
something else**, which required naming the other boundaries rather than
enumerating everything that is not a database. `NON_DATABASE_RECEIVER_NAMES` is
that vocabulary — positive knowledge of the same kind as `SQL_RECEIVER_NAMES`,
and the thing `OI-20` generalises. `mapper` is deliberately absent: a MyBatis
mapper genuinely is a database receiver.

The second half was as specified: `ps`, `pstmt` and `cstmt` joined the receiver
vocabulary, without which tightening would have withdrawn real
`PreparedStatement` sinks.

**On the observation layer.** This was the first classification fix made after
it, and the fix is confined to `_sql_verdict`/`_has_sql_evidence`, provably
needing no source — `tests/test_sql_classifier.py::test_the_classifier_needs_no_source`
now demonstrates the corrected behaviour from observations alone. **It is not yet
a re-aggregation**, because the classifier still runs during extraction, so the
version still bumps and the fleet still rescans. Making it an actual
re-aggregation needs the classifier moved to the aggregation phase, which is
blocked on `link_raw_code_payload_endpoints` moving with it.

**Behaviour change.** `sql` sinks disappear where the receiver reads as an HTTP
client, digest, executor, cache or logger — and any `raw-code-payload` endpoint
built on one goes with it. The existing fixture corpus contains none of these,
so the regenerated fixtures are unchanged; the new tests carry the evidence
instead.

---

_The original issue, verbatim:_

**Severity:** High — `OI-7`'s residual, and it can still manufacture the
fabricated injection endpoint `OI-7` was raised to stop.

### Symptom

Measured:

| File | `file_has_sql_evidence` | `sql` sinks emitted |
|---|---|---|
| an HTTP client call alone | `False` | none — the gate works |
| **the same call, plus one real SQL query elsewhere in the file** | `True` | **`httpClient.execute`** *and* `jdbcTemplate.query` |

`receiver_is_database('httpClient')` is `False` — the receiver is known not to be
a database — but file evidence overrides it.

### Root cause

The evidence terms are OR'd and one of them is file-scoped, so the weakest term
decides once satisfied:

```python
if not (has_hint or receiver_is_database(receiver) or file_sql_evidence):
    return
```

One real SQL statement anywhere in a file admits every sink-named call in that
file. `OI-7` replaced "name alone" with "name OR three evidence terms", and file
scope is too coarse for a call-level decision — the same scope error as using a
repo-level manifest to justify a file-level finding.

Because an execution sink feeds `link_raw_code_payload_endpoints`, this still
produces `raw-code-payload` findings for endpoints that were never vulnerable.

### Proposed fix

Two sides, and both are needed or recall drops:

1. File evidence must not override a receiver known **not** to be a database. It
   should rescue only calls with no receiver, or with a library hint in the call
   text.
2. The receiver vocabulary needs widening to compensate: `ps` and `pstmt` are
   absent while `stmt`, `conn` and `session` are present, so tightening alone
   would drop real `PreparedStatement` calls.

### Sequencing note

Under the current architecture this is a detection change and forces a fleet
rescan. Under the observation layer
([`observe-then-classify.md`](../plans/observe-then-classify.md) §3) it is a
classifier change costing a re-aggregation. That is an argument for sequencing
the observation layer first — **not** for leaving this unfixed.

### Suggested tests

* An HTTP client call in a file containing real SQL produces no `sql` sink.
* `ps.execute()` and `pstmt.execute()` still produce sinks.
* A bare `execute(sql)` with no receiver is still rescued by file evidence.
* No `raw-code-payload` node is produced for an endpoint whose only "sink" was an
  HTTP call in a SQL-bearing file.

## OI-19 — Dependency parsing covers two of nine ecosystems, and reads no lockfile

### Resolution

**Fixed in:** 2.1.0  
**Commit:** `1cdc8c7` (red), `a42c487` (fix) — PR #30  
**Tests:** `tests/test_polyglot_dependencies.py` — Go requires, internal/external classification of a module path, Python lock-vs-manifest, npm lock-vs-manifest, and the unparsed-ecosystem note. Mutants `OI19-M1`..`OI19-M3`.

**What changed.** `src2sink/dependencies.py` parses `go.mod`, `pyproject.toml`
with `uv.lock`/`poetry.lock`, and `package.json` with `package-lock.json`.
Dependencies carry `version_kind`: `resolved`, `range` or `unresolved`.

**Deviation.** The issue framed Maven as the reference case. Measurement inverted
that: Maven is the *hardest* ecosystem, and Go states exact versions outright
while npm and Python commit a lockfile holding the resolved answer. So the rule
became **lockfile first**, and the clever cross-repo resolution became `OI-18`'s
problem rather than the general one.

`yarn.lock` and `pnpm-lock.yaml` are deliberately unparsed — bespoke formats — so
those repos fall back to the manifest range, recorded as a range.

**Behaviour change.** Go and Python repos report `dependencies_internal` for the
first time, so cross-repo discovery reaches them. Every dependency gains
`version_kind`. A repo whose ecosystem is recognised but unparsed now carries a
note instead of an empty list that read as a result.

---

_The original issue, verbatim:_

**Severity:** Medium — for most of the fleet `dependencies_internal: []` means
"not implemented" and is indistinguishable from "no internal dependencies".

### Symptom

| | |
|---|---|
| **Identity** | polyglot — 9 ecosystems recognised |
| **Dependencies** | Java + npm only — 4 parsers |
| **Lockfiles** | **never read.** `yarn.lock` and `pnpm-lock.yaml` are touched at `repo_utils.py:224` solely to detect the build system |

### Root cause

Two wrong assumptions. **That Maven is representative** — it is the hardest
ecosystem, while three others keep exact versions in a committed file nothing
reads. **That a manifest states a version** — npm and Python manifests state
*ranges*; the lockfile holds the resolved answer, and it is committed.

| Ecosystem | Where exact versions live | Parsed today | Effort |
|---|---|---|---|
| **Go** | `go.mod` — exact, MVS, no ranges | no | trivial |
| **Python** | `uv.lock` / `poetry.lock` / pinned requirements | no | small |
| **npm/yarn/pnpm** | the lockfile | `package.json` only, i.e. ranges | small |
| **Maven** | parent chain + properties + BOM | literal only | `OI-18` |
| **Gradle** | catalogue + `ext` + computed | catalogue only (`OI-3`) | hard ceiling |

### Proposed fix

**Lockfile first; inheritance chasing only where no lockfile convention exists.**
Go `go.mod`; Python lock then manifest; npm lockfile then manifest range marked
*as* a range. Plus ecosystem-aware internal detection —
`is_internal_coordinate` only ever receives Maven/Gradle/npm coordinates, so a Go
module path and a Python distribution name are never tested.

### A consequence to design for

**A range is not a version.** Where no lockfile exists the honest record is a
constraint, and anything consuming it must handle "satisfies `^1.4.2`" rather
than "equals 1.4.2". Recording a range as a version would repeat `OI-18` in a new
ecosystem.

### Suggested tests

* A Go repo reports internal `require` entries with exact versions.
* A Python repo with a lockfile reports resolved versions; without one, ranges
  marked as ranges.
* An npm repo reports the lockfile's resolved version, not the manifest range.
* Internal detection recognises a Go module path and a Python distribution name.
* A repo with genuinely no internal dependencies is distinguishable in the record
  from one the tool cannot parse.

### Residual not covered

Rust, PHP, .NET and Ruby stay identity-only. A deliberate stop — but the record
must say "not parsed" rather than `[]`.

## OI-18 — Dependency versions are recorded unresolved

### Resolution

**Fixed in:** 2.1.0  
**Commit:** `0060158` (red), `a20a0c5` (fix) — PR #31  
**Tests:** `tests/test_maven_resolution.py` — nine cases across the five tiers, the BOM exclusion, the placeholder rule, and both property-chain terminations. Mutants `OI18-M1`..`OI18-M4`.

**What changed.** `src2sink/maven.py` resolves versions offline in tiers, and
records which tier answered: `literal`, `property`, `parent-in-repo`,
`parent-in-fleet`, `unresolved`. `<dependencyManagement>` is read for versions
only and no longer emits the BOM as a dependency. An unresolved version is
recorded as empty with `version_kind: unresolved`, never as `${...}`.

**The tier that makes it work offline** is `parent-in-fleet`: every internal repo
is already cloned, so a parent POM in another repository is a file read. No
`mvn`, no registry, no downloaded binaries. An external parent
(`spring-boot-starter-parent`) is deliberately left unresolved — it governs
external dependency versions, which are not tracked, so the one tier needing the
network is the one tier not needed.

**Imprecision recorded rather than hidden:** a sibling repo is at HEAD, not
necessarily at the version the consumer pins, so the entry carries
`parent_resolved_at: head`.

**Deviation.** A cycle-detecting `seen` set was written for property expansion
and the mutation gate proved it unreachable — the depth bound terminates
`${a}` -> `${b}` -> `${a}` either way, so the set changed how quickly the answer
arrived and never what it was. Removed, and the mutant re-derived to guard the
bound instead.

Also removed `repo_utils.parse_pom_dependencies`, superseded and unused. Its
billion-laughs XXE tests were retargeted to the new resolver rather than deleted
with it — the protection is the same `defusedxml` path, and the test is what says
so.

**Behaviour change.** Recorded versions change from `${property}` strings and
empty strings to resolved values or an explicit unresolved. BOM coordinates stop
appearing in `dependencies_internal`, so any edge built on one disappears.

---

_The original issue, verbatim:_

**Severity:** Medium — the tool records placeholders and empty strings in a field
consumers read as a version, and invents a dependency that does not exist.

### Symptom

Measured against `parse_pom_dependencies` on four POM shapes:

```
literal version                      -> [('warehouse-client', '1.4.2')]        OK
property-interpolated                -> [('warehouse-client', '${warehouse.version}')]
inherited from parent                -> [('warehouse-client', '<empty>')]
BOM-managed (dependencyManagement)   -> [('platform-bom', '7.2.0'), ('warehouse-client', '<empty>')]
```

Only the literal case works. Properties, parent inheritance and BOM imports are
the norm in enterprise Maven, so most recorded versions are a `${property}`
string or an empty string presented as a version — and in the BOM case
`platform-bom` is emitted as a **dependency in its own right**, an edge to an
artefact the code never calls.

### Root cause

`parse_pom_dependencies` reads `<version>` as text. Maven resolves it from four
places and the parser knows one. `<dependencyManagement>` is additionally read as
if it were `<dependencies>`, which produces the phantom BOM edge.

### Proposed fix

Resolve offline, in tiers, recording which tier answered: `literal`, `property`
(same file), `parent-in-repo`, `parent-in-fleet` (via the identity index), else
`unresolved`.

**Demonstrated feasible without `mvn`, a registry, or downloading anything** —
the identity index already maps `(group, name)` to a clone path, so a parent POM
in another repository is a file read. See
[`identity-versioning-boundaries.md`](../plans/identity-versioning-boundaries.md) §4.2.

Two rules matter more than the tiers:

1. **An unresolved version is recorded as unresolved**, never as `${...}` or `""`.
2. **`<dependencyManagement>` entries are not dependencies.** They constrain
   versions; the BOM edge must go.

**Known imprecision, to be labelled not hidden:** a parent POM read from a
sibling repo is that repo at HEAD, not at the pinned version. Record
`parent-resolved-at: head`.

### Suggested tests

* Each of the four shapes resolves or is explicitly `unresolved`.
* A parent in another repo resolves through the identity index as `parent-in-fleet`.
* A `<dependencyManagement>` entry never appears in `dependencies_internal`.
* An external parent yields `unresolved`, not a guess.
* A property referring to another property terminates rather than looping.

### Residual not covered

Gradle's resolution is a program, not a document. `OI-3` handled version
catalogues, the tractable part; anything beyond should report `unresolved`.

## OI-21 — Entry points are HTTP-annotation-only

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `b9edd57` (red), `1f6edad` (fix) — PR #33  
**Tests:** `tests/test_entry_points.py` — nine cases across HTTP, queue, gRPC, GraphQL, scheduled and CLI, plus the externally-triggered distinction, the producer exclusion, and the assertion that entry points are derived rather than extracted. Mutants `OI21-M1`..`OI21-M3`.

**What changed.** Two families. `entry-marker` is an *observation* recording that
a non-HTTP entry mechanism was seen; `entry-point` is *derived*, unifying HTTP
endpoints, queue consumers and markers into one set.

**The part that needed no new extraction at all.** A `@KafkaListener` has
produced a `queue-sub`/`source` node since 1.x — it was already recorded, and
simply never treated as a front door. The fixture corpus proves it: of the seven
entry points that appeared, one is a Kafka consumer in `notifications/sms-consumer`
that has been visible to the scanner and invisible as a way in for every release
so far.

**Derived, not extracted, deliberately.** What counts as a front door is a
classification, and the list will keep growing — every framework is another
mechanism. Deriving it means adding one costs a re-derive over records rather
than a fleet re-parse, which is exactly the property the observation layer was
built for.

**`externally_triggered`.** A scheduled job is a way in, but the clock opens it,
not a caller — so it carries no untrusted input by that route. Recorded as a
field rather than resolved by either omitting scheduled work or silently
equating it with an HTTP endpoint, because `OI-17` will need to tell them apart
when it reports what an attacker controls.

**Deviation.** The issue listed file watchers and environment input alongside the
rest. File watches are covered by a marker; environment reads are not, because
the `config` family already records them and a second reading would double-count.
Recorded here rather than quietly dropped.

**Behaviour change.** Records gain both families. `DETECTION_VERSION` and
`DERIVATION_VERSION` both bump, so the fleet rescans — the extraction half is new,
not only the classification.

---

_The original issue, verbatim:_

**Severity:** High — and it gates `OI-17`.

### Symptom

`HTTP_IN_RX` is keyed per framework bucket, so an entry point is recognised only
if it is an HTTP framework annotation. Invisible today: message consumers
(`@KafkaListener`, JMS, SQS, Rabbit), gRPC services, GraphQL resolvers, scheduled
jobs, file watchers, CLI arguments, and environment input.

The tool sees one *kind* of front door.

### Why it gates `OI-17`

Reachability computed from an incomplete entry-point set produces confident,
incomplete answers: a trace reporting "no path from any entrypoint" when the
entrypoint was a `@KafkaListener` nobody can see. That is the failure class this
project has spent its effort removing, and it is worse here because the
conclusion looks like a clean result.

**Decided:** `OI-21` lands before `OI-17`
([`identity-versioning-boundaries.md`](../plans/identity-versioning-boundaries.md) §7, Q7).

### Suggested tests

* A Kafka/JMS/SQS consumer is recognised as an entry point in Java and Kotlin.
* A gRPC service method and a GraphQL resolver are recognised.
* A scheduled job is recognised, and distinguished from an externally-triggered
  entry — it carries no untrusted input by that route.
* A repo with only non-HTTP entry points is distinguishable from one with none.

### Residual not covered

Entry points reached through frameworks that wire handlers dynamically at
runtime — reflection, service loaders, annotation processors — stay invisible.
The record must say which mechanisms were searched, so a caller can tell "none
found" from "none looked for".

## OI-13 — Kotlin call sites are invisible to the AST pass

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `8c0aeb1` (red), `9456ead` (fix) — PR #34  
**Tests:** `tests/test_oi13_kotlin_parity.py` (Java/Kotlin family parity, receiver on the sink, the `OI-26` guard holding in Kotlin, `raw-code-payload` and `script-exec` parity) and `tests/test_ast_walk.py` (name and receiver extraction, single-yield, the class-wide guard, defensive branches). Mutants `OI13-M1`, `OI13-M3`.

**What changed.** `call_name_java_kotlin` split into `call_name_java` and a new
`call_name_kotlin`, plus `call_receiver_kotlin`. Kotlin has no
`method_invocation`: a call is a `call_expression` whose first child is either a
bare `identifier` or a `navigation_expression` whose final `identifier` is the
name. The receiver is that navigation's first child, which is what gives
`this.dao.find(...)` the receiver `this.dao`.

`CALL_NODE_TYPES["kotlin"]` also narrowed to `call_expression` alone.
`navigation_expression` is a property access, and listing it made every call
arrive twice — once as the call and once as the navigation beneath it.

**Deviation.** The issue proposed a Kotlin-specific walker and that is what was
built, but it did not anticipate the double-yield: the type set and the name
extractor had to change together, and fixing only the extractor would have
doubled every Kotlin finding.

A mutant restoring the wider type set proved **unkillable**, because
`call_name_kotlin` rejects non-call nodes independently. Recorded in the code
rather than removing the second defence to make the mutant work.

**The corpus had the same blind spot as the code.** There were zero `.kt` files
in the fixtures, so the snapshots could not have caught this and did not change
when it was fixed. A Kotlin fixture was added, and it now demonstrates the
parity: two `sql` sinks with correct postures, an HTTP entry point, and
`httpClient.execute` correctly excluded.

**Behaviour change.** Kotlin repositories gain `sql` sinks, `script-exec` sinks
and `raw-code-payload` findings from the AST tier for the first time. Counts rise
for any Kotlin service. `DETECTION_VERSION` bumps, so the fleet rescans.

---

_The original issue, verbatim:_

**Severity:** High — a whole supported language silently loses its SQL sinks.

**Found:** while raising `extractors/ast_walk.py` coverage for WI-12; asserted there rather than fixed, because the fix changes detection output.

### Symptom

The same query, in the two languages the JVM half of the fleet is written in:

```java
// Java
class StockDao { List<Stock> find(long id) {
    return jdbcTemplate.query("SELECT ref FROM stock WHERE id = ?", mapper, id); } }
```

```kotlin
// Kotlin — identical call, identical receiver, identical SQL
class StockDao { fun find(id: Long): List<Stock> {
    return jdbcTemplate.query("SELECT ref FROM stock WHERE id = ?", mapper, id) } }
```

Measured:

```
java    -> ['data-class-field/source', 'sql/sink']
kotlin  -> ['data-class-field/source']
```

The Java file yields a `sql` execution sink. The Kotlin file yields none — and reports no error, no warning and no note.

### Root cause

`CALL_NODE_TYPES` correctly names Kotlin's call nodes:

```python
"kotlin": frozenset({"call_expression", "navigation_expression"}),
```

but `extract_call_name` routes Kotlin to the **Java** walker:

```python
if language in ("java", "kotlin"):
    return call_name_java_kotlin(source, node)
```

and that walker's first line rejects everything Kotlin produces:

```python
if node.type != "method_invocation":      # a Java grammar type
    return None
```

`method_invocation` does not exist in the Kotlin grammar — it uses `call_expression` wrapping a `navigation_expression`. So `iter_calls` finds the nodes, asks for their names, receives `None` for every one, and yields nothing. The two halves of the dispatch disagree and neither says so.

### What is lost

Everything downstream of the AST pass, for Kotlin only:

* **`sql` sinks** — no execution sinks, so no SQL family from the AST tier at all;
* **`raw-code-payload`** — requires `ctx.sql_execution_sinks`, which is always empty, so a Kotlin endpoint accepting a `sql` field and executing it is never correlated;
* **`script-exec`** — `eval`/`exec`/`compile` call sites are never seen;
* **receiver evidence** (`OI-7`) — with no call sites there is nothing to gate, so Kotlin SQL is found only when a *regex* tier happens to match a literal.

Kotlin is a first-class target: `tree-sitter-kotlin` is one of the eight runtime dependencies and `.kt` files are scanned like any other. This is not an unsupported language degrading gracefully; it is a supported one failing silently.

### Proposed fix

A Kotlin-specific walker, `call_name_kotlin`, reading the grammar's own shapes: a `call_expression` whose callee is a `simple_identifier` (a bare call) or a `navigation_expression` (a call on a receiver, where the name is the last `navigation_suffix`). `extract_call_receiver` needs the matching branch — the receiver is the navigation expression's first child rather than an `object` field.

The same dispatch bug is worth checking for elsewhere: any language whose entry in `CALL_NODE_TYPES` names node types the corresponding walker does not accept has this defect. A test that asserts *every* language in `CALL_NODE_TYPES` yields at least one named call for a representative source would catch the whole class, and is cheaper than auditing each walker by hand.

### Suggested tests

* The Kotlin fixture above yields a `sql` sink with `receiver == "jdbcTemplate"`, matching the Java result.
* A bare Kotlin call (`execute(x)`) yields a name and no receiver.
* A Kotlin endpoint with a `sql` field plus an execution sink yields `raw-code-payload`, as the Java equivalent does.
* Every language in `CALL_NODE_TYPES` names at least one call in a representative snippet — the class-wide guard.
* `tests/test_ast_walk.py::test_kotlin_calls_are_not_named_by_the_java_walker` asserts the *current* behaviour and must be replaced, not deleted: it is what would otherwise let this regress quietly.

### Residual not covered

Kotlin's regex tiers already work, so string-built SQL in Kotlin is detected today; this issue is only about the AST pass. Scala and other JVM languages are not scanned at all and are out of scope.

## OI-28 — The index fast path bypasses the significant-segment filter

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `75c5422` — PR #37  
**Tests:** `tests/test_oi28_index_fast_path.py` — 25 cases: every segment shape that names nothing, the real routes that must survive, the memo, an end-to-end `collect_service_edges` assertion, and a consistency invariant across sixteen paths. Mutant `OI28-M1`.

### Symptom

Reported from the field against 2.1.0. `OI-24` moved the equality shortcut in
`path_templates_match` below the guard that a path reducing to no significant
segments names nothing — and the service-call edges never went through it.

```
  path_templates_match('/v1', '/v1')            -> None
  match_path_in_inbound_index('/v1', inbound)   -> conf='high' rows=[...]
```

`match_path_in_inbound_index` looks the normalised path up in a dict first, and a
hit returns `high` without consulting the predicate at all. Two repos each
exposing a bare `/v1` still produced a confident cross-repo edge.

### Root cause

Two separate code paths answer the same question — *does this path denote that
route* — and nothing required them to agree. `OI-24` was verified against the
function the issue named, and nothing checked whether the callers reached it. The
reporter's phrasing is the diagnosis: **same issue in the caller instead of the
callee**.

### What changed

`_names_a_destination` is applied at the top of the lookup, before both the dict
fast path and the fuzzy pass, so a path that names nothing is rejected however it
is matched — and before the memo, so a wrong answer is not cached and served to
every later call site sharing that path.

**The durable part is not the guard.** A test now asserts the invariant directly:
if the predicate rejects a path, the index must return no rows for it, and if the
predicate accepts one, the index must not lose it. That is what would have caught
this without a second report, and it holds across every shape either has been
wrong about.

### No version bump

`match_path_in_inbound_index` reaches only aggregators; the record path
(`extract_urls_and_paths`) is untouched. Aggregate output is recomputed from
records every run, so nothing is served stale.

### Behaviour change

Cross-repo edges resolved through a bare `/v1`, `/api`, `/service` or `/{id}`
disappear. These are the edges `OI-24` was raised to remove and did not reach, so
anyone who saw no change from `OI-24` in 2.1.0 will see it now.

---

## OI-15 — The whole fleet is held in memory, so a large metabase cannot be read at all

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `aee11a2` — PR #39  
**Tests:** `tests/test_fleet_index.py` — 20 cases: the indexed trace with fleet-loading made an error, index/live agreement under four path filters, six staleness shapes (edit, addition, removal, vanished record, version mismatch, corruption), fallback behaviour, and the storage invariants.

### Symptom

Projected from measurement before anyone hit it; **triggered in 2026-08** when
the fleet passed 34 GB and a trace completed only by swapping.

`run_trace` read every repo record in the metabase to answer a question about one
repo. Deserialised JSON runs ~6.5x its size on disk, so 34 GB on disk needs
~222 GB resident merely to be held. Past that the tool does not run slowly; it is
killed, with no partial result and nothing to bisect.

### Root cause

`load_v2_repo_records` returns a `list` of every record, and `run_trace` was
written against that list — for the target lookup, for `collect_service_edges`,
and for the outbound-node scan.

### What the fix is not

The 3.0 plan held that this was blocked by **Finding A**: 14 aggregators fuse
computation with rendering, so there was said to be no computed value to persist.
That was wrong, and reading `trace` rather than the aggregator inventory showed
it: **`trace` reads none of the rendered artefacts.** All three of its fleet-wide
calls were already pure, and `run_trace` already returned a `TraceReport` that
was rendered separately.

The fused aggregators block persisting the *catalogue views*. They do not block
persisting the *trace inputs*, and it is the trace inputs that make `trace` slow.
So ~2,400 lines of refactoring across 13 modules was removed from the critical
path. See §2a of `docs/plans/src2sink-3.0-plan.md`.

### The fix

`src2sink/index_store.py` persists to `metabase/index.sqlite3`, during
aggregation, exactly the four things a trace consults — each keyed by target
repo:

| what | why it is enough |
|---|---|
| `repo` — repo id → JSON path | a trace reads exactly one record, so the records stay in their files |
| `call_edge` — indexed on `target_repo` | replaces `collect_service_edges` over the fleet |
| `outbound_node` — the `http-out` / `api-client-consumer` subset | replaces scanning every node of every record |
| `producer_hit` — indexed on `target_repo` | replaces `build_producer_indices` |

`FleetIndex` streams rows rather than fetching them into lists, which is the
property that matters: peak memory is a function of what arrives at the target,
not of fleet size.

**Drift is prevented structurally, not by discipline.** `_find_upstream_from_nodes`
was refactored so the live scan and the indexed query feed *the same*
`_upstream_from_outbound_nodes`. The index is a cache of this code's output
rather than a second implementation of it, so the two cannot answer differently.

**Staleness is checked on every read.** `fleet_signature` hashes each record's
size and mtime together with `SCHEMA_VERSION` and `DERIVATION_VERSION`; a
mismatch returns `None` and the caller falls back to loading. Content hashing was
rejected — hashing 34 GB to decide whether a cache is fresh costs more than the
cache saves — and the versions cover every change originating in the tool.
A missing, corrupt or foreign index is a cache miss, never an error.

### How it is tested

The issue suggested asserting peak RSS. The tests deliberately do not: an RSS
bound is machine-dependent, flaky on a shared runner, and since `ru_maxrss` is a
high-water mark that never falls it cannot observe the thing it claims to.

The structural assertion is exact instead — **make loading the fleet raise, and
require the trace to succeed anyway**:

```python
monkeypatch.setattr(trace_mod, "load_v2_repo_records", explode)
report = run_trace(tmp_path, _TARGET_ID)     # passes only if the fleet is never loaded
```

A trace that passes that provably held no fleet-wide structure, on any machine.

### Residual not covered

* The `outbound_node` table is *scanned*, not looked up, because a caller is
  usually identified by a literal inside `raw` that no key can answer. Memory
  stays flat, which is what this issue is about; the remaining time cost is
  bounded by a table far smaller than the fleet.
* Aggregation still loads the fleet — it genuinely needs several passes. This
  issue is about the *read* path; making the build streaming is separate work.
* The index is rebuilt whole rather than incrementally. Step 4 of the proposed
  fix (only changed repo versions recompute) is not done, and is what the
  versioning design's keys exist to enable.

---

## OI-29 — A caller's reported confidence was whichever edge came last

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `aee11a2` — PR #39  
**Tests:** `tests/test_fleet_index.py::test_the_index_and_a_fresh_computation_agree` and the four path-filter cases; the change is pinned by `tests/fixtures/characterization-snapshots/run_trace_sql_runner_api.json`.

### Symptom

Found while building the `OI-15` index, and only because the index ordered rows
differently from the live computation. The two paths disagreed about the same
fleet:

```
indexed :  fulfilment/fulfilment-commons  http-out-graph  high
computed:  fulfilment/fulfilment-commons  http-out-graph  low
```

The real fixture understated a finding the same way — `acme/api-consumer` was
reported at `low` when a `medium` edge for that caller existed.

### Root cause

`collect_service_edges` emits **several edges per caller**, one per route it
might be addressing, at different confidences. The merge in `run_trace` was:

```python
for hit in _find_upstream_from_graph(...):
    upstream[(hit.source_repo, hit.kind)] = hit     # last wins
```

Plain assignment, so the surviving hit was whichever edge the collector happened
to yield last — an arbitrary order, not a meaningful one. A `high` edge was
routinely overwritten by a `low` one for the same caller.

This mattered more than a cosmetic label. The output is an *indicator* meant to
tell a reader where to dig; reporting `low` where `high` evidence exists
suppresses the lead it was supposed to raise.

### The fix

Keep the strongest evidence per key rather than the last seen. This is what
`payload_producers` already did when merging its own hits, so the fix makes the
trace consistent with the project's existing rule rather than inventing one.

The three separate copies of the confidence rank map were replaced by a single
`graph_common.confidence_rank`, since having three was how the two merge sites
came to disagree in the first place.

Only within-source merging changes: the four evidence `kind`s are distinct per
source, so no source's precedence over another moved.

### Why it had not been caught

Nothing compared two independently-ordered computations of the same answer. The
characterization snapshot recorded the buggy value as correct, which is what a
characterization snapshot is for — it pins behaviour, and pinning includes
pinning a defect until something else disputes it. The index was that something.

---

## OI-30 — The producer scan reads the whole fleet once per binding

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `383a8ca` — PR #42  
**Tests:** `tests/test_producer_scan_single_pass.py` — 9 cases: read counts flat across binding counts, no file read twice, the per-binding dedup isolation, and one build per aggregation run. Mutants `OI30-M1`, `OI30-M2`.

### Symptom

Reported from the field: `payload-endpoint-producers` is the slowest part of a
scan apart from fleet-wide traces, at **70 minutes**.

### Root cause

`build_producer_indices` looped over bindings on the *outside*:

```python
for binding in get_bindings():          # N bindings
    for group/repo in repos_root:       # every repo
        for path in repo_dir.rglob("*"):
            text = _read_capped(path)   # read from disk, once per binding
```

So the entire checkout is read from disk **once per binding**, and the only
thing that differs between passes is which regex runs over text already in
memory. `IMPORT_SCAN_RX` was inside the loop too, so it re-ran over the same
text N times. Instrumented before the fix:

```
     1 bindings ->   11 file reads  (1x the fleet)
     3 bindings ->   33 file reads  (3x the fleet)
    10 bindings ->  110 file reads  (10x the fleet)
```

**And `OI-15` doubled it.** `aggregate_graphs_v2` called `build_producer_indices`
twice — once for the catalogue and once to populate the fleet index — so ten
bindings meant reading a 34 GB fleet twenty times. That regression was
introduced by the index work and landed on the slowest step of the run.

### The fix

Invert the loops: walk the fleet once, read each file once, and match it against
every binding while it is in memory. `scan_repos_for_bindings` returns one hit
list per binding, in the same order as before.

Disk I/O is the dominant term and regex over resident text is not, so the
reduction in reads is the reduction in time: **N×**, plus the 2× from building
the indices once per run and handing them to both consumers.

```
 bindings |   before |    after | speedup
        1 |       11 |       11 |   1.0x
       10 |      110 |       11 |  10.0x
       20 |      220 |       11 |  20.0x
```

### What the tests are really for

This is a pure performance change, so the output tests matter more than the
speed one. The scan's dedup state is keyed `(repo, kind)` **per binding**, and
collapsing the loops is exactly the change that would quietly share one set —
after which the first binding to match a repo silently suppresses every other
binding's hit in it. A repo importing two different clients is the fixture that
catches it, and `OI30-M1` is the mutant that proves the fixture works.

### Confirmed in the field

Reported back after the fix: **the full fleet scans in 14 minutes** without
`--discover-api-clients`. `payload-endpoint-producers` alone was 70 minutes
before, so the step that dominated the run no longer does.

### Residual not covered

The walk is still single-threaded and still reads every file with a scannable
suffix. The next lever is skipping repos whose content-addressed version has not
changed — the versioning design's keys exist to enable exactly that, and it is
the same unfinished step 4 of `OI-15`.

`--discover-api-clients` was still adding fifteen more traversals on top of this;
that is `OI-31`.

---

## OI-17 — Nothing connects an entrypoint to a sink inside a service

### Resolution

**Fixed in:** 3.0.0  
**Commits:** `9456ead` (#34, Kotlin parity) · `1f6edad` (#33, entry points) · step 1 (#35) · step 2 (#36) · `abd9144` (#40, resolution) · `2940e56` (#41, path search)  
**Tests:** `tests/test_method_structure.py`, `tests/test_type_declarations.py`, `tests/test_call_resolution.py`, `tests/test_tainted_paths.py`, `tests/test_entry_points.py`. Mutants `OI17-M1` … `OI17-M17`.

### Symptom

The capability the tool is named for. `src2sink` found sources and found sinks
and did not connect them — measured on the canonical layered shape before any of
this work:

```
  StockController.java   -> ['http-in/source']      entrypoint found
  StockService.java      -> (nothing)               middle layer invisible
  StockDao.java          -> ['sql/sink', ...]       injectable sink found
  edges produced: 0
```

### What shipped, in four steps

1. **Method-level structure.** Declarations recorded as `method-decl`
   observations; every node stamped with its enclosing method.
2. **Type facts.** `type-decl` observations record field types, supertypes and
   whether a type is an interface.
3. **Tiered resolution** (`src2sink/resolve.py`), and observation widened from
   sink-shaped names to every call — the middle of every layered path was
   recorded nowhere. T1 declared field type (`high`), T2 interface expanded to
   implementations (`medium`, ambiguous when several), T3 unique name (`low`),
   otherwise dropped. The first `intra-repo` edges the schema has ever carried.
4. **Path search** (`src2sink/paths.py`). Entry-point parameters are tainted, an
   argument mentioning a tainted name taints the callee's parameter, and a hop
   carrying nothing is not walked.

### Where the design departed from the issue as filed

Three of step 4's stated requirements were overridden by
`docs/plans/observe-then-classify.md`, written later and with measurements:

| as filed | as shipped | why |
|---|---|---|
| BFS to a limit | unbounded depth | capping at 3 hops finds 25% of what depth 8 finds |
| confidence degrades along the path | minimum hop, never a product | multiplying takes 8 `medium` hops to 0.058, burying the deep paths that hold the value |
| "a floor below which nothing is emitted" | no floor | for an indicator a floor turns cheap false positives into expensive invisible false negatives |

T1 was also narrowed to declared **fields** only. `method-decl` records parameter
names and not their types, so `void f(StockService s) { s.process() }` falls to
T3. A real gap rather than a decision.

### The volume estimate was wrong, and its own warning caught it

The issue estimated widening at `+20%` from a 12-file synthetic corpus, with a
note to measure on real code first. Measured:

```
  synthetic corpus  :  +21 nodes            (+18%)
  real repository   :  1,667 -> 5,711 nodes (3.4x)
                       call sites are 75% of all nodes, ~54 per file
```

At fleet scale that is 34 GB becoming ~130 GB. Two changes brought it to 1.6x
nodes / 1.7x bytes: dropping `raw` and the SQL-evidence fields from ordinary
calls, and pruning calls naming nothing declared in the repo — **77% of observed
calls**, being `get`, `append`, `len`, `str`, `join`, which an intra-repo path
cannot pass through by definition.

### What building it exposed

**Three Kotlin gaps, each live since 2.1.0, each making Kotlin produce a
clean-looking result rather than a wrong one:** interfaces not recognised as
interfaces, an empty parameter list on every method, and an empty argument list
on every call. All three passed their original parity tests, because those tests
compared method *names* — the easy half of a record. That pattern is the lesson
worth carrying: a parity test that checks what is easy to check is not a parity
test.

### Residual not covered

Reflection, dynamic proxies, lambdas passed as callbacks, and queue or event hops
are out of reach syntactically. Python and JavaScript lack declared types, so T1
is largely unavailable and results there are much weaker — the value is
concentrated in the JVM fleet. Field writes, collections and returned values are
not modelled: this tracks *arguments into parameters*, and is described as that
rather than as taint analysis. Argument binding is positional, so named and
defaulted arguments under-taint rather than over-taint.

---

## OI-31 — The checkout is walked once per filename, and no phase shares a walk

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `ccdb358` — PR #43  
**Tests:** `tests/test_checkout_scan.py` — 17 cases: walk counts, cache reuse and widening, `prewalk`, the `SKIP_DIRS` prefix rule, glob vs exact attribution, sort stability, and both CLI paths. Mutants `OI31-M1`…`OI31-M3`.

### Symptom

Found by the same reporter as `OI-30`, immediately after it: *"looks like the
same problem that impacted the payload_producers also impacts the discovery."*
Correct, and in two places rather than one.

### Root cause

`Path.rglob(name)` traverses the whole tree and filters by name, so asking for
four filenames costs four full traversals. Measured on one run:

```
aggregation            :  10 full walks    8x discover_openapi_specs (4 globs x 2 call sites)
                                           2x discover_helm_hosts
--discover-api-clients :  15 more         15x _iter_manifests
TOTAL                  :  25
```

Twenty-five traversals of a 34 GB checkout to find a handful of manifests. Same
shape as `OI-30`: the loop over *what to look for* sat outside the loop over
*where to look*. And nothing was shared between phases — `--discover-api-clients`
re-walked everything aggregation had just walked.

### The fix

`src2sink/checkout_scan.py`: one traversal, cached per root, keeping every file
matching any requested pattern. A caller asking for patterns already covered is
served from the cache; one asking for something new **widens** the walk rather
than starting a private one, so phases converge on a single traversal without
either having to know the other exists. `prewalk` lets the CLI name every
pattern up front and guarantee it from the first call.

```
                         before   after   with prewalk
aggregation                  10       2              1
--discover-api-clients       15       1              0
TOTAL                        25       3              1
```

### The other bug this exposed

**`--discover-api-clients` was silently ignored outside a full scan.** Discovery
ran only in the full-scan path, so combining it with `--graphs-only` or
`--aggregate-only` printed `Done.` and wrote nothing — the flag accepted, doing
nothing, giving no clue why no candidates appeared:

```
$ src2sink-build ... --graphs-only --discover-api-clients
Aggregate-only: 1 v2 JSONs
Done.
$ ls metabase/api-clients.discovered.json
ls: No such file or directory
```

Discovery reads records and the checkout, both of which those modes have, so
there was never a reason it could not run. Now reachable from both paths.

### What nearly went wrong

The first version of the shared walk matched `SKIP_DIRS` against the **absolute**
path, so a checkout under `/tmp/build/repos` excluded its own entire tree —
everything resolving to "not found" with no error.
`test_skip_dirs_apply_below_the_root_only` caught it, which is exactly the
regression that test was written for: its docstring records that CI found the
same defect once before, because CI's tmpdir is under `/tmp`. The rule is now
asserted in `checkout_scan`'s own tests as well, since the logic is duplicated
there to avoid an import cycle.

### Residual not covered

The walk holds only matching paths, so memory is bounded by how many manifests a
fleet has rather than by how many files. Two roots are cached independently and
nothing evicts, which is right for a batch run and wrong for a long-lived
process — `clear_cache` exists for the latter. The producer scan (`OI-30`) still
walks separately, because it reads files by *suffix* across the whole tree rather
than by name; folding it in would mean holding every source path in memory,
trading this fix for the one `OI-15` exists to prevent.

---

## OI-35 — Api-client discovery rescans the whole fleet once per class

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `a5a75ed` — PR #44  

> **Note on the id.** This was filed and fixed as `OI-33`, and renumbered to
> `OI-35` when two issues taking ids 33 and 34 landed on `main` first. Theirs
> were published and cited; this one was still on a branch, so this one moved.
>
> **The squash-merge commit title still reads `OI-33`**, because it predates the
> renumbering and a merged commit message on `main` is not worth a force-push to
> correct. So `git log --grep OI-33` finds this commit, which is *not* the
> identity-mismatch issue that now holds that number. Recorded here because this
> is where someone chasing the discrepancy will look.

**Tests:** `tests/test_discovery_scan_cost.py` — 11 cases: equivalence against the replaced implementation kept as an oracle, one-pass node-visit counts, the growth curve across two fleet sizes, and the over-generic warning the scan exists to produce. Mutants `OI35-M1`, `OI35-M2`.

### Symptom

Reported from the field, immediately after `OI-31`: *"discovery is a quadratic
scan."* It was, and in the worst shape rather worse than quadratic.

### Root cause

`_apply_demand_side` iterated targets, and each target's candidate classes, and
asked the corpus about every one:

```python
for target, seen in sorted(observed.items()):        # T targets
    for cls in sorted(seen["classes"]):              # C classes each
        repos = _repos_containing_class(records, cls)  # walks EVERY node of EVERY record
```

So the cost was `targets x classes x records x nodes`. Measured on a synthetic
fleet where all four scale together, node visits grew **~15x for every doubling**
of the repository count:

```
 repos   nodes    node visits
     8      20            320
    16      72          4,608   14.4x
    32     272         69,632   15.1x
    48     600        345,600
```

`_enclosing_class` — a path-stem split — was recomputed for every node on every
one of those passes.

### The fix

Nothing about the answer needed the rescan. "Which repos contain a file called
`StockClient`" is corpus-wide and **target-independent**, so it is answered from
an index built in one pass before the loop rather than by a scan inside it.

```
 repos   nodes       before      after     saving
     8      20          320         20        16x
    16      72        4,608         72        64x
    32     272       69,632        272       256x
    48     600      345,600        600       576x
```

Node visits now equal the fleet's node count exactly, and the saving keeps
growing with the fleet — on a real corpus of thousands of repositories it is
orders of magnitude.

### Why the equivalence test matters more than the speed test

An index that is faster and answers differently is not a fix, and the answer here
decides a *safety* warning: a class pattern appearing in too many repositories is
too generic to accept as a binding, and a reviewer is told so. So the replaced
implementation is kept in the test file as an **oracle** and the two are asserted
identical across every shape, including the empty class name and a class present
nowhere. `OI35-M2` — keying the index on the full path instead of the class name
— is the mutant for the failure that would otherwise be silent: every lookup
misses, and the warning simply stops appearing.

### The pattern this is the third instance of

`OI-30` (producer scan, once per binding), `OI-31` (checkout walk, once per
filename), and now `OI-35` (fleet scan, once per class). Each is the same shape:
**a loop over what to look for placed outside the loop over where to look.**
`OI-14` was the first, in the service-call graph. It is worth treating as a
recognised smell rather than four coincidences — any `for x in things: scan(fleet)`
deserves the question of whether the scan is `x`-independent, and here it always
has been.

### Residual not covered

The remaining passes over the fleet — `_http_in_paths_by_repo`,
`_collect_candidates`, `_demand_side_observations`, and this index — each run
exactly once, verified by inspection of the call sites. They are linear and are
not worth merging: they read different fields and merging them would trade a
clear cost for an unclear one.

---

## OI-33 — Discovery's two passes never agree: `discovery_method: "both"` is unreachable

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `4789405` — PR #45  
**Tests:** `tests/test_oi33_canonical_repo_id.py` — 15 cases: the normalisation across module paths, nested repos and unresolvable inputs; `both` reachable end to end; the defect reproduced so that test cannot pass vacuously; the recovered paths, confidence and alias; and reviewer state surviving the key change. Mutants `OI33-M1`…`OI33-M3`.

### Symptom

Filed from the first completed discovery run over the fleet, once `OI-35` let the
pass finish at all:

```
discovery_method: dependency  125
discovery_method: call-site   101
discovery_method: both          0
```

Never one, out of 226 candidates.

### Root cause

The two passes named the same service differently. Demand-side used
`repo_id(data)` — `group/name`. Supply-side used the resolver's output directly,
and the resolver returns the directory that *declares* a coordinate, which for a
multi-module build sits inside the repo. `_apply_demand_side` then did an exact
string lookup that could never bridge `group/repo` and `group/repo/some-client`.

The merge logic was correct throughout. It was fed a key it could not match.

**The last step was simply missing.** The identity index was built and wired, but
nothing normalised its output back to a repo id — and `resolve()`'s own docstring
claimed it returned *"the repo id that publishes it"*, which is very likely how
it went unnoticed. A function documented as doing the thing it does not do.

### The fix

`_canonical_repo_id` matches the **longest known repo id** that prefixes the
resolved path, applied where `_collect_candidates` sets `target_repo`.

Longest-match rather than a segment count, because `group/subgroup/repo` is a
valid GitLab path and this estate contains them (`OI-34`) — truncating to two
segments would corrupt exactly those. It also needs neither a depth rule nor
`.git`, both of which this estate defeats: 65 of 746 repos have no `.git` at all.

The raw path is kept as `target_module` and surfaced as `evidence.declared_at`,
since it is strictly more information than the repo id.

### Three consequences beyond the merge

None of these were in the report, and they are why this was not cosmetic:

* **Paths were suppressed.** `paths_by_repo` is keyed by repo id, so a
  module-path target matched none — and a binding with no paths cannot match a
  route.
* **Confidence was capped as a side effect.** `_confidence` returns
  `"high" if has_paths else "medium"`, so the missing paths held those candidates
  at `medium`. Expect the confidence distribution to shift upward after this
  lands; that is the fix, not a regression.
* **`service_aliases` was corrupted.** It is `target.split("/")[-1]`, which for
  `group/repo/warehouse-client` yielded the build module rather than the service.
  Alias matching is how a hand-rolled caller is recognised, so it misdirected the
  very pass the alias exists for.

### The migration hazard, handled

Candidates already reviewed are stored keyed on the **old** module path. The new
key is the repo id, so a naive fix would find nothing and silently revert every
`accepted`/`rejected` to `pending` — losing human review work with no message.
`_load_discovered` now indexes each stored candidate under its canonical key as
well, and `OI33-M3` is the mutant for that exact loss.

### The 8 empty targets

Kept rather than dropped — dropping loses information — but now carrying a
warning that names the coordinate and says promoting it would create an edge to
nothing. An unresolvable target must not read like a merely weak candidate.
That is `OI-36`'s rule applied at the point it was violated.

### What this says about the class

`resolve()` was documented as returning a repo id and returned a clone path. The
mismatch was invisible because both are strings, both look like `a/b`, and the
consumer that could not match simply produced fewer merges. Nothing failed. That
is `OI-36` again, in its purest form: **the wrong answer, with no signal.**

---

## OI-37 — The Express inbound-endpoint pattern has no receiver, so any `.get("…")` is an HTTP route

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `9f7300b` — PR #46  
**Tests:** `tests/test_oi37_express_anchor.py` — 23 cases: the direction test first, then each false population from the report's breakdown, the eight router shapes that must survive, the worked template-cache example, confidence by anchoring, `raw` auditability, and a mixed-file ratio guard. Mutants `OI37-M1`, `OI37-M2`.

### Symptom

Two thirds of every inbound endpoint in a 746-repo, predominantly JVM estate
attributed to Express. Resolving each against its source line: **20 were routes,
10,225 were not** — all at `confidence: "high"`.

### Root cause

The pattern began at the dot, so it matched a verb-named call on *any* receiver,
in the one language where `.get(key)` is ubiquitous for reasons unrelated to
HTTP. Every sibling in `HTTP_IN_RX` is anchored to a route-declaration marker —
Flask to `@app.route`, FastAPI to `@router.`, Spring and JAX-RS to an annotation.
This was the only one with no anchor.

### The fix

Require the receiver, from the small closed set of router idioms. Verified
against the report's own cases: the 20 genuine routes survive, and every false
population disappears — there is no middle band to tune.

Two changes alongside, both from the report:

* **`confidence` is derived from anchoring**, not hardcoded. `UNANCHORED_HTTP_IN`
  names the patterns that have no anchor, and `gin` is in it. This is the change
  that would have surfaced the defect without a fleet run, and it forces the next
  unanchored pattern to declare itself rather than inherit `high`.
* **`raw` now contains the receiver**, which falls out of the anchoring for free.
  It previously began at the dot, so all 10,245 nodes looked identical in the
  output and the distinction was recoverable only by re-reading the source.

`DETECTION_VERSION` 11 → 12: records built by the previous detector carry the
false endpoints and must not be reused.

### What made this worse than a noisy pattern

`http-in` is the entry-point set. `OI-21` derives entry points from these nodes,
so everything reachability-related inherited 10,225 fictitious front doors — and
**605 of them were outbound client calls**, recorded as doors *into* the service.
Direction-inverted, not merely spurious.

### Not fixed

Test, spec, Cypress and e2e trees are still not excluded from inbound-endpoint
extraction — 64% of the original matches were in them. With the anchor in place
the population is far smaller, but a route declared only in a test is still not
an entry point of the deployed service. Worth a provenance marker so reachability
can exclude them; filed thinking rather than code.

The report's fleet-level guard — flag it in the run manifest when one framework
dominates the inbound endpoints of an estate whose primary languages are
something else — is not implemented either. It belongs with `OI-36`, and it is
the cheap check that would have caught this on any run: the ratio was the symptom
long before anyone read a node.

---

## OI-38 — Only the build indexes traces, so the trace index never describes the batch that just ran

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `9f7300b` — PR #46  
**Tests:** `tests/test_oi38_trace_index_freshness.py` — 8 cases: the first-run failure reproduced, the batch writing its own index, the coverage figure tracking the traces that exist, the build's call still working, and `--output` outside the traces directory not rewriting the metabase. Mutant `OI38-M1`.

### Symptom

94 reports written by the first completed fleet-wide batch, and **no index**.

### Root cause

`write_traces_index` had exactly one caller — the build's aggregation phase.
Neither entry point that produces traces called it. Under the normal build-then-
trace workflow the index was generated before any trace from this cycle existed,
so it always described the previous batch; on a clean metabase the traces
directory does not exist during aggregation, so no index was written at all.

### Why it was more than cosmetic

The index states catalogue coverage — *"N / M endpoints have traces"* — with a
Missing traces table and the instruction to re-run with `--skip-existing`. That
is the operational signal saying how complete the work is, and it was stale in
the direction that matters: immediately after a batch, when someone is checking
whether the batch covered everything.

### The fix

`trace_batch` writes the index after its run, and reports the count. `trace`
does the same **only when its `--output` lands inside the traces directory** — a
trace written elsewhere has not changed the indexed set, and rewriting the
metabase as a side effect of `--output` would be surprising.

The build's call is kept. The two are complements: the build refreshes coverage
when the *catalogue* moves, the batch when the *traces* move.

### A note on the tests

The index recovers `(repo, endpoint)` from each report's **content**, not its
filename. A stub report without the `# Flow trace:` header counts as untraced, so
every coverage assertion would have passed vacuously against one — which the
first draft of these tests did, and the failure caught.

---

## OI-39 — The test-path predicate excluded production code and admitted test code

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `96aff62` — PR #47  
**Tests:** `tests/test_oi39_test_path_classification.py` — 32 cases across both directions, plus the boundary cases that keep each half honest. Mutants `OI39-M1`, `OI39-M2`.

### How it was found

Chasing down `OI-37`'s deferred item — *"exclude test and vendored trees from
inbound-endpoint extraction"* — which had been recorded under **Not fixed** in a
closed issue rather than filed as open. It surfaced only because someone read
that entry.

That is worth recording as its own small lesson: a deferral written into a closed
issue has nothing carrying it forward. `OI-36`'s argument applies to the issue
tracker as well as to the code.

### Two defects in one predicate, in opposite directions

`TEST_PATH_RX` gates **all** extraction — `extract_from_file` returns `[], []` on
a match — so it decides what the tool can see at all. It matched whole path
*segments*.

**Too wide, and this is the serious half.** The camelCase branch
`[a-z][a-zA-Z0-9]*Tests?` sat under `re.IGNORECASE`, so it read as *any segment
ending in "test"*:

```
SKIP  api/latest/handler.go      <-- a versioned API directory
SKIP  src/protest/handler.go
SKIP  src/contest/service.js
SKIP  src/attest/signer.py
SKIP  src/greatest/hits.ts
```

A repository laid out under `api/latest/` contributed **nothing** to the
metabase — no endpoints, no sinks, no PII, no dependencies — with no note, no
count and no warning. Indistinguishable from a repository that is genuinely
clean.

**Too narrow.** A test file beside the code it tests is invisible to segment
matching: `routes.spec.ts`, `handler_test.go`, `test_views.py`,
`StockControllerTest.java`, and anything under `cypress/` that is not also under
`e2e/`. **64% of `OI-37`'s 10,225 false endpoints were in files shaped like
these**, and a route declared only in a mock server is not a door into the
deployed service.

### The fix

* The camelCase branch is **case-sensitive** — `(?-i:...)`. A literal capital `T`
  separates `FooTest` from `latest`.
* Lowercase compound conventions (`loadtest`, `smoke-test`, `perf_test`,
  `acceptance-tests`) are **named explicitly**, because a rule broad enough to
  catch them by shape is precisely the rule that caused the defect above. Four
  alternations is a cheap price for not swallowing English.
* Filename conventions are matched separately and **anchored to source
  extensions**, so `api/openapi.spec.yaml` — a document describing the service's
  real endpoints — is not mistaken for a test.

`DETECTION_VERSION` 12 → 13.

### Why the boundary tests are the load-bearing ones

Both halves are one predicate, and a change to either can reopen the other. The
suite asserts in both directions on purpose — `openapi.spec.yaml` kept and
`routes.spec.ts` dropped; `latest/` kept and `loadtest/` dropped — because the
obvious fix for each half is the thing that breaks the other.

### Residual not covered

Vendored and minified trees are still not excluded. `SKIP_DIRS` covers `vendor`,
but a bundled `framework.js` under `src/assets/` is not caught by anything, and
5% of `OI-37`'s matches were in files like that. There is no reliable syntactic
signal for "this is third-party code we do not own" — line length and lack of
newlines are heuristics, not facts — so this needs a decision rather than a
pattern, and it is left open rather than guessed at.

---

## OI-40 — A candidate's `target_repo` names the client library when the library is its own repo

### Resolution

**Fixed in:** 3.0.0  
**Commit:** `2861396` — PR #49  
**Tests:** `tests/test_oi40_client_repo_target.py` — 14 cases. The two that matter are `test_a_real_service_is_never_rewritten` and `test_an_endpointless_target_is_flagged_not_dropped`. Mutants `OI40-M1`…`OI40-M4`.

### Symptom

42 of 191 candidates named a client library as `target_repo`; 0 of the 99
hand-authored bindings do. The record looks entirely correct — real repo, real
artifact, real consumers, unaffected confidence — and only the semantics are
wrong, which is why it survives every existing check.

### Root cause

`OI-33`'s fix behaving exactly as designed, meeting a case its design did not
consider. When a client library is published from **its own repository**, the
repo that declares the coordinate *is* the library. The pipeline faithfully
answers *"which repo declares this artifact"* while the binding needs *"which
service does this artifact call"* — a question coordinate resolution cannot
reach, because the service is not named anywhere in the consumer's dependency
declaration.

`OI-33` fixed the identity's **shape**. This is its **referent**.

### The fix, and why the discriminator is not the name

A service has inbound endpoints; a client library does not. So the test is the
endpoint count, and the name supplies only the stem to search for. The reporter's
own measurement is why: the name rule caught all 42 with no false positives *on
this fleet*, but the endpoint rule catches those 42 **plus 26 more across 19
repos** that no naming convention would find.

Validated against bindings written independently and long before discovery
existed: of 11 derivable corrections, **11 agree with the human-chosen target and
0 disagree**.

### Three outcomes, all recorded

1. **Corrected** — `target_repo` becomes the service, the library is kept as
   `client_repo`, and a warning names the substitution. A rewrite a reviewer
   cannot see is one they cannot check.
2. **No endpoints and no sibling** — emitted with a warning naming *both*
   possibilities. **Never dropped.** Zero endpoints is also what an `OI-17`-class
   detection gap looks like, and the two are indistinguishable from outside;
   filtering them would hide our own blind spot behind what reads as a
   data-quality rule. That is `OI-36` with the tool doing it to itself.
3. **Unchanged** — the common case, no annotation, so the warnings stay worth
   reading.

### On the migration mechanism

`_load_discovered`'s `known`-based indexing — written for `OI-33` so a target
reshaping would not discard reviewer accepts — carried this change with **no
modification at all**. The candidate key changes, and previously-accepted
candidates are still found.

That is the second use of one mechanism, and it is worth noting for `OI-34`: the
*structure* generalises, but the *derivation* does not. `_canonical_repo_id` maps
long → short by prefix; `OI-34` needs short → long, which is neither a prefix
relation nor unambiguous. See the migration table in `OI-34`.

---

## OI-42 — `--promote-api-clients` validates nothing and silently drops file keys

### Resolution

**Fixed in:** 3.1.0 (unreleased)  
**Commit:** `872e7ef` — PR #52 (the `_load_bindings_file` half landed early in `923fe1c`, PR #51 — see below)  
**Tests:** `tests/test_oi42_promote_validation.py` — 9 cases across both gates, the lossy rewrite, duplicate keys, idempotence, and the documented post-conditions asserted rather than checklisted. Mutants `OI42-M1`…`OI42-M3`.

### The tool was asking a human to do its arithmetic

`docs/api-clients-json.md` §4 lists five disqualifying gates. Two are pure
computation the tool already performs at discovery time, using code written for
`OI-33` and `OI-40`:

| gate | rejected by hand | now enforced by |
|---|---|---|
| 1 — `target_repo` non-empty and a known repo id | 8 of 191 | `known_repos` from the metabase |
| 2 — names the service, not the client library | 42 of 191 | `_service_for_client_repo` |

**50 of 191 candidates** were rejected manually for conditions the tool could
detect. `promote` merged on trust and re-checked nothing, so anything a reviewer
missed became an authoritative binding and misdirected every edge it matched.

Refusals name the candidate, the reason, and — for a client library — the service
it should have pointed at. One refusal does not block the rest of the batch.

### Two lossy behaviours, both documented as the reviewer's problem

**The handling notice was dropped.** `promote` wrote `{"bindings": [...]}` and
discarded every other top-level key, including the `_comment` carrying *"never
commit — internal topology"* on a gitignored, sensitivity-marked file. Silent,
and the file still looked correct. `OI-36` with a confidentiality consequence.

**Duplicate keys went stale.** Bindings were indexed with a dict comprehension
over a list that may hold duplicates, so only the last copy of a key was
refreshed. Earlier copies stayed at their old values — still present, still
loaded, now disagreeing with their twin about the same binding.

### The post-conditions are asserted, not checklisted

The document lists five post-conditions for a person to verify after promoting.
They are now a test. A checklist a tool can run should not be a checklist a
person runs.

### A note on how half of this shipped

`_load_bindings_file` and its malformed-JSON warning landed inside PR #51, the
`OI-41` work, whose description says nothing about them. They were uncommitted on
this branch when I switched to fix an unrelated opengrep failure, were carried
across, and were committed with that PR.

The changes are correct and tested, so reverting would be worse than recording
it — but the history is misleading, and a reviewer of #51 had no reason to expect
a change to `api_client_discovery.py` in a performance PR. Recorded here because
this is where someone tracing the change will look.

The half that shipped was also inert on its own: it preserved the whole document
on *load* while the write still rebuilt `{"bindings": ...}`, so nothing was
actually preserved until this change.

---

## OI-41 — Aggregation parses the whole metabase 14 times per run

### Resolution

**Fixed in:** 3.1.0 (unreleased)  
**Commit:** `923fe1c` — PR #51  
**Tests:** `tests/test_fleet_pass.py` — 8 cases: streaming and loading agree, collectors retain no records, one pass serves every collector, ordering preserved, and a ratchet on the counts. Verified byte-for-byte by `tests/test_aggregate_output_golden.py`.

### Result

```
full parses           : 14 -> 3
collect_service_edges :  3 -> 1
aggregation           : 2.12s -> 0.90s   (2.4x on the test fixture)
peak RSS              : 148 MB -> 150 MB (memoising was 266 MB)
```

That +2 MB is the point. Memoising buys the same seconds and costs a *held* copy
of the fleet — measured at +118 MB on a 29 MB metabase, which at 2.2 GB is
`OI-15`'s ceiling reached through the fix, on a host already swapping.

### The bigger find was not a parse

`collect_service_edges` — the fleet-wide derivation `OI-14` identified as
dominating cost — ran three times per aggregation: the service-call report, the
fleet index, and inside the PII cross-repo flows, which is *per PII field*.

That is the sixth instance of one pattern, after `OI-14`, `OI-30`, `OI-31`,
`OI-35` and this issue's own parses: **a target-independent derivation computed
per consumer.**

### Why three parses remain

Three genuinely separate phases with a data dependency, not laziness: the shared
load feeding the service-call report and producer index; the PII lifecycle pass
that *produces* the touchpoints; and phase 3, which consumes them. Collapsing
them means holding records across the whole aggregation — the cost this exists to
avoid.

A ratchet asserts parses ≤ 3 and edge builds ≤ 1, so a new aggregator that loads
for itself fails rather than quietly adding a pass back.

### It also corrected the 3.0 plan

Withdrawing Phase 1 from `OI-15`'s critical path was right; concluding it could
be deferred indefinitely was not. It is not on *trace*'s critical path — it is
squarely on *aggregation*'s, which is 78% of the run. The withdrawal reasoned
from one consumer and generalised to the whole plan. §2a records both.

### Residual not covered

Extraction is unchanged, and `OI-32`'s measurement puts it at ~150 s of a 657 s
run already spread across 11 processes. The remaining aggregation time is real
work rather than repetition.

