# `api-clients.json` — purpose, authoring, and auto-discovery

This document answers three questions:

1. **What is `api-clients.json` for?**
2. **If I ask another AI agent to add more entries, what should it look for?**
3. **Could entries be added automatically during the first-pass codebase scan?**

---

## 0. Generating `api-clients.json` from scratch

There is no fully-automatic generator — a binding's `target_repo` resolution and
`payload_fields` are domain judgements (see §3). But the metabase itself is the
raw material, and the **discovery flow** (§3) drafts candidates for you. The
end-to-end procedure:

1. **Bootstrap the file.** It is gitignored (it exposes internal service
   topology), so copy the committed template:
   ```bash
   cp api-clients.example.json api-clients.json
   ```
   Omitting `--api-clients` entirely turns the feature off. But passing
   `--api-clients` with a file that loads **zero** bindings is now a hard error:
   it silently disables every cross-repo client-detection path while the run
   still reports success, which is exactly how almost every caller of one service
   once went missing from a real fleet's graphs (ADR-011 in
   [`architecture.md`](architecture.md)). Use `--allow-empty-api-clients` if you
   genuinely want to accept that.

2. **Scan once without bindings** to produce the metabase, which surfaces the
   candidates:
   ```bash
   uv run src2sink-build --discover-api-clients
   ```
   `--discover-api-clients` writes `metabase/api-clients.discovered.json` (§3).
   Without the flag you can still mine `graphs/service-call-graph.md`,
   `graphs/payload-endpoint-producers.md`, and per-repo pages by hand for
   client libraries (artifacts/packages ending `-client`/`-api-client`/`-sdk`,
   `@FeignClient`/Retrofit interfaces, `RestClient`/`WebClient` wrappers).

3. **Review candidates.** Open `api-clients.discovered.json` and set each
   candidate's `status` to `accepted` (or `rejected`), tightening
   `import_prefix` / `paths` / `payload_fields` as needed. Nothing is merged
   automatically.

4. **Promote the accepted ones** into the authoritative file:
   ```bash
   uv run src2sink-build --promote-api-clients --api-clients api-clients.json
   ```

5. **Re-run with the file active** so the bindings take effect at worker init:
   ```bash
   uv run src2sink-build --api-clients api-clients.json
   # same flag applies to src2sink-trace and src2sink-trace-batch
   ```

The two-pass shape (discover → promote → re-run) is intrinsic: bindings must be
loaded at worker init *before* extraction, so a binding discovered in one pass
cannot influence that same pass. See the field reference in §2 before editing
entries by hand.

---

## 1. Purpose

`api-clients.json` is a **runtime-loaded registry that maps a first-party HTTP
*client library* to the backend *service* it talks to**. It is the on-disk,
gitignored form of the `ApiClientBinding` dataclass in
`src2sink/known_api_clients.py`.

### Why the tool needs it

`src2sink` statically scans a fleet of repositories and builds a "metabase"
graph of security-relevant nodes (sources, sinks, propagators) plus a
cross-service call graph used for taint / data-flow analysis.

The problem it solves: **client→server HTTP edges usually cannot be inferred
from source code alone.** When a repo consumes another team's service, it does
so through a *published client library* (typically a generated Maven client
JAR). The consuming code only ever does something like:

```java
import com.example.acme.sqlrunner.client.SqlRunnerApiClient;
...
SqlRunnerApiClient client = ...;
client.runQuery(sql);
```

There is **no literal URL, host, or target-service name** in the consumer's
source — the endpoint is baked into the compiled client. A static scanner sees
an import and a method call and cannot know they cross a service boundary or
carry a sensitive payload.

A binding closes that gap. It tells the scanner: *"this import prefix / this
artifact / this class name means a call to **that** target repo, over **these**
API paths, carrying **these** payload fields (e.g. `sql`)."*

### Where bindings are consumed

Bindings are loaded once per process at scan start via the single entry point
`known_api_clients.configure_from_path()` (used by `src2sink-build`,
`src2sink-trace` and `src2sink-trace-batch`), which configures the binding
registry *and* the http-out class patterns together — no caller can wire up one
and forget the other. They then drive:

