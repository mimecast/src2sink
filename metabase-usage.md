# Metabase Usage Reference

The metabase (also called the knowledge graph) is a human-readable, LLM-consumable corpus of facts about the organizations source-code ecosystem. It exists to give SAST analysis the cross-repo and second-party-library context that a single-repo scan cannot derive from source alone.

This reference describes:
1. What the metabase contains
2. Where to find it
3. How to consult it for source/sink resolution, taint propagation, sensitivity tagging, and crypto-agility annotations
4. How metabase findings interact with the per-vulnerability triage rules

**Handling:** the metabase is classified **RESTRICTED**. It is, by design, a
concentrated map of where the exploitable weaknesses and personal data are across
an entire estate. Reason from it and cite it in the analysis you were asked for,
but do not copy its contents into public issues, external services, or any
audience wider than that report.

**This document is a snapshot**, written against src2sink 1.0.3
(`schema_version: 2`). The repository is authoritative: if something here does not
match what you find, or you need detail this reference does not cover, fetch it.
These URLs track `main` and return raw Markdown, so they stay current as the
project changes:

- **Field, node-family and edge definitions** —
  <https://raw.githubusercontent.com/mimecast/src2sink/main/SCHEMA.md>
  This document explains how to *use* the outputs; `SCHEMA.md` defines precisely
  what every field and value means. Fetch it before relying on the exact
  semantics of `family`, `kind`, `confidence`, or `pii_classification`.
- **Building and refreshing the metabase** —
  <https://raw.githubusercontent.com/mimecast/src2sink/main/README.md>
- **Handling, classification, and retention** —
  <https://raw.githubusercontent.com/mimecast/src2sink/main/docs/operations-security.md>
- **Repository** — <https://github.com/mimecast/src2sink>

---

## 1. What the metabase contains

The metabase is built by `src2sink-build` (`src2sink/build_metabase_v2.py`), which walks a fleet of cloned source repos and writes structured output to a configurable `--metabase-root` directory. The canonical format is the **v2 flow graph** (`schema_version: 2`).

### Per-repo flow graph

For each repo, two files at `repos/<group>/<name>.{md,json}`:

- **`.md`** — human-readable summary (frameworks, dependency count, node counts per family)
- **`.json`** — machine-readable flow graph:

