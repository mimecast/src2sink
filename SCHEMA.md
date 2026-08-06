# Metabase Schema

Canonical definitions for every entry type. Both human reviewers and
the LLM consumers should rely on this document when interpreting a
metabase entry.

## Per-repo entry

File path: `repos/<group>/<repo>.md` (human-readable),
`repos/<group>/<repo>.json` (machine-readable mirror).

### `identity`

| Field | Type | Description |
|-------|------|-------------|
| `group` | string | GitLab group folder under `repos/` |
| `name` | string | Repo directory name |
| `path` | string | Filesystem path relative to `repos/` |
| `git_sha` | string | HEAD commit SHA of the analysed tree (if a `.git` directory is present) |
| `analysed_at` | ISO-8601 datetime | When the script last ran on this repo |
| `derivation_version` | integer | Which rules *interpreted* the observations into findings. Separate from `detection_version` because a rule change costs a **re-derive over existing records** — no source, no parsing — while a detector change costs a full rescan. See [`docs/plans/observe-then-classify.md`](docs/plans/observe-then-classify.md). |
| `dependencies_internal[].version_kind` | string | How well the version is known: `resolved` (from a lockfile or an exact declaration), `range` (a constraint like `^1.4.2` — **not** a version), or `unresolved`. See `OI-18`/`OI-19`. |
| `detection_version` | integer | Which detector produced this record. Together with `git_sha` it forms the incremental scan's cache key: a record whose detector is older than the running build is rebuilt rather than reused. A record without this field predates it and is always treated as stale. See `OI-16`. |

### `language`

| Field | Type | Description |
|-------|------|-------------|
| `primary` | enum {`java`, `kotlin`, `typescript`, `javascript`, `python`, `go`, `rust`, `c#`, `unknown`} | Language with the most source files |
| `breakdown` | map<string, int> | Lines-of-source-equivalent (file count) per language |

### `build_systems`

Array of detected build systems. Possible values: `maven`, `gradle`,
`gradle-kts`, `npm`, `yarn`, `pnpm`, `pip`, `poetry`, `go-modules`,
`cargo`, `dotnet`, `make`, `bazel`.

### `frameworks`

Array of detected frameworks. Possible values:

- Java: `spring-boot`, `spring-mvc`, `spring-security`, `micronaut`,
  `quarkus`, `dropwizard`, `jax-rs`, `vertx`, `play`, `helidon`
- JS/TS: `express`, `nestjs`, `koa`, `fastify`, `hapi`, `next.js`,
  `react`, `angular`, `vue`, `apollo-server`, `graphql-yoga`
- Python: `flask`, `fastapi`, `django`, `aiohttp`, `tornado`, `celery`
- Go: `gin`, `echo`, `chi`, `fiber`, `net/http`
- Other: `protobuf`, `grpc`, `graphql`, `openapi`

Each entry carries the source of the detection (`pom.xml`,
`package.json`, etc.).

### `dependencies`

Array of `{group, name, version, kind}` where `kind` is `internal`
(group prefix matches a known organization-internal namespace such as
`com.example.*`, `@example/*`, or originates from a path that
suggests a sibling repo) or `external`.

### `inbound_endpoints` (sources)

Array of `{kind, method, path, file, line, auth, framework, raw}`.

- `kind`: `http`, `grpc`, `graphql`, `queue`, `webhook`, `cli`,
  `scheduled`
- `method`: HTTP verb / queue topic / RPC service name
- `path`: HTTP path / topic name / RPC method
- `auth`: detected auth annotation (e.g. `@PreAuthorize`,
  `@Secured`, `@PermitAll`, `IS_ANONYMOUS`, `@RolesAllowed("ADMIN")`)
  or `unknown` if the controller has no inferable auth
- `framework`: which framework's annotation matched
- `raw`: the matched line (for human review)

### `outbound_http` (cross-repo sinks via HTTP)

Array of `{url, file, line, kind, raw}`.

- `kind`: `static-url`, `templated-url`, `host-from-config`,
  `service-discovery`
- For `host-from-config`, capture the config key
- The `url` field is the literal text or the templated form

### `queue_io`

Array of `{direction, system, topic, file, line}`.