| Consumer | Function | Effect |
|---|---|---|
| Import scan | `binding_for_import()` in `extractors/regex_extractors.py::extract_api_client_imports` | Import lines matching `import_prefix` become `propagator` nodes, family `api-client-consumer`, tagged with `target_repo`, `paths`, and `data_class="raw-sql-payload"`. |
| Call-site scan | `get_binding_call_patterns()` (built from `class_patterns`) in `extractors/http_out.py` + `extract_http_outbound` | Uses of the concrete client classes become `http-out` nodes stamped with the binding's `target_repo` and `client_paths`. |
| Service-alias resolution | `binding_target_for_text()`, called from `enrich_http_out_detail` | A call site with no host literal still resolves if the surrounding code *names* the service — a base-URL helper (`get_<service>_base_url`) or a `${<service>.base-url}` config key. |
| Host index | `binding_alias_index()` merged in `graph_common.build_repo_alias_index` | `service_aliases` resolve outbound hostnames whose DNS name differs from the repo short name. Repo records still win on conflict. |
| Service-call graph | `_collect_api_client_edges` in `aggregators/service_call_collect.py` | `api-client-consumer` nodes become cross-repo `CallEdge`s (`high` confidence; `target_path="*"` when the binding declares several routes) and appear in `service-call-edges.jsonl` and the OpenAPI edge graph. |
| Cross-repo aggregation | `binding_for_coordinate()` + `get_bindings()` in `aggregators/payload_producers.py` | Stitches consumer-repo → `target_repo` edges and emits the **payload-endpoint-producers** report (who sends raw SQL / dangerous payloads to which service). |
| Coverage reconciliation | `_render_binding_coverage` in `aggregators/service_call_report.py` | Every configured binding is reconciled against the edges it produced; a binding with **zero** edges is reported as a detection failure rather than an empty graph. |

### Why it is gitignored

Real bindings expose internal service topology and artifact names, so
`api-clients.json` is **gitignored and must not be committed**. The committed
template is `api-clients.example.json`. Activate bindings by passing
`--api-clients api-clients.json` to `src2sink-build`, `src2sink-trace`, and
`src2sink-trace-batch`. Omitting the flag leaves the feature off. Passing it with
a missing/invalid/empty file is a hard error on all three CLIs (override with
`--allow-empty-api-clients`), because the alternative — a successful-looking run
with every cross-repo client hop missing — is far more expensive than a failed
one. `load_api_client_bindings` itself still never raises; the decision lives in
`configure_from_path`, and the loaded count is recorded in `run-manifest.json` as
`api_clients_binding_count`.

---

## 2. Authoring guidance — what an agent should look for

### Field reference

Each entry in the `bindings` array becomes one `ApiClientBinding`:

| Field | Required | Meaning | Matching semantics |
|---|---|---|---|
| `target_repo` | yes | Metabase repo id (`group/name`) of the **service** that receives the calls. | Exact string; must match a repo id in the fleet for edges to resolve. |
| `maven_artifact` | yes | Artifact id (or a distinctive substring) of the client library. | **Substring** (`binding.maven_artifact in artifactId`) — keep specific. |
| `import_prefix` | yes | Package prefix under which the client classes live. | **Substring** of the import/package line. Avoid over-broad prefixes like `com.example`. |
| `paths` | recommended | API endpoint templates the client exposes. | Used for enrichment / reporting. |
| `payload_fields` | optional (default `["sql"]`) | Security-relevant request-body field names. | Feeds `data_class` / payload-producer analysis. |
| `service_aliases` | optional | Other names the service is known by. | Host/URL matching. |
| `class_patterns` | optional | Concrete client class names. | Compiled to regex (`re.escape`-joined) for call-site matching. |

### Discovery heuristics — tell the agent to find, per client library:

1. **The client library module** — repos/modules whose artifact or package name
   ends in `-client`, `-api-client`, `-sdk`; OpenAPI-generated clients
   (openapi-generator configs, `@Generated`), Feign `@FeignClient` interfaces,
   Retrofit `@GET/@POST` interfaces, or Spring `RestClient`/`WebClient` wrappers.