```json
{
  "schema_version": 2,
  "group": "acme",
  "name": "sql-runner-api",
  "primary_language": "java",
  "frameworks": ["spring-boot"],
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

Every node has:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable node identifier |
| `kind` | `source` \| `propagator` \| `sink` \| `store` | Role in the flow |
| `family` | string | Fine-grained category (see below) |
| `file` | string | Repo-relative source path |
| `line` | int | Line number |
| `language` | string | `java`, `kotlin`, `python`, `go`, `typescript`, `javascript` |
| `framework` | string \| null | Detected framework (e.g. `spring`) |
| `pii_classification` | string \| null | `direct-pii`, `sensitive`, `special-category-gdpr`, `quasi-id` |
| `data_class` | string \| null | `dangerous-payload`, `tenant-content`, `credential`, `raw-sql-payload`, … |
| `confidence` | `high` \| `medium` \| `low` | Extraction confidence |
| `detail` | object | Family-specific fields (see family table) |

**Node families relevant to SAST:**

| `family` | `kind` | `detail` fields | SAST relevance |
|----------|--------|-----------------|----------------|
| `http-in` | source | `method`, `path`, `raw`, `framework` | Entry points / attack surface |
| `http-out` | sink | `host`, `http_method`, `path`, `url` | Cross-repo call edges; upstream attack surface |
| `sql` | source or sink | `execution`, `parameterised`, `symbol`, `raw` | `execution=true, parameterised=false` → SQL injection sink |
| `file` | sink | `symbol`, `raw` | Path traversal / arbitrary write |
| `queue-pub` / `queue-sub` | sink / source | `system`, `topic` | Messaging topology |
| `pii-field` | source | `field_name`, `pii_class` | GDPR-sensitive field declarations |
| `pii-log` | sink | `field_name` (null if no PII token in ±200 chars) | PII egress via logging |
| `pii-storage` | sink | `field_name` (null + `confidence=low` if no PII token in ±120 chars) | PII egress via persistence |
| `crypto-algorithm` | sink | `algorithm`, `mode`, `key_source`, `agility` | Weak/non-agile crypto |
| `crypto-key-source` | propagator | `source_type` (KMS/env/hardcoded/…) | Key provenance |
| `raw-code-payload` | source | `endpoint_path`, `field_line`, `sink_line`, `sink_symbol` | HTTP endpoint accepts SQL/code payload and has in-file SQL execution sink — highest-priority injection finding class |
| `api-client-consumer` | propagator | `client`, `target_repo`, `import`, `paths` | Import of a registered internal client library. The **only** evidence of the hop when the endpoint is compiled into the client — regular SAST sees an import and a method call and cannot know they cross a service boundary |
| `path-constant` | reference | `path`, `symbol` | Route-like constant / enum member (`PATH_QUERY = "/v1/queries"`). Resolves call sites that build their URL from a named constant declared in another file |
| `config-store` | store | `kind`, `url_key` | JDBC/Mongo/Redis/S3 from config files |
| `config-security` | store | `key`, `value`, `severity_signal` | Security-sensitive config flags |
| `config-crypto` | store | `key`, `algorithm` | Cipher suites / signing algorithms from config |
| `data-class-field` | source | `field_name`, `data_class` | Dangerous-payload or credential field declarations (not PII) |

Intra-file edges (in `edges[]`) connect a `source` node to a `sink` in the same file where the extractor confirmed the path.

### Internal-library taint tables

For each internally developed library, `internal-libraries/<maven-coord>.md` lists its public API with hand-curated taint roles:

| `taint_role` | Meaning |
|---|---|
| `sink` | Arguments reach a raw execution sink; treat callers as injection sinks |
| `propagator` | Arguments pass through unchanged to the underlying sink |
| `sanitiser` | Method parameterises / escapes before calling the underlying sink |
| `opaque` | Auto-curated but not yet human-reviewed; treat as sink until confirmed |
| `none` | No taint effect |

This is the single biggest source of false-positive reduction for injection findings. An internal `DbHelper.executeRaw(String sql)` will not appear as a `sql` sink node in the calling repo unless its taint table marks it as one.

### Cross-repo taint catalogs

Aggregated across all repos at `taint/`:

| File | Content |
|------|---------|
| `taint/sql-sources.md` + `.jsonl` | String-concat SQL (injection sources) |
| `taint/sql-execution-sinks.md` + `.jsonl` | JDBC/JPA/ORM execution sinks |
| `taint/file-sinks.md` + `.jsonl` | Filesystem write / archive extract |
| `taint/http-sinks.md` + `.jsonl` | Outbound HTTP calls |
| `taint/pii-sources.md` + `.jsonl` | PII field declarations (bounded MD, full jsonl) |
| `taint/pii-sinks.md` + `.jsonl` | PII log and storage sinks |
| `taint/raw-code-payload-endpoints.md` + `.jsonl` | Endpoints accepting SQL/code payloads with in-file sinks |
| `taint/crypto-operations.md` + `.jsonl` | Crypto algorithm use fleet-wide |
| `taint/config-data-stores.md` + `.jsonl` | Data store endpoints from config |
| `taint/config-security.md` + `.jsonl` | Security-sensitive config keys |
| `taint/config-crypto.md` + `.jsonl` | Cipher suites / signing algorithms from config |

The `.jsonl` sidecars are the machine-readable form; each line is a JSON object with at minimum `repo`, `file`, `line`, and family-specific fields.

### Cross-repo graphs

At `graphs/`:

| File | Content |
|------|---------|
| `graphs/service-call-graph.md` | Sampled cross-repo HTTP edges |
| `graphs/service-call-edges.jsonl` | Full edge list: `source_repo`, `target_repo`, `target_path`, `confidence`, `evidence` |
| `graphs/service-call-unmatched.jsonl` | Outbound call sites that produced **no** edge, with `reason`. Read this before concluding a service has no callers — an empty edge list can mean "no callers" or "detection failed", and only this file tells you which |
| `graphs/queue-graph.md` + `.jsonl` | Topic → producers / consumers with queue system type |
| `graphs/data-store-graph.md` + `.jsonl` | Store → repos that read/write it |
| `graphs/payload-endpoint-producers.md` + `.jsonl` | Registered API client → target service dangerous endpoints |
| `graphs/pii-flow.md` | PII touchpoints by GDPR class and top repos |
| `graphs/pii-lifecycle.md` + `.jsonl` | PII lifecycle stage touchpoints (collect / process / store / transmit / log / encrypt / delete) |
| `graphs/pii-phone-cross-repo.md` + `.jsonl` | Cross-repo phone-number hops (phone is the canonical PII example) |
| `graphs/traces/<target>.md` | Pre-computed bidirectional endpoint traces |
| `ropa/categories-of-personal-data.md` | GDPR Article 30 projection |
| `conventions/auth-models.md` + `.jsonl` | Per-repo auth pattern cards |
| `conventions/crypto-agility.md` + `.jsonl` | Per-repo crypto maturity cards |

### PII and sensitivity tagging

PII classification is carried directly on nodes (`pii_classification` field) and aggregated in `taint/pii-sources.md`, `graphs/pii-lifecycle.md`, and `ropa/`. The classification values are:

| Value | Meaning |
|-------|---------|
| `direct-pii` | Directly identifies a person (name, email, national ID) |
| `sensitive` | Sensitive but not special-category (financial, health adjacent) |
| `special-category-gdpr` | Article 9 data (health, biometric, religion, …) |
| `quasi-id` | Can identify when combined (postcode, DOB, device ID) |

The `data_class` field on non-PII nodes carries business-data sensitivity: `dangerous-payload` (SQL/code fields), `credential`, `tenant-content`, `raw-sql-payload`.

### Crypto usage and agility

The `crypto-algorithm` family captures: `algorithm`, `mode`, `key_source`, and `agility`:

| `agility` | Meaning |
|-----------|---------|
| `hardcoded` | Algorithm and/or key baked into source; rotation requires code change and redeploy |
| `config-driven` | Algorithm chosen by config; rotatable without code change |
| `pluggable` | Provider/strategy pattern; fully agile |

`hardcoded` crypto that becomes weak cannot be remediated at incident speed. Flag it regardless of current algorithm strength.

---

## 2. Where to find it

In order of precedence:

1. **A path supplied by the user** in the request (e.g., "the metabase is at `/srv/sast-context`"). User intent always wins.
2. **Environment variable `SAST_METABASE_DIR`** — operator-set override.
3. **`./metabase/`** at the working-tree root or workspace root — the conventional default.

If none of the above resolve, proceed without the metabase and **note in the report** that cross-repo taint, second-party library taint resolution, and sensitivity-aware data-flow analysis were **not performed**. This is a real coverage gap and the user should know.

If the metabase folder exists but is partially populated, record what's available and what's missing in the run header. Use what's there; don't block the scan waiting for completeness.

Confirm the metabase is current-generation before relying on it:

```
repos/<group>/<name>.json   →  "schema_version": 2   ✓ current
```

A file without `schema_version` is a legacy artefact; treat it as absent for machine-readable purposes and read the `.md` sidecar instead. The node and edge
vocabulary those files use is defined in `SCHEMA.md`
(<https://raw.githubusercontent.com/mimecast/src2sink/main/SCHEMA.md>).

---

## 3. How to consult the metabase

### When scanning a fresh repo (Mode A)

Before walking the source code, do this in order:

1. **Find the per-repo entry.** Read `repos/<group>/<name>.json` (machine) and `repos/<group>/<name>.md` (human summary). Check `schema_version: 2`.
2. **Survey the node families present.** Look at what families appear in `nodes[]`. A repo with `raw-code-payload` nodes is a top-priority target. A repo with no `http-in` nodes is not a direct entry point.
3. **For each internal dependency** in `dependencies_internal[]`, read `internal-libraries/<coord>.md` to resolve taint roles. If the file exists and a method is marked `sink` or `propagator`, callers in the source must be treated as taint sinks even if they look like clean helper calls.
4. **Check the cross-repo call graph** — `graphs/service-call-edges.jsonl` filtered to this repo as `source_repo` or `target_repo` — to understand what data crosses repo boundaries. An `http-out` node whose host matches another repo's `http-in` path is a cross-repo taint edge; so is an edge whose evidence names an api-client binding, where the caller's source contains no URL at all. Then check `graphs/service-call-unmatched.jsonl` for this repo: those are outbound calls whose target could **not** be resolved, i.e. hops that are real but unmapped.
5. **Check queue topology** — `graphs/queue-graph.jsonl` filtered to this repo — to find upstream producers and downstream consumers.
6. **Check PII sensitivity** — `taint/pii-sources.jsonl` filtered to this repo — for field-level classifications. Cross-reference against how the repo handles those fields (logged, stored, transmitted).

When walking the source, treat metabase-declared sinks (including internal-library `sink`/`propagator` methods) as authoritative — even if the local code looks clean, taint reaching a declared sink is a finding.

### When triaging an external SAST report (Mode B)

For each input finding:

1. Find the metabase entry for the repo and the function / file the finding cites.
2. Check whether the sink the finding alleges is corroborated by the metabase:
   - **Metabase has a `sql` node with `execution=true, parameterised=false` at the same file/line** → upgrade confidence. Verdict starts at CONFIRMED unless source-side constraints disprove it.
   - **Metabase has no `sql` node at that location, or the node has `parameterised=true`** → examine whether an internal wrapper is involved. Check `internal-libraries/` for the callee's taint role. If the wrapper parameterises, downgrade to FALSE-POSITIVE with the metabase citation as evidence.
   - **Metabase has no entry for this repo** → fall back to the per-family triage rule and note the gap.
3. Check whether taint can actually reach the sink given the cross-repo flow:
   - If no cross-repo edge in `service-call-edges.jsonl` points to the alleged sink repo+path, the finding *may* be DEAD-CODE — but **absence of an edge is not evidence of absence of a caller**. Before concluding that, confirm coverage was not simply lost: check `run-manifest.json` for a non-zero `api_clients_binding_count`, the "API-client binding coverage" table in `service-call-graph.md` for bindings that produced no edges, and `service-call-unmatched.jsonl` for unresolved outbound calls from candidate caller repos. Only mark DEAD-CODE once those three are clean.
   - If the cross-repo edge exists but terminates at a node with `parameterised=true` or a `sanitiser`-role library method, the path is FALSE-POSITIVE *for the alleged route* — but check whether the same source feeds any other sink before clearing it.

### When deep-tracing a HIGH/CRITICAL finding (Mode C)

Use pre-computed traces first: `graphs/traces/` may already contain a `.md` for the target endpoint (generated by `src2sink-trace` or `src2sink-trace-batch`). If a trace file exists, it includes inbound routes, `raw-code-payload` nodes, SQL sinks, config stores, and known upstream callers.

For a manual trace, walk the chain explicitly using the graph artefacts:

```
[source: acme/sql-runner-api — http-in node, path=/query, line 7]
  -> [raw-code-payload node, field=sql, sink_line=17]
  -> [sql sink node, execution=true, parameterised=false, line 17]
  -> [cross-repo: graphs/service-call-edges.jsonl, source_repo=acme/reporting-service]
  -> [reporting-service http-out node pointing to /query]
  -> [internal-libraries/com.example.sqlrunner-client.md, method executeQuery → taint_role=propagator]