- `direction`: `produce` / `consume`
- `system`: `kafka`, `sqs`, `rabbitmq`, `gcp-pubsub`, `kinesis`
- `topic`: topic / queue / stream name
- `file:line` of the producer / consumer declaration

### `data_stores`

Array of `{kind, name, file, line, ops}`.

- `kind`: `jdbc`, `jpa-entity`, `mybatis-mapper`, `mongo`, `redis`,
  `elasticsearch`, `dynamodb`, `s3`, `cassandra`, `neo4j`,
  `falkordb`
- `name`: database / table / collection / bucket / index
- `ops`: subset of {`read`, `write`, `delete`, `aggregate`, `index`}

### `sql_patterns`

Array of `{kind, file, line, snippet, confidence}`.

- `kind`: `parameterised` (PreparedStatement / parameter binding /
  named-parameter), `concatenated` (string concat in a SQL-shaped
  string), `string-template` (interpolation), `dynamic-where`
  (where-clause built from input), `raw-execute` (Statement
  .executeQuery on user input)
- `confidence`: HIGH / MEDIUM / LOW per the scoring rubric in
  README.md

### `crypto_operations`

Array of `{algorithm, mode, padding, key_source, file, line, kind,
agility, confidence}`.

- `algorithm`: `AES`, `RSA`, `HMAC-SHA256`, `BCrypt`, `Argon2id`,
  `PBKDF2`, `MD5`, `SHA1`, `SHA-256`, `Ed25519`, etc.
- `mode`: `GCM`, `CBC`, `CTR`, `ECB`, `OAEP`, `PKCS1`, `null`
- `padding`: where applicable
- `key_source`: `hardcoded`, `env`, `secrets-manager`, `kms`,
  `keystore`, `derived-from-password`, `parameter`, `unknown`
- `kind`: `encrypt`, `decrypt`, `sign`, `verify`, `hash`, `mac`,
  `kdf`, `random`, `key-load`, `key-generate`
- `agility`: `hardcoded` (algorithm baked into source),
  `config-driven` (algorithm chosen by config), `pluggable`
  (provider / strategy pattern)

### `auth_patterns`

Array of `{pattern, file, line, scope, raw}`.

- `pattern`: e.g. `@PreAuthorize`, `@Secured`, `@RolesAllowed`,
  `@PermitAll`, `IS_ANONYMOUS`, `csrf().disable()`,
  `permitAll()`, `requestMatchers().permitAll()`,
  `WebSecurity.ignoring()`, JWT validator class, custom filter
- `scope`: `endpoint` / `controller` / `global` / `package`

### `config_security_flags`

Array of `{key, value, file, line, profile, severity_signal}`.

- `key`: e.g. `security.enabled`, `enable.serverauth`,
  `csrf.disabled`, `cors.allowed-origins`,
  `management.endpoints.web.exposure.include`,
  `tls.enabled`, `verify-ssl`
- `severity_signal`: `disables-control` / `enables-control` /
  `informational`

### `pii_fields`

Array of `{field_name, classification, file, line, dto}`.

- `classification`: `quasi-id`, `direct-pii`, `sensitive`,
  `special-category-gdpr`, `unknown`
- `dto`: enclosing class / object name

### `internal_libraries`

Array of internal Internal libraries imported by this repo. Each
entry: `{coordinate, kind, taint_role}`.

- `coordinate`: `com.example.foo:bar:1.2.3` or `@example/baz@1.0`
- `kind`: `db-wrapper`, `crypto-helper`, `auth-utils`,
  `http-client`, `messaging-client`, `tenant-context`,
  `serialiser`, `logging`, `unknown`
- `taint_role`: `source`, `sink`, `pass-through`, `sanitiser`,
  `none`, `unknown` — what the library does with tainted data

### `notable_patterns`

Free-form list of human-curated observations. Auto-generation can
suggest entries but humans confirm.

---

## Internal-library entry

File path: `internal-libraries/<library>.md`.

Each library catalogues the taint surface of its public API. Fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Library coordinate |
| `purpose` | string | One-paragraph description |
| `consumers` | array | Repos that import this library |
| `public_methods_with_taint_role` | array | `{method_signature, role, sink_type, raw}` |
| `crypto_provided` | array | What crypto operations this library exposes |
| `auth_provided` | array | What auth functions this library exposes |
| `pii_handling` | array | Fields / methods that touch PII |

