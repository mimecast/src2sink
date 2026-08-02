# src2sink 1.1.0 — Open Detection Issues and Proposed Fixes

**Version reviewed:** src2sink 1.1.0
**Status:** every issue in this document is **open**. Fixed issues are removed from here and recorded in [`src2sink-closed-issues.md`](src2sink-closed-issues.md) with their fix and commit sha, so the length of this file is the backlog. Earlier defects (empty-binding silent failure, `api-client-consumer` nodes never reaching the call graph, class-name-anchored call-site regexes, constant/enum indirection, binding aliases, unmatched-ref reporting) were fixed in 1.1.0 before that convention existed and are not repeated here.

**Citing an issue:** use the stable `OI-n` id shown on each heading, not the section number — section numbers do not survive the move to the closed-issues file. See §5.

**Anonymisation notice:** every repository name, package name, artifact id, service name, class name, constant name and **URL path** in this document is fictitious. The worked example throughout is an invented warehouse system. References to `src2sink`'s own source (file:line) and to third-party library names appearing in `src2sink`'s regexes (`RestTemplate`, `requests`, …) are real, as those are needed to locate the code being fixed.

---

## 0. Context: how these were found

**§1–§4** came from a fleet scan of several hundred repositories, used to measure detection coverage for one heavily-consumed internal service. Coverage of that service's callers in the service-call graph rose from 1 to 22 after upgrading to 1.1.0. Investigating the callers that *remained* invisible surfaced those four issues. Three of them are general — not specific to the service used as the probe.

**§7–§9** came from a later review of the SQL families, unrelated to the fleet scan. Their evidence is measured `extract_from_file` output on 1.1.0 rather than fleet statistics.

The running example is a fictitious service `commerce/warehouse-service`, which publishes a client library `warehouse-service-client` (group `com.example.commerce.warehouse.client`) and exposes `POST /stock`. It is consumed by a fictitious repo `fulfilment/fulfilment-commons`.

---

## 1. Version prefixes outrank real route names in path matching  `OI-1`

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

## 2. Context guards suppress fully custom HTTP wrappers  `OI-2`

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

## 3. Dependency parsing misses Gradle version catalogs  `OI-3`

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

## 4. Client discovery is single-direction and never proposes `class_patterns`  `OI-4`

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

## 5. Priority

| id | # | Issue | Effort | Value | Priority |
|---|---|---|---|---|---|
| OI-7 | 7 | `sql` family matches on method name alone | low–medium | withdraws fabricated high-confidence injection findings | **P0** |
| OI-1 | 1 | Version prefixes outrank route names | low | fixes wrong edges, not just missing ones | **P0** |
| OI-10 | 10 | `parameterised` claims a safety property it cannot establish | low (with OI-8) | withdraws a false *safe* label from injectable call sites | **P0** |
| OI-11 | 11 | A base-query constant hides the concatenation appended to it | medium | misses a common injection shape *and* labels it safe | **P0** |
| OI-8 | 8 | SQL built by formatting is undetected | low | a confirmed injection currently produces no node | P1 |
| OI-3 | 3 | Gradle version catalogs unparsed | low | restores discovery input for affected repos | P1 |
| OI-2 | 2 | Context guards miss custom wrappers | low–medium | recovers hand-rolled callers | P1 |
| OI-9 | 9 | No `sql-payload-out` family | medium | a whole sink class is unrepresented | P2 |
| OI-4 | 4 | Demand-side discovery | medium | generates the field that cannot be inferred otherwise | P2 |

Issues 7, 1 and 10 are first because they produce **incorrect output**. The others
reduce recall; these reduce precision, and a confidently wrong result is worse than
a missing one — nothing downstream distinguishes it from a real finding. Issue 7
outranks issue 1 because its wrong output is a *security finding*: a wrong service
edge misleads, a fabricated injection endpoint sends someone to audit code that was
never vulnerable.

Issues 10 and 11 are the mirror image and are the ones to weigh most carefully. A
false finding costs a reviewer time and is self-correcting — someone reads the code
and closes it. A false **safe** label costs nothing and is never revisited, because
nothing draws attention back to a call site the tool has already cleared. Both are
ranked alongside the P0s, and 10 is deliberately sequenced *after* OI-8 rather than
before, since its fix depends on detection OI-8 provides.

Issue 11 is the worst of the set on the evidence available: the shape it misses —
a base-query constant with a clause concatenated onto it — is how most hand-written
DAOs build a filtered query, and it fails in both directions at once, emitting no
finding *and* certifying the call site as safe. It is P0 despite being the most
expensive to fix, because the two cheaper fixes either side of it (OI-8, OI-10)
leave it untouched.

### Issue ids and lifecycle

Each issue carries a stable `OI-n` id **in addition to** its section number,
because section numbers do not survive the move to
[`src2sink-closed-issues.md`](src2sink-closed-issues.md). Cite `OI-n` — never `§n` —
from test docstrings, commit messages, and code comments.

When an issue is fixed it is **removed from this document** and its section moved
verbatim to the closed-issues document, with a fix description and the commit sha
appended. This file is therefore always and only the open set: its length is the
backlog. See the closed-issues header for the exact move procedure.

---

## 6. Cross-cutting principle

Three of these four defects share one shape: **a detection path that fails to empty without emitting a signal.**

- An empty bindings file disabled all client detection (fixed in 1.1.0 by a hard error plus a manifest count).
- A guard that never matches produces zero nodes and no note (§2).
- An unparsed dependency format produces `dependencies_internal: []` and no note (§3).

The 1.1.0 work established the right pattern — the manifest binding count, the unconditional `service-call-unmatched.jsonl`, the recorded oversized-file skips. Extending it consistently is the durable fix: **any detection input that resolves to nothing should say so in the run manifest or the repo's notes.** A count of zero is a finding; an absent field is not.

---

## 7. The `sql` family matches on method name alone  `OI-7`

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

## 8. SQL built by formatting produces no node at all  `OI-8`

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

## 9. An outbound request carrying a SQL payload has no home family  `OI-9`

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

## 10. `parameterised` is reported as a safety property it cannot establish  `OI-10`

**Severity:** High — a false *safe* label is more dangerous than a false finding.

**Introduced by:** the OI-7 fix (commit `1339b60`, 1.2.0-dev). The 1.1.0 field was differently wrong (`"?" in call_text or ":" in call_text`); this section is about the replacement, not the original.

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

## 11. A base-query constant hides the concatenation appended to it  `OI-11`

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