2. **The owning service** — which repo *publishes* that client (often a `-client`
   submodule of the service repo, or a `settings.gradle` include). Resolve to its
   metabase repo id `group/name`. **This is exactly what the new
   `repo_utils._build_component_identity_index` produces** — coordinate →
   `clone_path` — so it can be reused for target resolution.
3. **The import prefix / package** — the Java package (or JS/Python/Go module
   path) the client classes sit under → `import_prefix`.
4. **Concrete client class names** — `*Client`, `*ApiClient`, Feign interfaces →
   `class_patterns`.
5. **Endpoint paths** — from the client's method annotations
   (`@RequestMapping`, `@GetMapping`, Retrofit `@GET`) or the server's
   controllers → `paths`.
6. **Sensitive payload fields** — request-body field names worth tracking
   (`sql`, `query`, `command`, `script`, `template`, `path`, `url`) →
   `payload_fields`. This is a **judgement call**, not mechanical.
7. **Service aliases** — `spring.application.name`, k8s service names, config
   hostnames → `service_aliases`.

**Caution to pass to the agent:** `import_prefix` and `maven_artifact` are
matched with Python `in` (substring), so make them specific enough to avoid
false positives but general enough to survive version bumps. A single wrong or
over-broad binding poisons the taint graph with phantom cross-service edges.

### In-house HTTP wrappers: when to declare `class_patterns`

Outbound call sites matched on a broad receiver — `client.post(...)`,
`self.post(...)` — are only trusted when the enclosing file also shows HTTP
evidence, otherwise the pattern would match every Mapping-like helper in the
fleet. That evidence is either a recognised HTTP type, a transport-agnostic
signal (`HttpStatus`, `MediaType`, `Authorization`, `Bearer`, `status_code`), or
a route-like constant declared in the same file.

A wrapper that satisfies **none** of those — no library name, no status or auth
handling, and a route that arrives from configuration rather than a constant —
is still invisible. Declaring the wrapper class as a binding `class_patterns`
entry is the intended remedy: those patterns run in an **unguarded** tier, so
they match regardless of file-level evidence.

That tier is also language-agnostic and matched as a plain substring, which is
exactly why the pattern must be distinctive. `Client`, `ApiClient` or
`ServiceGateway` will match across the fleet and manufacture phantom edges;
name the concrete class (`StockTransportClient`), not its suffix.

---

## 3. Discovery flow (`--discover-api-clients` / `--promote-api-clients`)

Bindings **are** auto-drafted as *candidates* — never authoritative entries.
Implemented in `src2sink/aggregators/api_client_discovery.py`.

### How it works

Discovery runs in the **aggregation phase** (whole fleet in memory), so it needs
no new extractor and is unaffected by the worker-init ordering constraint below.
For each per-repo record it:

1. scans the consumer's `dependencies_internal` for an artifact whose id ends in
   a client suffix (`-client`, `-api-client`, `-rest-client`, `-client-java`,
   `-java-client`, `-sdk`, `-sdk-java`);
2. resolves that artifact's coordinate to the **publishing** repo via
   `repo_utils._build_component_identity_index` → the candidate `target_repo`;
3. pulls the target repo's `http-in` node paths → candidate `paths`;
4. defaults `import_prefix` to the dependency's `groupId` and `payload_fields`
   to `["sql"]` (both flagged for review — see §2).

### The candidate file

`--discover-api-clients` writes `metabase/api-clients.discovered.json`:

```json
{
  "candidates": [
    {
      "status": "pending",
      "confidence": "high",
      "target_repo": "acme/sql-runner-api",
      "maven_artifact": "sql-runner-api-client",
      "import_prefix": "com.example.acme",
      "paths": ["/query"],
      "payload_fields": ["sql"],
      "service_aliases": ["sql-runner-api"],
      "class_patterns": [],
      "evidence": {
        "coordinate": "com.example.acme:sql-runner-api-client",
        "consumers": ["acme/reporting"],
        "resolved": true,
        "paths_from_target_scan": true
      }
    }
  ]
}
```