A `role` of `sink` with a `sink_type` (e.g. `sql`, `shell`,
`file-write`, `http-request`, `template-render`) is the critical
information: it tells consuming-repo SAST that arguments to this
method must be treated as if they reached a raw SQL / shell / etc.
sink.

---

## Taint catalog entries

### `taint/sql-sinks.md`

A canonical, cross-repo list of every method signature that should be
treated as a SQL sink. Schema per row:

| `signature` | `kind` | `defined_in` | `consumers_count` | `parameterised_safe?` |

`kind`: `prepared-statement-execute`, `statement-execute`,
`jpa-native-query`, `jdbc-template-query`, `mybatis-raw`,
`internal-wrapper-raw`, `internal-wrapper-string`.

`parameterised_safe?` = YES means the method signature *only*
accepts parameter binding (no raw string), so it is not a sink.

### `taint/http-sinks.md`

Cross-repo HTTP sinks: outbound HTTP-client methods that compose URLs
or bodies from arguments. Same schema as SQL sinks.

### `taint/file-sinks.md`

File-write methods, path-construction methods, archive-extraction
methods that may write outside a given directory.

### `taint/crypto-operations.md`

Aggregated view of every crypto call across the codebase. Each row:
`algorithm`, `mode`, `key_source`, `repo`, `file:line`, `agility`.

### `taint/pii-sources.md` and `taint/pii-sinks.md`

GDPR-axis **PII field** entry and exit points (`pii-field`, `pii-log`,
`pii-storage` nodes). business-data-class and dangerous-payload field names
live in `taint/dangerous-payload-fields.md` — not personal data.

**`pii-log`** — matches `logger.info(…)` / `log.warn(…)`-style calls.
`detail.field_name` is set only when a PII vocabulary token (`phone`,
`email`, …) appears within ±200 characters of the call; otherwise the
node is not emitted.

**`pii-storage`** — matches generic persistence patterns (`.save(`,
`.persist(`, `insertOne(`, `putObject(`, email SDK calls, etc.). Within
±120 characters the extractor looks for the same PII vocabulary:

| `detail.field_name` | `confidence` | Meaning |
|-------------------|--------------|---------|
| e.g. `email` | `medium` | Persistence call **and** a PII-like identifier nearby in source |
| `null` | `low` | Persistence (or S3/comms) call **without** a nearby PII identifier — possible write path, not proven PII (e.g. `model.save()` in ML scripts) |

`null` does **not** mean “unknown PII at runtime”; it means static
analysis could not tie the sink to a named field. Both rows are written
to `pii-sinks.jsonl`; filter on `confidence` or non-null `field_name`
when reviewing for real PII egress.

---

## Graph entries

### `graphs/service-call-graph.md` (v2)

Cross-repo edges from three independent sources:

1. `http-out` call sites matched to `http-in` path templates;
2. path/URL literals in any v2 node `detail` (config, clients, `path-constant`
   declarations);
3. hops declared by an api-client binding — an `api-client-consumer` import or a
   `class_patterns` call site, where the consumer's own source contains no host
   or URL to match on at all.

Sidecar: `graphs/service-call-edges.jsonl`. Confidence: **high** = same
normalised path, or a configured api-client binding; **medium** =
prefix/template overlap; **low** = hostname hint only; **openapi** = matched to
a discovered spec. A `target_path` of `*` is a service-level hop whose specific
route was not resolved.

Outbound call sites that produced *no* edge are written to
`graphs/service-call-unmatched.jsonl` with a `reason`, and the report reconciles
every configured binding against the edges it produced — a binding with zero
edges means client-library detection for that service is broken, which
previously surfaced only as an empty graph.

### `graphs/pii-<field>-cross-repo.md` (Phase 3)

Cross-repo hops for showcase PII fields (`phone`, `email`, `ip_address`)
where **both** repos have lifecycle touchpoints for that field. Links:
shared messaging topics (`queue-pub` / `queue-sub`) and service-call edges
(HTTP confidence not `low`). Sidecar: `pii-<field>-cross-repo.jsonl`.

