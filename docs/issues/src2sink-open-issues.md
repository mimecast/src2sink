# src2sink 1.1.0 — Open Detection Issues and Proposed Fixes

**Version reviewed:** src2sink 1.1.0
**Status:** all issues below are open in 1.1.0. Earlier defects (empty-binding silent failure, `api-client-consumer` nodes never reaching the call graph, class-name-anchored call-site regexes, constant/enum indirection, binding aliases, unmatched-ref reporting) were fixed in 1.1.0 and are not repeated here.

**Anonymisation notice:** every repository name, package name, artifact id, service name, class name, constant name and **URL path** in this document is fictitious. The worked example throughout is an invented warehouse system. References to `src2sink`'s own source (file:line) and to third-party library names appearing in `src2sink`'s regexes (`RestTemplate`, `requests`, …) are real, as those are needed to locate the code being fixed.

---

## 0. Context: how these were found

A fleet scan of several hundred repositories was used to measure detection coverage for one heavily-consumed internal service. Coverage of that service's callers in the service-call graph rose from 1 to 22 after upgrading to 1.1.0. Investigating the callers that *remained* invisible surfaced the four issues below. Three of them are general — not specific to the service used as the probe.

The running example is a fictitious service `commerce/warehouse-service`, which publishes a client library `warehouse-service-client` (group `com.example.commerce.warehouse.client`) and exposes `POST /stock`. It is consumed by a fictitious repo `fulfilment/fulfilment-commons`.

---

## 1. Version prefixes outrank real route names in path matching

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

## 2. Context guards suppress fully custom HTTP wrappers

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

## 3. Dependency parsing misses Gradle version catalogs

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

## 4. Client discovery is single-direction and never proposes `class_patterns`

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

| # | Issue | Effort | Value | Priority |
|---|---|---|---|---|
| 1 | Version prefixes outrank route names | low | fixes wrong edges, not just missing ones | **P0** |
| 3 | Gradle version catalogs unparsed | low | restores discovery input for affected repos | P1 |
| 2 | Context guards miss custom wrappers | low–medium | recovers hand-rolled callers | P1 |
| 4 | Demand-side discovery | medium | generates the field that cannot be inferred otherwise | P2 |

Issue 1 is first because it is the only one producing **incorrect output**. The others reduce recall; this one reduces precision, and a confidently wrong edge is worse than a missing one — nothing in the graph distinguishes it from a real dependency.

---

## 6. Cross-cutting principle

Three of these four defects share one shape: **a detection path that fails to empty without emitting a signal.**

- An empty bindings file disabled all client detection (fixed in 1.1.0 by a hard error plus a manifest count).
- A guard that never matches produces zero nodes and no note (§2).
- An unparsed dependency format produces `dependencies_internal: []` and no note (§3).

The 1.1.0 work established the right pattern — the manifest binding count, the unconditional `service-call-unmatched.jsonl`, the recorded oversized-file skips. Extending it consistently is the durable fix: **any detection input that resolves to nothing should say so in the run manifest or the repo's notes.** A count of zero is a finding; an absent field is not.