```

Cite the metabase file and node/line used at each step. Without metabase-cited evidence at a boundary, a cross-repo claim is speculation — mark it as such if you must make it.

---

## 4. Interaction with per-family triage

The per-family triage rules are written assuming the metabase may or may not be available. When it **is** available:

- **Injection triage** — metabase resolves whether internal library wrappers parameterise (`internal-libraries/<coord>.md`, `taint_role=sanitiser`). This is the largest single source of false-positive reduction. A `sql` sink node with `parameterised=false` is the corroborating signal that confirms the finding.
- **`raw-code-payload` findings** — these are the highest-confidence injection findings the metabase produces. They are only emitted when the extractor finds both an HTTP endpoint accepting a SQL/code-shaped field **and** a `sql` execution sink in the same file. Treat them as CONFIRMED pending source review.
- **Credential triage** — metabase `crypto-key-source` nodes tell you whether a hardcoded-looking value is actually loaded from KMS or Secrets Manager at runtime (downgrade if so, with citation). A `key_source=hardcoded` node with `agility=hardcoded` is a genuine hard-coded credential.
- **Hashing triage** — metabase `crypto-algorithm` node `detail.kind` tells you the hash's use context (`hash`, `mac`, `kdf`, `sign`). Don't downgrade to LOW for non-cryptographic use unless `detail.kind` confirms it.
- **Crypto agility triage** — `agility=hardcoded` means rotation requires a code change and redeploy. Flag non-agile weak crypto at HIGH regardless of current algorithm strength.
- **Tenant-isolation triage** — `graphs/service-call-edges.jsonl` shows which repo receives the tenant-ID claim and whether it enforces it. A tenant ID in a non-signed field that a downstream service trusts is a CONFIRMED finding even if the emitting repo looks clean.
- **PII / data-minimisation triage** — metabase `pii_classification` on nodes overrides in-repo-only judgement. If a field is classified `sensitive` or `special-category-gdpr`, treating it as uncritical in this repo is a finding. Use `confidence` to calibrate: `high` = tree-sitter confirmed; `medium` = regex heuristic; `low` = weak signal — investigate before escalating.

**Confidence levels:**

| Level | Source | How to use |
|-------|--------|------------|
| `high` | Tree-sitter call-site match or explicit registered-client import | Treat as confirmed signal |
| `medium` | Regex heuristic (field name, URL pattern) | Corroborate with source review |
| `low` | Weak signal (host-only URL match, generic `.save()` without PII token) | Flag as "investigate", not "confirmed" |

**Conflict rule:** if the metabase says one thing and the local source says another, the metabase is the source of truth for *intent* (what a method is supposed to do, what classification a field is supposed to carry). The local source is the source of truth for *behaviour* (what actually executes). When they disagree, that disagreement is itself a finding — flag it as **METABASE-DRIFT** at MEDIUM severity unless the security impact warrants higher.

---

## 5. Updating the metabase

This skill **reads** the metabase; it does not write to it. If during analysis you discover a new sink, a missing library taint entry, an undocumented cross-repo edge, or a sensitivity tag that doesn't match observed code, record it in the report under a **"Metabase Improvements"** section. The metabase is refreshed by running `src2sink-build` (full extract) or `src2sink-build --graphs-only` (re-aggregate from existing per-repo JSONs); the full
full set of invocations is in the README
(<https://raw.githubusercontent.com/mimecast/src2sink/main/README.md>).

To add a new internal client library to the producer index, edit `src2sink/known_api_clients.py` (`ApiClientBinding`) and re-run `src2sink-build --graphs-only`.

Do not silently assume facts the metabase does not state. If a fact is needed for a finding and the metabase doesn't have it, either confirm it from the source or flag it as an unresolved assumption.