### `graphs/pii-lifecycle.md` (v2, Phase 3)

Fleet-wide PII touchpoints by lifecycle stage (`collect`, `process`, `store`,
`transmit`, `log`, `encrypt`, `delete`). Sidecar: `graphs/pii-lifecycle.jsonl`.
Includes a **phone** worked-example section. Regenerated with `--phase3-only`
or on every full graph build (unless `--no-phase3`).

### `ropa/categories-of-personal-data.md` (Phase 3)

Article 30-style processing activities projected from PII lifecycle data.
Sidecar: `ropa/processing-activities.jsonl`.

### `graphs/traces/<target>.md`

Written by `metabase/scripts/trace.py` or batch via `trace_batch.py` — inbound
routes, raw-sql payload rows, SQL sinks, config stores, and upstream callers
(graph + optional `--scan-repos`).

### `graphs/payload-endpoint-producers.md`

Producer index for registered API clients (`known_api_clients.py`): Maven/
Gradle dependency, `api-client-consumer` imports, enriched `http-out` URLs,
and optional `repos/` import scan. Sidecar: `payload-endpoint-producers.jsonl`.

### `graphs/data-store-graph.md`

Each data store (DB / topic / bucket) maps to a list of repos that
read or write it.

### `graphs/queue-graph.md`

For every messaging topic, list **queue type** (`detail.system` from
`queue-pub` / `queue-sub` nodes: `kafka`, `rabbitmq`, `sqs`, `sns`,
`redis-stream`, `nats`, `jms`, …), producers, and consumers. Sidecar:
`graphs/queue-graph.jsonl` (`queue_types` array + `queue_type` string).

### `graphs/pii-flow.md`

PII flow diagram. Every node is a service or store; every edge is a
field name with classification.

---

## Convention entries

### `conventions/auth-models.md`

Documents the auth patterns the ecosystem uses, with examples per
pattern: cookie session, JWT bearer, mTLS, API key, internal-only
(network-isolated), public.

### `conventions/crypto-agility.md`

Documents whether the ecosystem is crypto-agile and how. Configurable
algorithms? Pluggable KMS? Provider abstraction?

### `conventions/pii-classification.md`

Field-name conventions to PII class mapping. E.g. `customerId` →
quasi-id; `emailAddress` → direct-pii; `dateOfBirth` →
special-category-gdpr-when-combined.

---

## Metabase v2 flow graph (`schema_version: 2`)

Produced by `src2sink/build_metabase_v2.py`. JSON **must** include `"schema_version": 2`.

### Node kinds

| `kind` | Role |
|--------|------|
| `source` | Untrusted or sensitive data enters scope (HTTP body, queue message, field declaration) |
| `propagator` | Data moves through code (auth filter, KMS decrypt, variable pass-through) |
| `sink` | Dangerous operation (SQL execute, file write, outbound HTTP, log with PII) |
| `store` | Named persistence from config (JDBC/Mongo/Redis/S3 URLs in YAML/properties) |
| `reference` | A declaration that *names* an endpoint without being a call site (route constant, path enum member). Feeds cross-repo path matching; never a taint source or sink itself |
| `finding` | **Derived**: a statement about a *relationship* between other nodes rather than about one location. Currently only `tainted-path` (`OI-17`) |

### Node families (selected)

