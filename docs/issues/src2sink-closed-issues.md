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

**Fixed in:** 2.1.0 (unreleased)  
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

**Fixed in:** 2.1.0 (unreleased)  
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