- **`confidence`** — `high` = coordinate resolved to a scanned fleet repo *and*
  paths were found; `medium` = resolved but not scanned / no paths; `low` = the
  artifact looks like a client but the coordinate did not resolve (likely an
  external/published client whose service isn't in the fleet — set `target_repo`
  yourself).
- **`status`** — reviewer-owned: set to `accepted` or `rejected`. A non-`pending`
  status **and any fields you tuned** are preserved verbatim when discovery is
  re-run; only `confidence`/`evidence` refresh.
- The file has the **same sensitivity** as `api-clients.json` (internal service
  topology) and is gitignored.

### Promote

`--promote-api-clients` merges **only `accepted`** candidates into the file given
by `--api-clients` (default `api-clients.json`), matched by
`(target_repo, maven_artifact)`: a new coordinate is appended, an existing one is
updated in place. It is **idempotent** and **never auto-runs** — the sensitive
authoritative file stays human-curated, so an unverified binding can't poison the
taint graph with phantom cross-service edges.

### The ordering constraint (why it's two-pass)

Bindings load at **worker init, before extraction** (`_worker_init`), so a
binding discovered in one pass cannot influence that same pass. Discovery is
therefore intrinsically **discover → promote → re-run** (see §0 for the full
command sequence).

### What still needs a human

- `target_repo` when the client is an external/published artifact not in the fleet.
- `payload_fields` — which body field is *sensitive* is a domain judgement;
  `sql` is a fleet-specific default.
- `import_prefix` — defaulted to `groupId`, which is often too broad; tighten it.
- False positives — a `-client` suffix is a hint, not a guarantee.

## 4. Review criteria — validating discovered candidates before promotion

§3 is now implemented: `--discover-api-clients` writes candidates and
`--promote-api-clients` merges the ones whose `status` a reviewer set to
`accepted`. This section is the **acceptance standard** applied at that review
step. It exists because `promote` merges on trust — it performs no validation of
its own, so every check below is the reviewer's responsibility, and anything not
caught here reaches the taint graph.

The gates are ordered cheapest-first and are **disqualifying**: a candidate
failing any gate is rejected outright rather than weighed against its other
evidence. Counts in brackets are from the first full review of a 746-repo fleet
(191 candidates), given as calibration for what a normal distribution looks like.

### Gate 1 — Can the binding resolve at all? *(rejected 8)*

`target_repo` must be non-empty **and** exactly match a metabase repo id.

An empty `target_repo` is the resolver having failed, not a service without a
name. Per the field reference above, matching is exact-string, so such a binding
can never match a node — it is inert at best and misleading in review at worst,
because it still occupies a candidate slot and carries a confidence value.

### Gate 2 — Does `target_repo` name the service, or the client library? *(rejected 42)*

`target_repo` must be the **service that receives the calls**, never the
repository that publishes the client library.

This is the gate that rejects the most, and the one most easily missed, because
such candidates look perfectly well-formed: real repo id, real artifact, high
confidence. The failure is semantic. When a client library lives in its own
repository — `commerce/warehouse-service-client` published alongside
`commerce/warehouse-service` — coordinate resolution correctly identifies where
the artifact is *declared*, which is the client repo. The binding then points at
the library rather than the service it fronts, and every edge it produces is
attributed to the wrong node.

The convention is unambiguous and worth checking mechanically: **of the 99
hand-authored bindings, zero name a `*-client` / `*-clients` / `*-sdk` repo.**
Any candidate that does is wrong by construction.

```python
CLIENTY = re.compile(r'(-client|-clients|-sdk|-api-client)$', re.I)
if CLIENTY.search(target_repo.split('/')[-1]):
    reject("target_repo names the client library, not the service it fronts")
```

Do **not** repair these by guessing the sibling service. Only a third of them had
an identifiable sibling in the fleet, and inventing a target reintroduces exactly
the broken-edge class the resolution work removed. Reject, and report the
pattern upstream.

### Gate 3 — Is there anything to match on? *(rejected 26)*

After Gate 4's tightening, a candidate must retain **at least one of** `paths` or
`class_patterns`.

These are the only two matching mechanisms: `paths` resolve route matches,
`class_patterns` recognise a hand-rolled caller at its call site. A binding with
neither contributes nothing and cannot be falsified by any later run — it simply
sits in the registry looking like coverage.