| `family` | Typical `kind` | Notes |
|----------|----------------|-------|
| `http-in` | source | Inbound route |
| `http-out` | sink | Outbound client |
| `sql` | source or sink | **source** = string concat; **sink** = `executeQuery` / `JdbcTemplate` (see below) |
| `entry-point` | source | **Derived**: a way untrusted input enters this service. `mechanism` is `http`, `queue`, `grpc`, `graphql`, `schedule`, `cli` or `file-watch`; `channel` names the specific door; `externally_triggered` is false for a scheduled job, which is reachable but not opened by a caller. See `OI-21`. |
| `type-decl` | reference | An **observation**: a type this file declares, with `fields` (name → declared type), `supertypes` and `is_interface`. The facts a call is resolved against (`OI-17`). |
| `method-decl` | reference | An **observation**: a callable this file declares, with `class`, `method`, `params` and its `start_line`/`end_line` span. The unit a source-to-sink path is made of (`OI-17`). |
| `entry-marker` | reference | An **observation**: a non-HTTP entry mechanism was seen here. Input to the `entry-point` derivation. |
| `sql-field-marker` | reference | An **observation**: a SQL-shaped field name appears at this line. Input to the `raw-code-payload` derivation, which is why it is recorded rather than held in memory. |
| `call-site` | reference | An **observation**, not a finding: a call the extractor examined. Asserts nothing about danger. **Two shapes since 3.0.0** — see below. |
| `tainted-path` | finding | **Derived**: a value from an entry point reaches a sink. Carries the entry, the sink, every hop with its resolution tier, and the `weakest_link` (`OI-17`). |
| `file` | sink | Filesystem write / archive extract |
| `queue-pub` / `queue-sub` | sink / source | Messaging |
| `pii-field` | source | Field-name heuristic |
| `pii-log` | sink | Logger call; `field_name` set only if PII token is near the call |
| `pii-storage` | sink | `.save` / `.persist` / S3 / email SDK; `field_name` null + `confidence=low` when no PII token in ±120 char window |
| `crypto-algorithm` | sink | Literal algorithm use |
| `crypto-key-source` | propagator | Secrets Manager / KMS / Vault |
| `raw-code-payload` | source | Endpoint accepts `sql`/`query`/… and file has SQL execution sink |
| `sql-payload-out` | sink | Outbound request whose body carries a `sql`/`dql`/… field; see below |
| `api-client-consumer` | propagator | Import of a registered client library (`known_api_clients.py`); carries `target_repo` + declared `paths`, so the hop is graphed even though the consumer's source names no host or URL |
| `path-constant` | reference | Route-like string constant or enum member (`PATH_QUERY = "/v1/queries"`); recovers call sites that build their URL from a named constant in another file |

#### `call-site` `detail` — two shapes (**breaking change in 3.0.0**)

Until 3.0.0 a `call-site` was emitted only for *sink-shaped* names, and every one
carried the full SQL-evidence set. `OI-17` widened observation to **every** call,
because `stockService.process(...)` is the middle of every layered path and was
recorded nowhere — so the tool could see both ends of a path and never the part
that joins them.

Recording the full set for every call was measured at 3.4x the nodes and 4.7x the
record size on a real repository, where call sites are ~75% of all nodes. So an
ordinary call records only what resolving it needs:

| Field | Ordinary call | Sink-shaped call |
|---|---|---|
| `symbol`, `receiver`, `arguments` | ✅ | ✅ |
| `enclosing_class`, `enclosing_method` | ✅ | ✅ |
| `raw` | **absent** | ✅ |
| `receiver_is_database`, `library_hint`, `file_sql_evidence`, `parameterised` | **absent** | ✅ |

**A consumer must use `.get()` rather than `[]` on the second group.** Absent
reads as "no SQL evidence", which is the truthful default: nothing vouched for
the call being SQL because nothing looked. A call is sink-shaped when its name is
in the SQL or script-exec name sets, or its text names a SQL library.

Calls naming nothing declared anywhere in the repository are **pruned** after the
repo is parsed — `get`, `append`, `len`, `str`, `join` and the rest of the
standard library, which an *intra-repo* path cannot pass through by definition.
The prune applies the same test T3 resolution does, so it only removes calls
every tier would have dropped.

#### `tainted-path` `detail`

| Field | Meaning |
|---|---|
| `entry_mechanism`, `entry_channel` | the door: `http` + `/stock`, `queue` + a topic, … |
| `externally_triggered` | `false` for a scheduled job — reachable, but nobody outside chooses when |
| `sink_family`, `sink_symbol`, `sink_file`, `sink_line` | where the value arrives |
| `tainted_at_sink` | the name carrying the value at the sink |
| `hops` | path length, recorded **beside** confidence rather than folded into it |
| `weakest_link` | the single hop a reader should check first |
| `path` | every hop, each citing `file:line`, its resolution tier and the argument that carried the value |
| `search_truncated` | present and `true` only when the search hit its explored budget |

The node's `confidence` is the **minimum** hop confidence, never a product.
Multiplying takes eight `medium` hops to 0.058 and buries exactly the deep paths
that hold most of the value — but hops are not independent coin flips: eight
individually resolved calls with declared receiver types are not less trustworthy
than two fuzzy string matches. A reader can act on "8 hops, weakest link is the
`B→C` binding"; nobody can act on `0.058`.

There is **no confidence floor**. Paths are emitted broadly and ranked honestly,
because for an indicator a floor converts cheap false positives into expensive,
invisible false negatives.

#### `sql` sink `detail`

| Field | Values |
|-------|--------|
| `symbol` | the invoked method name (`query`, `execute`, `find`, …) |
| `receiver` | the expression the call was made on (`jdbcTemplate`, `this.stockRepository`), or `""` for an unqualified call |
| `execution` | `true` for JDBC/JPA/native execution, `false` for ORM helpers |
| `parameterised` | a posture: `parameterised`, `mixed`, `raw`, `static`, `unknown` — see below |

`execute`, `query` and `update` are ordinary method names, so a `sql` sink is
emitted only when one positive signal supports it: a database-ish `receiver`, an
explicit library name in the call text, or file-level SQL evidence (a SQL keyword
inside a string literal, or a database import). A field merely *named* `sql` is
deliberately not evidence — matching on the method name alone catalogued
`httpClient.execute(request)` and `messageDigest.update(data)` as SQL execution
sinks, and let an HTTP proxy fabricate a `raw-code-payload` finding (issue `OI-7`).

`parameterised` is a **posture, not a safety verdict**, because a placeholder does
not undo a concatenation in the same statement: `"… ref = '" + ref + "' AND id = ?"`
is injectable despite the `?`. Two independent facts about the statement executed
at the call site are reported as one label:

| Posture | Placeholders | Statement constructed | Read it as |
|---|---|---|---|
| `parameterised` | yes | no | bound parameters, nothing concatenated |
| `mixed` | yes | yes | **partially parameterised — still injectable** |
| `raw` | no | yes | assembled from parts |
| `static` | no | no | a constant statement; no input reaches it |
| `unknown` | — | — | no statement attributable to this call site |

`mixed` and `unknown` must never be read as safe. The governing rule is that weak
evidence may downgrade a posture but never establish `parameterised`: a statement
found at the call site is a fact about that call, while a literal found elsewhere
in the file is a guess, so it is only trusted when the file holds exactly one
candidate statement.

Before 1.2.0 this field was a boolean derived from `"?" in call_text`, which
labelled calls containing no SQL at all as *unparameterised* and calls next to an
unrelated safe constant as *parameterised*. Values from an older metabase are
reported as `unknown` rather than translated.

#### `sql-payload-out` vs `raw-code-payload`

The two are the ends of one cross-repo hop and neither implies the other:

| Family | Direction | Means |
|--------|-----------|-------|
| `raw-code-payload` | inbound | *this service accepts SQL* — an endpoint whose handler takes a `sql`-ish field and reaches an execution sink |
| `sql-payload-out` | outbound | *this service sends SQL* — a `sql`-ish field bound into an outbound request body |

An outbound SQL payload is neither a local `sql` sink (nothing executes here)
nor an ordinary `http-out` (the body is executable input at the far end), which
is why it needs a family of its own rather than a flag on either.

`detail` carries `field_name`, `http_out_line` and `path` (the request it rides
on), plus `target_repo`/`client` when a binding resolved the destination.
Confidence is `high` when the field was named in an `api-clients.json`
`payload_fields` list — a declaration that the receiving service treats it as
executable — and `medium` on the generic vocabulary alone.

Joining the two families across repos is not done: a `sql-payload-out` in one
repo and a `raw-code-payload` in another are recorded independently.

### Classification axes (orthogonal)

| Field | Values |
|-------|--------|
| `pii_classification` | `direct-pii`, `sensitive`, `special-category-gdpr`, `quasi-id` |
| `data_class` | `tenant-content`, `credential`, `dangerous-payload`, … |

### `enclosing_class` / `enclosing_method`

Every node that falls inside a declared callable carries the class and method it
sits in, assigned by span containment, innermost first. A node inside no method —
a field, a class-level constant — carries **neither**, rather than being
attributed to whichever method happens to be nearest: a guessed scope is worse
than an absent one.