Note this is common and not a sign of a bad candidate generator: it usually means
the *target's* own endpoints were never detected, so there were no paths to
attach. The binding is still unusable.

### Gate 4 — Tighten `class_patterns` *(113 patterns stripped)*

`class_patterns` compile to a regex matched against call sites, so a loose
pattern produces phantom edges. Strip any pattern that is:

| Reject | Why | Examples |
|---|---|---|
| Test artefacts | Not shipped; matches test scaffolding across unrelated repos | `test_*`, `conftest`, `probe_*`, `trigger_*`, `load_test*` |
| `snake_case` identifiers | A Python function name, not a client class | `lambda_function`, `nim_client` |
| Anything with non-identifier characters | Cannot be a class name | `Some-Hyphenated-Name`, `Product2.0` |
| Flagged by the tool's own warning | Appears in more than `MAX_PATTERN_REPOS` repos | `DefaultApi` (10 repos), `HealthCheckService` (4) |

The `DefaultApi` case is worth calling out: it is the default class name emitted
by OpenAPI generators, so it is simultaneously a *genuine* client class and
useless as a discriminator. Generic-but-real is still a reject.

Calibration: **0 suspect patterns across all 99 hand-authored bindings**, versus
27 of 101 distinct patterns in the discovered set. If a review is stripping
nothing, the filter is probably not running.

### Gate 5 — Rank the surviving evidence

Everything reaching this point is acceptable. `discovery_method` orders how much
independent support it has:

| method | strength | why |
|---|---|---|
| `both` | strongest | a declared dependency **and** an observed call site resolved to the same service — two independent paths agreeing |
| `dependency` | medium | a declared coordinate; says the consumer *links* the client, not that it calls it |
| `call-site` | weakest | rests on path matching, which `OI-1` showed can be wrong |

**`paths` is not independent evidence.** It is populated from the target's own
scanned endpoints, keyed on `target_repo` — so a candidate whose paths are all
real tells you the target resolved to a scanned repo, and nothing whatever about
whether the consumer calls those paths. Treating path agreement as corroboration
double-counts a single fact. In the reviewed set every candidate with paths had
100% real paths, which measures the keying, not the finding.

### Post-conditions — assert these after `--promote-api-clients`

`promote` validates nothing, so verify the merged file directly:

```
empty target_repo        == 0
client-library targets   == 0
suspect class_patterns   == 0
original bindings intact == all pre-existing (target_repo, maven_artifact) keys present
duplicate keys           == unchanged from before the merge
```

Two known behaviours of `promote` to account for:

* **It rewrites the file as `{"bindings": [...]}` and drops every other
  top-level key**, including the `_comment` that carries this file's handling
  notice. Restore it after promoting, or the "never commit" marker silently
  disappears from a gitignored, sensitivity-marked file.
* **It indexes by `(target_repo, maven_artifact)`**, so if the authoritative file
  already contains duplicate keys, only the last copy of each is updated and the
  earlier ones go stale. Deduplicate before promoting, not after.

### Handling note

`api-clients.json` is gitignored as internal topology. `--discover-api-clients`
writes `metabase/api-clients.discovered.json`, whose own header declares **"same
sensitivity as that file"** — it contains the same service names, artifacts and
endpoint paths, plus consumer lists. Confirm it, and the `metabase/repos/` tree,
are covered by `.gitignore` before any commit; matching the authoritative file's
protection is not automatic.

---

## References

- `src2sink/known_api_clients.py` — `ApiClientBinding`, loader, matchers.
- `src2sink/extractors/regex_extractors.py` — `extract_api_client_imports`.
- `src2sink/extractors/http_out.py` — `configure_http_out_client_patterns`, `_BINDING_CLASS_RX`.
- `src2sink/aggregators/payload_producers.py` — cross-repo edge + report builder.
- `src2sink/aggregators/api_client_discovery.py` — candidate discovery + promote (§3).
- `src2sink/build_metabase_v2.py` — `_worker_init`, `--api-clients` /
  `--discover-api-clients` / `--promote-api-clients` wiring.
- `api-clients.example.json` — committed template.