### Observations and findings

Nodes divide in two, and the distinction is load-bearing rather than cosmetic:

* **Observations** (`call-site`, `sql-field-marker`, `kind: reference`) record
  what the extractor saw.
* **Findings** (`sql`/`sink`, `raw-code-payload`/`source`, `entry-point`/`source`) are *derived* from
  observations by `src2sink/derive.py`.

Strip the findings from a record and what remains is exactly the input they were
derived from, so re-deriving reproduces them without reading a single source
file. That is why a corrected classification costs a pass over records rather
than a fleet rescan — and why `derivation_version` is versioned apart from
`detection_version`.

### `call-site` — observations, not findings

Emitted for every call carrying a sink-shaped name, **whether or not any family
classifies it**. `kind` is always `reference`.

The point is that classification should be revisable without re-extraction. A
`call-site` records what was seen and what could be told about it, so changing
what a library *means* becomes a re-aggregation rather than a fleet rescan. See
[`docs/plans/observe-then-classify.md`](docs/plans/observe-then-classify.md) §3.

| Field | Meaning |
|---|---|
| `symbol` | the called name |
| `receiver` | the receiver expression, or `null` when the call has none |
| `receiver_is_database` | whether the receiver reads as a database handle |
| `library_hint` | whether the call text names a known data-access API |
| `file_sql_evidence` | whether the *file* shows SQL evidence — **file-scoped, and too coarse to decide a call-level question alone** (`OI-26`); recorded so a classifier can weigh it, not act on it |
| `parameterised` | the statement posture, computed here because it needs the whole file — an observation about shape, not a safety verdict |
| `raw` | the call text, truncated |

A consumer must not read a `call-site` as a finding. The absence of a
corresponding `sql` node means the classifier declined it, which is information
about the classifier, not about the code.

### Per-repo JSON (`nodes` / `edges`)

```json
{
  "schema_version": 2,
  "detection_version": 1,
  "group": "acme",
  "name": "sql-runner-api",
  "nodes": [{ "id": "...", "kind": "sink", "family": "sql", ... }],
  "edges": [{ "src_id": "...", "dst_id": "...", "kind": "intra-file", ... }]
}
```

#### Edge `kind`

| `kind` | Meaning |
|---|---|
| `intra-file` | Two nodes in one file correlated by proximity |
| `intra-repo` | A **resolved call**: this call site invokes that method declaration. Emitted since 3.0.0 (`OI-17`); the kind was declared from the start and nothing produced one. `evidence` is prefixed with the resolution tier — `[T1]` a declared field type, `[T2]` an interface expanded to its implementations, `[T3]` a name unique in the repo |
| `cross-repo` | A call from one repository to another, held to a higher evidence bar than the other two |

### Taint catalogues (v2)

| File | Content |
|------|---------|
| `taint/sql-sources.md` + `.jsonl` | String-concat SQL (sources) |
| `taint/sql-execution-sinks.md` + `.jsonl` | JDBC/JPA/ORM execution |
| `taint/file-sinks.md` | File I/O sinks |
| `taint/http-sinks.md` | Outbound HTTP |
| `taint/pii-sources.md` + `.jsonl` | Field-level sources (hierarchical MD) |
| `taint/pii-sinks.md` | Log + storage sinks |
| `taint/raw-code-payload-endpoints.md` | Auto-discovered dangerous payload endpoints |
| `taint/config-data-stores.md` + `.jsonl` | JDBC / Mongo / Redis / S3 from config files |
| `taint/config-security.md` + `.jsonl` | Security-sensitive config keys |
| `taint/config-crypto.md` + `.jsonl` | Cipher suites / algorithms from config |

### Graph artefacts (v2)

| File | Content |
|------|---------|
| `graphs/service-call-graph.md` | Sampled cross-repo HTTP edges |
| `graphs/service-call-edges.jsonl` | Full edge list: `source_repo`, `target_repo`, `target_path`, `confidence`, `evidence` |
| `graphs/service-call-unmatched.jsonl` | Outbound call sites that produced no edge, with `reason` — the negative-coverage signal |
| `graphs/queue-graph.md` + `.jsonl` | Topic → producers / consumers |
| `graphs/data-store-graph.md` + `.jsonl` | Store key → repos |
| `graphs/payload-endpoint-producers.md` + `.jsonl` | Producers of registered dangerous-payload APIs |
| `graphs/pii-lifecycle.md` + `.jsonl` | PII lifecycle touchpoints (Phase 3) |
| `ropa/categories-of-personal-data.md` + `processing-activities.jsonl` | ROPA projection (Phase 3) |
| `conventions/auth-models.md` + `.jsonl` | Per-repo auth pattern cards (Phase 3) |
| `conventions/crypto-agility.md` + `.jsonl` | Per-repo crypto maturity cards (Phase 3) |
| `graphs/traces/<name>.md` | `trace.py` or `trace_batch.py` (batch not auto-run on every build) |

### Scripts and extension points

| Module | Purpose |
|--------|---------|
| `scripts/build_metabase_v2.py` | Fleet/single-repo extract; calls taint + graph aggregators |
| `scripts/trace.py` | CLI: `--target`, `--path`, `--scan-repos`, `--output` |
| `scripts/trace_batch.py` | Batch traces from `taint/raw-code-payload-endpoints.jsonl` |
| `scripts/aggregators/pii_lifecycle.py` | PII lifecycle graph + jsonl |
| `scripts/aggregators/ropa.py` | ROPA projection |
| `scripts/aggregators/auth_cards.py` | Auth convention cards |
| `scripts/aggregators/crypto_cards.py` | Crypto agility cards |
| `scripts/known_api_clients.py` | `ApiClientBinding` registry (Maven coord, import prefix, target paths) |
| `scripts/graph_common.py` | Shared URL/path normalisation, v2 JSON loader |
| `scripts/extractors/unified.py` | Per-source-file tree-sitter + regex extraction |
| `scripts/extractors/config.py` | YAML/properties JDBC, security keys, crypto config |
| `scripts/extractors/http_out.py` | Enriched `http-out` detail (`url`, `host`, `path`) |
| `scripts/aggregators/taint_catalogs.py` | Hierarchical taint MD + jsonl sidecars |
| `scripts/aggregators/service_calls.py` | `http-in` ↔ `http-out` matching |
| `scripts/aggregators/queues.py` | Queue producer/consumer graph |
| `scripts/aggregators/data_stores.py` | Config store bipartite graph |
| `scripts/aggregators/payload_producers.py` | Client-library producer index |

### `build_metabase_v2.py` flags

| Flag | Effect |
|------|--------|
| `--repos-root` | Cloned GitLab trees (required) |
| `--metabase-root` | Output root (required) |
| `--repo` | Single repo filter (`group/name` substring) |
| `--limit` | Cap repo count (testing) |
| `--workers` | Parallel extract (default: CPU−1) |
| `--aggregate-only` | Rebuild `taint/` from existing v2 JSONs |
| `--graphs-only` | Rebuild `graphs/` only (+ producer index; skips taint) |
| `--phase3-only` | PII lifecycle, ROPA, auth/crypto cards only |
| `--no-phase3` | Skip Phase 3 when rebuilding graphs |

### `trace.py` flags

| Flag | Effect |
|------|--------|
| `--target` | Repo id (`acme/sql-runner-api`) or directory name |
| `--path` | Filter inbound/raw-payload paths (e.g. `/query`) |
| `--scan-repos` | Optional path to `repos/` for import/URL literal scan |
| `--output` | Write markdown report (default: stdout) |

### Implementation status

| Phase | Scope                                                                                              | Status |
|-------|----------------------------------------------------------------------------------------------------|--------|
| 0 | Repository index probe, module skeleton, tree-sitter smoke tests                                   | Done |
| 1 | v2 schema, extractors, hierarchical taint catalogues, fleet extract                                | Done |
| 2 | Service/queue/data-store graphs, `trace.py`, `trace_batch.py`, producer index, enriched `http-out` | Done |
| 3 | PII lifecycle model, ROPA view, per-repo auth/crypto cards                                         | Done (static/heuristic) |
| 4 | Synthetic fixtures, extractor snapshots, taint MD caps, fleet regression                           | Done |

All new work should extend v2 `nodes`/`edges`.
