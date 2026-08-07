# Changelog

Notable changes to src2sink. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html) applied to the
observable contract — the CLI flags and the output schema (`SCHEMA_VERSION`), as
set out in [`docs/releasing.md`](docs/releasing.md).

## [Unreleased]

## [3.0.0] - 2026-08-06

**`src2sink` can now say which entry points reach which sinks, with evidence.**
That is the capability the tool is named for, and until this release it did not
have it: it found sources, it found sinks, and nothing connected them.

### ⚠️ Upgrading

**A full rescan is required.** `DETECTION_VERSION` moves 10 → 11, so records
built by an earlier version are not reused. `SCHEMA_VERSION` stays at `2` — the
record *structure* is unchanged and an existing metabase still parses — but two
things about its contents changed, and one of them will break a consumer that
does not expect it.

**Breaking: `call-site` observations now come in two shapes.** Observation was
widened from sink-shaped names to *every* call, because the middle of a layered
path was recorded nowhere. Recording the full SQL-evidence set for every call
measured at 3.4x the nodes and 4.7x the record size on a real repository, so an
ordinary call now records only `symbol`, `receiver`, `arguments` and its
enclosing scope. **`raw`, `receiver_is_database`, `library_hint`,
`file_sql_evidence` and `parameterised` are absent on ordinary calls** and
present only on sink-shaped ones. Read them with `.get()`, not `[]`; absent means
"nothing vouched for this call being SQL, because nothing looked". See
[`SCHEMA.md`](SCHEMA.md#call-site-detail--two-shapes-breaking-change-in-300).

**Records grow ~1.6x in nodes and ~1.7x in bytes.** That is the measured cost of
the widening after two mitigations: dropping `raw` from ordinary calls, and
pruning calls that name nothing declared in the repository — 77% of observed
calls, being `get`, `append`, `len` and the rest of the standard library, which
an intra-repo path cannot pass through by definition.

### Added

- **Source-to-sink paths inside a service (`OI-17`).** The headline. A new
  `tainted-path` finding states that a value from an entry point reaches a sink,
  and cites every hop with its `file:line`, its resolution tier, and the argument
  that carried the value. Reachability alone would report every endpoint as
  reaching every sink its service can touch; a hop carrying no tainted value is
  **pruned**, so a decoy calling a static query does not appear at all rather
  than appearing with a low score.

  Path confidence is the **minimum** hop, never a product, with length recorded
  separately and the weakest link named — a reader can act on "8 hops, weakest
  link is the `B→C` binding" and nobody can act on `0.058`. Depth is unbounded:
  measured on a 2,000-service fleet, capping at three hops finds 25% of what
  depth eight finds. There is no confidence floor, because for an indicator a
  floor converts cheap false positives into expensive, invisible false negatives.

- **Tiered call resolution (`OI-17`, step 3).** `stockService.process(...)` now
  resolves to a specific declaration: **T1** from a declared field type (`high`),
  **T2** an interface expanded to its implementations (`medium`, and explicitly
  ambiguous when there is more than one), **T3** a name unique in the repo
  (`low`); not unique, dropped. The tier is recorded on every edge. T2 is why the
  answer can never be "unreachable" — a constructor-injected interface field is
  the standard Spring shape, so stopping at the declared type would report a
  confident dead end for most of a JVM fleet.

- **The first `intra-repo` edges.** `FlowEdge` has advertised the kind since the
  schema was written and nothing ever emitted one.

- **Call arguments are recorded**, which is what makes a path evidence rather
  than reachability.

- **A persisted fleet index (`OI-15`).** `metabase/index.sqlite3`, built during
  aggregation, holds the four things a trace consults, keyed by target repo. A
  trace now answers from indexed lookups and **never loads the fleet** — peak
  memory is a function of what arrives at the target, not of fleet size.
  Previously a trace read every record in the metabase to answer a question about
  one repo, which at 34 GB on disk needs roughly 222 GB resident merely to be
  held. Staleness is checked on every read; a stale, missing or corrupt index
  falls back to loading rather than serving a wrong answer.

### Fixed

- **The producer scan read the whole fleet once per binding (`OI-30`).** Reported
  from the field at **70 minutes** — the slowest part of a scan bar fleet-wide
  traces. The binding loop was on the outside, so a 34 GB checkout was read from
  disk once per binding and the only thing that differed between passes was which
  regex ran over text already in memory. Now the fleet is walked once and matched
  against every binding while resident: **N× fewer file reads**, plus a further 2×
  from building the indices once per run instead of twice.

- **Discovery's two passes could never agree, so `discovery_method: "both"` was
  unreachable (`OI-33`).** The supply-side pass named a target by the directory
  that *declares* the client coordinate — inside the repo for a multi-module
  build — while the demand-side pass used the repo id. An exact-string lookup
  could not bridge them, so the strongest signal the design produces occurred
  zero times in 226 candidates. Targets are now normalised to the longest known
  repo id, which also restores the `paths` and `service_aliases` the bad key was
  suppressing. Reviewer decisions on already-triaged candidates are preserved
  across the key change, and a coordinate that resolves to nothing now says so
  instead of arriving as an ordinary weak candidate.

- **Api-client discovery rescanned the whole fleet once per class (`OI-35`).**
  The demand-side pass asked "which repos contain a file called `StockClient`"
  by walking every node of every record, once per target *and* per class — so
  node visits grew about 15x for every doubling of the repository count. The
  question is corpus-wide and target-independent, so it is now answered from an
  index built in one pass: **576x fewer node visits at 48 repos**, and the
  saving keeps growing with the fleet.

- **The checkout was walked 25 times per run to find a handful of files
  (`OI-31`).** `Path.rglob(name)` traverses the whole tree and filters by name,
  so four filenames cost four traversals — and no phase shared a walk with any
  other. Now one traversal serves everything: **25 → 3**, and **→ 1** when the
  CLI names every pattern up front.

- **`--discover-api-clients` was silently ignored outside a full scan
  (`OI-31`).** Combined with `--graphs-only` or `--aggregate-only` it printed
  `Done.` and wrote no candidates file — the flag accepted, doing nothing, with
  no clue why. Discovery reads records and the checkout, both of which those
  modes have.

- **Three Kotlin gaps, all silent since 2.1.0.** Each made Kotlin produce a
  *clean-looking* result rather than a wrong one, which is the failure mode
  `OI-13` exists to prevent, and each passed its original parity test because
  those tests compared the easy half of a record:
  - **interfaces were never recognised as interfaces** — Kotlin has no
    `interface_declaration` node, so a call on an interface-typed field bound to
    the bodiless method and the chain stopped;
  - **every method recorded an empty parameter list** — Kotlin exposes no
    `parameters` field, so nothing could be tainted and no Kotlin path existed
    anywhere in a fleet;
  - **every call recorded an empty argument list** — Kotlin names no argument
    field, so no Kotlin hop could carry a value.

- **A caller's reported confidence was whichever edge came last (`OI-29`).**
  `collect_service_edges` emits several edges per caller, one per route it might
  be addressing, and the merge kept the last rather than the strongest — so a
  `high` edge was routinely overwritten by a `low` one for the same caller.
  Callers now report their best-evidenced confidence, which for an indicator
  matters: understating evidence suppresses the lead it should raise.

- **Kotlin call sites were invisible to the AST pass (`OI-13`).** The dispatch
  named Kotlin and then routed it to the Java walker, which requires a
  `method_invocation` node Kotlin never produces. Every Kotlin call site returned
  nothing, so Kotlin SQL sinks were found only when a regex tier happened to
  match — and nothing reported the gap. Kotlin repositories now yield `sql`
  sinks, `script-exec` sinks and `raw-code-payload` findings from the AST tier,
  so counts rise for any Kotlin service. A prerequisite for everything above:
  reachability that silently covers one language is worse than none.

### Also added in this release

- **Type facts for call resolution (`OI-17`, step 2).** A new `type-decl`
  observation records each declared type's field types, supertypes and whether it
  is an interface. A field's declared type is what makes `stockService.process()`
  resolvable offline without a compiler; the supertypes are what let a call on an
  interface reach the implementations that have a body. Kotlin constructor
  properties count as fields, since that is how the standard Spring shape declares
  its collaborators.
- **Nodes know which method they are in (`OI-17`, step 1).** A node recorded
  `file` and `line` and nothing about its enclosing method, so there was no
  "entrypoint 1 of B" to reason about. Callables are now recorded as `method-decl`
  observations with class, parameters and span, and every node carries
  `enclosing_class`/`enclosing_method` — or neither, if it sits inside no method.
  Foundation for call resolution and reachability; neither is possible without it.
- **Entry points beyond HTTP (`OI-21`).** A way into a service counted only if it
  was an HTTP annotation, so queue consumers, gRPC services, GraphQL resolvers,
  scheduled jobs and CLI entry points were invisible as front doors. A new
  derived `entry-point` family unifies them, with `mechanism` naming which kind
  and `externally_triggered` separating a door a caller can open from one only
  the clock opens.
  A `@KafkaListener` needed **no new extraction** — it has produced a `queue-sub`
  node since 1.x and was simply never treated as a way in.
  Both versions bump, so the fleet rescans.

## [2.1.0] - 2026-08-06

Observation and classification, separated. `2.0.0` was about findings that were
*wrong*; this release is mostly about making a wrong finding **cheap to correct**
— plus one defect reported from the field that made Maven dependency data empty
for most real repositories.

**Why a minor bump.** Two new node families and new record fields. Nothing is
removed and no field changes meaning, so a `2.0.0` consumer keeps working;
`docs/releasing.md` makes a new family a minor bump.

**Before you upgrade:** the first build after installing rescans **every
repository** — `DETECTION_VERSION` moved from 1 to 6. If you have not yet run the
rescan `2.0.0` required, this replaces it rather than adding to it.

**The one to read if you read nothing else:** namespaced POMs — which is to say
every POM an IDE or archetype emits — parsed to zero dependencies in `2.0.0` and
earlier, silently. If you have Maven repositories, your dependency data was
empty and nothing said so.

### Added

- **`call-site` observation nodes.** The extractor now records every call
  carrying a sink-shaped name, with the inputs a classifier needs, whether or
  not any family claims it. Previously a call that failed the evidence gate was
  discarded, which is why changing what a library *means* required re-extracting
  the fleet. `kind` is `reference`: an observation asserts nothing about danger,
  and the absence of a corresponding `sql` node says the classifier declined it,
  not that the code is safe.
  **`DETECTION_VERSION` bumps**, so the first build after upgrading rescans
  every repository. Measured at ~7% more nodes on the fixture corpus, roughly
  one observation per `sql` node.

- **File-scoped SQL evidence no longer overrules a receiver (`OI-26`).** The
  three evidence signals were OR'd together, so the weakest — a fact about other
  code in the same file — decided once satisfied. `httpClient.execute(r)` became
  a SQL execution sink because a JDBC query sat in the same class, and since
  execution sinks feed the raw-code-payload linker, it could fabricate an
  injection endpoint that never existed. Evidence is now ordered by how local it
  is: file evidence rescues an *unknown* receiver, never one recognised as
  another kind of boundary. `ps`, `pstmt` and `cstmt` joined the receiver
  vocabulary so the tightening does not withdraw real `PreparedStatement` sinks.

- **Namespaced POMs parsed to zero dependencies (`OI-18`).** Reported from the
  field against 2.0.0. Namespace declarations were stripped with a regex, which
  left prefix *uses* behind — the standard Maven root element carries
  `xsi:schemaLocation`, so removing `xmlns:xsi="..."` made `xsi:` an unbound
  prefix and the document failed to parse. `except ParseError: return []`
  swallowed it. **Every POM an IDE or archetype emits was affected**, so Maven
  dependency data was empty for most real repositories and nothing said so.
  Namespaces are now matched rather than stripped.
- **Maven dependency versions are resolved offline (`OI-18`).** `${property}`
  strings and empty inherited versions were recorded as though they were
  versions. Resolution is now tiered — `literal`, `property`, `parent-in-repo`,
  `parent-in-fleet`, `unresolved` — with the tier recorded, and works without
  `mvn`, a registry, or downloading anything: an internal parent POM lives in a
  repository already cloned. `<dependencyManagement>` no longer emits the BOM as
  a dependency in its own right.
- **Go and Python dependencies are parsed, and lockfiles are read (`OI-19`).**
  Dependency parsing covered Java and npm against nine ecosystems recognised for
  identity, so `dependencies_internal: []` on a Go repo meant "not implemented"
  and looked identical to "no internal dependencies". `go.mod` states exact
  versions outright; `uv.lock`, `poetry.lock` and `package-lock.json` hold the
  resolved answer a manifest range does not. Dependencies now carry
  `version_kind` — `resolved`, `range` or `unresolved` — because `^1.4.2` names a
  set rather than a version. A repo whose ecosystem is recognised but unparsed
  now says so in its notes instead of reporting an empty list.

### Changed

- **Findings are now derived from observations, in a separate pass.** Extraction
  records what it saw; `src2sink/derive.py` decides what it means. The two are
  versioned apart: `derivation_version` governs the findings, and changing a
  classification rule now re-derives from existing records — **no source, no
  parsing, no fleet rescan** — while `detection_version` still governs what is
  observed. A new `sql-field-marker` observation carries the input the
  `raw-code-payload` link previously held only in memory.
- **SQL classification now reads observations, not source.** The rules are
  unchanged and the `sql` nodes emitted are byte-identical; what changed is the
  input. `classify_sql_from_observations` consumes `call-site` records and never
  touches the source or the AST, so correcting a classification — `OI-26`, or any
  entry in the boundary catalogue — becomes a change to one function over stored
  data rather than a reason to re-extract the fleet.

### Fixed

- **Identical but meaningless paths matched at `high` confidence (`OI-24`).**
  `path_templates_match` returned on string equality *before* asking whether
  either side named a destination, so two repos both exposing a bare `/v1` — or
  `/api`, or `/service` — produced a high-confidence cross-repo edge. That is the
  defect `OI-1` was raised to remove, surviving through the one path that never
  reached the guard `OI-1` added.
- **Path placeholders and operation verbs counted as destinations (`OI-25`).**
  `/{id}` matched `/{name}`, and `/search` matched `/search`, both at `high`.
  Placeholders are now filtered like `/api`. Operation verbs are deliberately
  *not* filtered — `/v1/query` is a real route of a real query service — but a
  match resting entirely on verbs is capped at `low`.
  **Behaviour change:** `/orders/create` and `/orders/delete` no longer match
  each other. They are different endpoints, and conflating them was never
  intended.

## [2.0.0] - 2026-08-04

Detection correctness. Where 1.1.0 recovered callers that were missing, this work
is mostly about output that was **wrong** — findings the tool stated confidently
and should not have. Issues are tracked as `OI-n` in
[`docs/issues/src2sink-open-issues.md`](docs/issues/src2sink-open-issues.md);
each is reproduced by a named regression test.

**Why a major bump.** `SCHEMA_VERSION` is unchanged at `2` and existing
metabases still load, but `detail.parameterised` — a documented field — changed
from a boolean to a posture string. Anything reading it as a boolean breaks, and
[`docs/releasing.md`](docs/releasing.md) makes a change in a documented field's
meaning a major bump regardless of whether the file still parses.

**Before you upgrade:** the first build after installing this version **rescans
every repository**, because records now record which detector produced them and
none of the existing ones do (`OI-16`). Budget for a full fleet scan once.

### Fixed

- **The `sql` family matched on method name alone (`OI-7`).** `execute`, `query`
  and `update` are ordinary method names, and the receiver — available on every
  grammar's AST node — was read and discarded. So `httpClient.execute(request)`,
  `messageDigest.update(data)` and `call.execute()` were catalogued as
  unparameterised SQL execution sinks at `high` confidence. Worse, an execution
  sink feeds `link_raw_code_payload_endpoints`, so an HTTP proxy carrying a field
  named `sql` **manufactured a `raw-code-payload` finding** — a fabricated
  injection endpoint that sends an analyst to audit code that was never
  vulnerable. A `sql` node now requires positive evidence: a database receiver, a
  library name in the call text, or file-level SQL evidence.
- **Version prefixes outranked real route names (`OI-1`).** Confidence graded by
  which structural rule fired rather than how much meaning matched, so a repo
  exposing a bare `/v1` beat the service actually exposing `/stock` — and the
  correct candidate was discarded, not merely ranked lower. Segments naming a
  version or a layer are now dropped before comparison, and equal-confidence
  candidates are ranked by specificity.
- **SQL assembled by formatting produced no node at all (`OI-8`).**
  `String.format`, `.formatted`, `MessageFormat.format`, Python `%` and `.format`
  were uncovered. Two adjacent holes mattered more: the concatenation patterns
  excluded *both* quote characters, so `"… WHERE ref = '" + ref + "'"` — the
  canonical injection shape — could not be spanned; and the template pattern
  required interpolation *before* the SQL keyword, so `"SELECT … ${id}"` rarely
  fired.
- **`parameterised` claimed a safety property it could not establish (`OI-10`).**
  A placeholder does not undo a concatenation in the same statement, and the
  value was computed from any SQL-shaped literal anywhere in the file — so an
  unrelated safe constant certified an injectable call site while the scan
  simultaneously reported that statement as a `sql` source.
- **A base-query constant hid the concatenation appended to it (`OI-11`).** In
  `SAFE + " AND ref = '" + ref + "'"` the keyword lives in the constant and the
  concatenated fragments carry none, so nothing matched: no finding was emitted
  *and* the call site was reported safe. Constants are now resolved through a
  per-file symbol table, the same mechanism already used for constant-mediated
  URLs.
- **Tracing rebuilt the whole fleet service-call graph for every target
  (`OI-14`).** The graph is fleet-wide and target-independent, so a batch of N
  traces built the identical graph N times and then filtered it N times. It is
  now built once per run: 10 traces over a 200-repo fleet fell from 22.05s to
  0.04s. Separately, the graph build itself re-normalised every candidate route
  string on every comparison — 4.5M calls for 150 repos — which memoisation cuts
  by 4.2x (400 repos: 38.28s to 9.15s). Reports are unchanged.

- **A detection fix never reached a repository that had not changed
  (`OI-16`).** The incremental scan skipped a repo whose git sha matched the sha
  in its existing record — keying the cache on what was scanned, but not on what
  scanned it. So every fix in this release reached only the repositories that
  happened to commit afterwards, and nothing said so, because a record did not
  record which detector produced it. Records now carry a `detection_version` and
  are rebuilt when it differs. **The first build after upgrading rescans the
  whole fleet**, since no existing record carries the field; after that, findings
  from superseded detectors disappear. A new gate fails the build when an
  extractor changes without a version bump.

### Changed

- **`detail.parameterised` is now a posture, not a boolean.** Values are
  `parameterised`, `mixed`, `raw`, `static` or `unknown`. `mixed` — a placeholder
  in a statement that is *also* concatenated — is the case the boolean could not
  express. `SCHEMA_VERSION` is unchanged at `2` and existing metabases still
  load: a pre-2.0.0 `true`/`false` is reported as `unknown` rather than
  translated, because that boolean was produced by the heuristic this release
  removes.
- **`sql` and `raw-code-payload` counts fall.** This is withdrawn false
  positives, not lost coverage — expect the drop on any dashboard tracking them.
- **Service-call edges resolved through a bare `/v1` or `/api` disappear**, and
  edges that previously ranked `low` through a version prefix are now `medium`.
  `trace --path` filtering is deliberately unchanged: filtering and routing are
  different questions and now use different predicates.
- `detail.receiver` added to `sql` sink nodes.

### Added

- **A mutation gate** (`make mutation`, `scripts/mutation_check.py`, in the `ci`
  chain). Coverage says the tests ran; this says they would notice. It
  reintroduces each catalogued defect in a sandboxed copy of the tree and
  requires the suite to fail. On its first runs it found two vacuous tests, two
  pieces of dead logic, and one untested branch — see the commit history.

## [1.1.0] — 2026-08-01

Cross-repo caller detection: on a real fleet, a service with roughly two dozen
callers showed **one** of them in the generated graphs. Six independent causes,
below. Reproduced end-to-end against the six caller shapes in
`tests/test_cross_repo_caller_coverage.py`: **1/6 detected before, 6/6 after**.

### Fixed

- **`class_patterns` bindings were dead code.** `regex_extractors` did
  `from .http_out import _BINDING_CLASS_RX`, which snapshots the empty list at
  import time; `configure_http_out_client_patterns` rebinds the module global, so
  the extractor's copy stayed empty forever. Every configured `class_patterns`
  entry was silently ignored — restoring a correct `api-clients.json` alone would
  *not* have fixed the reported gap. Callers now go through
  `get_binding_call_patterns()`.
- **`api-client-consumer` nodes never became edges.** They carried `target_repo`
  and declared `paths` but were read only by the payload-producers report, so a
  repo calling a service purely through its published client library could not
  appear in `service-call-edges.jsonl` or the OpenAPI edge graph. This is the hop
  regular SAST cannot see at all: the consumer's source contains no URL, host, or
  service name.
- **Outbound call sites were anchored to class names, case-sensitively.**
  `RestTemplate\.` never matched the ordinary injected-field call
  `restTemplate.exchange(...)`. Spring patterns are now anchored on the
  distinctive method name and match any receiver.
- **Endpoints reached through a constant, enum, or config value were dropped.**
  The ±3-line literal window saw nothing for `host + PATH_QUERY` or
  `ApiPaths.SUBMIT_SYNC`. A per-file identifier → literal symbol table now
  resolves in-file references, and a new `path-constant` reference node covers the
  cross-file case.
- **Wrong-target edges from fuzzy path matching.** `match_path_in_inbound_index`
  returned the first fuzzy match in dict-iteration order, so an incidental
  single-segment overlap with an unrelated repo could beat the correct prefix
  match. It now returns the best-confidence candidate, and memoises.
- **Binding `service_aliases` were ignored by the host index.** They are now
  merged into `build_repo_alias_index` (repo records still win on conflict), so a
  service whose DNS name differs from its repo short name resolves.
- Language hints on call-site patterns had no effect (every hint was in the
  allow-list), so e.g. Python `requests.` patterns ran against Java sources. Hints
  now map to real language sets.
- `src2sink-trace` and `src2sink-trace-batch` duplicated the binding-load logic;
  all three CLIs now share `known_api_clients.configure_from_path`, and `trace`
  recognises binding-declared callers as upstream hits.
- **The dependency audit rewrote the lockfile it was auditing.** `make audit` and
  `tests/test_dependency_pinning.py` both ran `uv run pip-audit`, which
  re-resolves the project first and rewrites every entry in `uv.lock` to whatever
  index `UV_INDEX` / `UV_DEFAULT_INDEX` points at. On a developer machine
  configured for an internal mirror, a read-only test run silently replaced all
  900+ `pypi.org` URLs with internal hostnames — staging internal infrastructure
  names into a public repository, with the hashes preserved so the substitution
  is easy to miss in review. Both now pass `--frozen`, so the audit runs against
  the lockfile as committed, and the test asserts the lockfile digest is unchanged
  across the subprocess so the regression cannot return silently.

### Added

- `--allow-empty-api-clients` on all three CLIs. Passing `--api-clients` with a
  file that loads **zero** bindings is now a hard error: it disables every
  cross-repo client-detection path while the run still reports success. Omitting
  `--api-clients` is unchanged (silently off). See ADR-011.
- Negative coverage as a first-class output (ADR-012): outbound call sites that
  resolve to nothing go to `graphs/service-call-unmatched.jsonl` with a `reason`;
  `service-call-graph.md` reconciles every configured binding against the edges it
  produced; and `run-manifest.json` records `api_clients_binding_count` alongside
  the existing `api_clients_configured` boolean, which only ever meant "a path was
  passed".
- Context-gated call-site patterns: broad receiver matches (`self.post(`,
  `client.post(`) fire only in files that also show HTTP-client evidence, so
  hand-rolled client wrappers are recovered without flagging every `x.get(`.
- Service-alias resolution from the call context — a base-URL helper name
  (`get_<service>_base_url`) or a `${<service>.base-url}` config key now resolves
  to the target repo.
- New node family `path-constant` and node kind `reference` (see `SCHEMA.md`).

### Changed

- Triage guidance in `metabase-usage.md`: a missing cross-repo edge is no longer
  sufficient to call a finding DEAD-CODE. The binding count, the binding-coverage
  table, and the unmatched-call list must all be clean first — absence of an edge
  is not evidence of absence of a caller.

## [1.0.3] — 2026-07-29

**No functional changes.** Hardens how releases are built and attested.

### Added

- **SLSA Build L3.** Provenance is now generated and signed inside
  `slsa-github-generator`'s reusable workflow, whose steps this repository cannot
  modify and whose signing identity the build steps never see. That isolation is
  what separates L3 from L2: at L2 the identity that signs lives in the same job
  that runs the build, so a compromised build step could produce a tampered
  artefact with authentic-looking provenance. The provenance is attached to the
  GitHub release as `multiple.intoto.jsonl` and verified with:

  ```sh
  slsa-verifier verify-artifact src2sink-1.0.3-py3-none-any.whl \
    --provenance-path multiple.intoto.jsonl \
    --source-uri github.com/mimecast/src2sink \
    --source-tag v1.0.3
  ```

  The L2 attestations from 1.0.2 continue alongside it, so `gh attestation
  verify` and the PyPI PEP 740 route both still work — three ways to check the
  same artefact, aimed at different tools.
- Publishing now runs only after provenance generation succeeds, so a provenance
  failure stops a release before the irreversible upload rather than after it.
- A `workflow_dispatch` trigger on the release workflow that builds and generates
  provenance without publishing, for exercising changes to the release path
  without spending a version number — and a monthly schedule that runs the same
  rehearsal unattended, so breakage in the upstream generator surfaces before a
  release depends on it.

### Fixed

- **Build provenance no longer attests `dist/.gitignore`.** `subject-path: dist/*`
  matched the file uv writes there, so 1.0.2's provenance lists it as a third
  subject alongside the wheel and sdist. Cosmetic — the real artefacts were
  attested correctly either way — but a provenance statement claiming a
  `.gitignore` was a build output undermines confidence in the rest of it. The
  attested subjects are now the two distributions and nothing else:

  ```console
  $ gh attestation verify src2sink-1.0.3-py3-none-any.whl --repo mimecast/src2sink
  # subjects: src2sink-1.0.3-py3-none-any.whl, src2sink-1.0.3.tar.gz
  ```

## [1.0.2] — 2026-07-29

**No functional changes.** Adds build provenance to the release process.

### Added

- **Signed build provenance — SLSA Build L2.** Every published artefact now
  carries provenance generated and signed by the build platform, in two forms:
  a GitHub attestation, and a PEP 740 attestation stored alongside the files on
  PyPI so it is available to anyone who installed from there rather than from
  the GitHub release. Verify with:

  ```sh
  gh attestation verify src2sink-1.0.2-py3-none-any.whl --repo mimecast/src2sink
  python -m pypi_attestations verify pypi --repo mimecast/src2sink src2sink-1.0.2-*.whl
  ```

  Provenance ties an artefact to the source commit, tag, and workflow run that
  produced it, so a file claiming to be src2sink can be checked rather than
  trusted. 1.0.0 and 1.0.1 have none — they predate this.

  This is Build **L2**, not L3: the identity that signs belongs to the same job
  that runs the build, so it is not beyond the reach of a compromised build step.
  [`docs/slsa.md`](docs/slsa.md) sets out what closes that gap.

### Changed

- The publish step uses `pypa/gh-action-pypi-publish` instead of `uv publish`.
  uv uploads PEP 740 attestations that already exist but does not generate them;
  the PyPA action does both. Publishing is still tokenless Trusted Publishing,
  and the PyPI publisher configuration is unchanged.

## [1.0.1] — 2026-07-29

**No functional changes.** The analyser, its output schema, and the CLI are
byte-for-byte what 1.0.0 shipped. There is nothing here a user of 1.0.0 needs;
the version exists to carry the release automation and documentation below.

### Added

- This changelog, and a release procedure ([`docs/releasing.md`](docs/releasing.md))
  covering versioning, the gate run, tagging, building, and recovery when a bad
  version reaches PyPI.
- Automated publishing via **PyPI Trusted Publishing** (OIDC). A `v*` tag now
  builds from the tagged tree, verifies the tag matches the packaged version,
  publishes to PyPI, and attaches the same artefacts to the GitHub release —
  with no API token stored anywhere in the repository.
- A link to this changelog from the README.

### Fixed

- **CI cache save race.** Every cached job derived the same `uv` cache key and
  started at once, so they raced for the save reservation and the losers
  annotated each run with "Unable to reserve cache". One job now writes the
  cache and the rest restore only; `srtm`, which installs nothing, opts out
  entirely (the input defaults to `auto`, which had quietly opted it in).
  Verified on a green run with zero annotations.

## [1.0.0] — 2026-07-28

> **Yanked on PyPI, 2026-07-29 — "Superseded by 1.0.1".** Not a defect: 1.0.1 is
> functionally identical, and this release is sound. Existing `== 1.0.0` pins
> keep resolving as before; new installs resolve to 1.0.1 regardless.

First public release. src2sink builds a **source-to-sink metabase**: a structured,
human-readable knowledge base of an entire source-code estate, designed to be
loaded as context for LLM-assisted SAST so taint can be followed *across*
repositories — a SQL fragment built in one service and executed in another, an
internal library that silently forwards to a JDBC sink, PII entering at an
ingress and surfacing in a log three repos away.

### Extraction

- Tree-sitter extractors for **Java, Kotlin, Python, Go, JavaScript, and
  TypeScript**, plus configuration-file extraction (Spring `application.yml` /
  `.properties`, and friends) for facts that never appear in code.
- Output schema **v2** (`SCHEMA_VERSION = 2`): a flow graph of `FlowNode`
  (`source` / `propagator` / `sink` / `store`, with a `family`, a
  `pii_classification`, a `data_class`, and a confidence rating) joined by
  `FlowEdge` at intra-file, intra-repo, and **cross-repo** scope.
- Build-system and framework detection across Maven, Gradle, npm/yarn/pnpm, pip,
  Poetry, Pipenv, Go modules, and Cargo. Declared dependencies are parsed from
  `pom.xml`, `build.gradle*`, and `package.json`; component *identity* (which
  repo publishes a given coordinate) additionally resolves `pyproject.toml`,
  `setup.cfg`, `Cargo.toml`, `composer.json`, `go.mod`, `*.csproj`/`*.fsproj`/
  `*.vbproj`, and `*.gemspec`. An internal-vs-external coordinate classifier
  runs off your own namespace patterns.

### Cross-repo analysis

- **Taint catalogues** — SQL sources and execution sinks, file sinks, outbound
  HTTP sinks, PII sources and sinks, crypto operations, raw-code-payload
  endpoints, and security-relevant configuration.
- **Graphs** — service-call graph (HTTP out ↔ HTTP in), queue producer/consumer
  graph, data-store graph, payload-endpoint producers, PII lifecycle, and
  cross-repo phone-number flows.
- **OpenAPI discovery** — specs found in the estate are matched to services and
  folded into the service-call edges.
- **Phase 3 models** — per-repo auth cards, crypto-agility cards, a PII lifecycle
  model, and a **GDPR Article 30 ROPA projection**.
- **Bidirectional tracing** — `src2sink-trace` follows a target repo or endpoint
  upstream to its producers and downstream to its sinks; `src2sink-trace-batch`
  does it for every raw-payload endpoint discovered.

### Command-line tools

`src2sink-build` (extract + aggregate), `src2sink-trace`, `src2sink-trace-batch`,
`src2sink-curate` (internal-library taint tables), and `src2sink-baseline`
(fleet baseline). Builds are incremental by git SHA — unchanged repos are
skipped, cross-repo aggregation always re-runs so the estate stays consistent.

### Built to scan hostile input

Scanned repositories are treated as untrusted, and the outputs are treated as
prompt material for an LLM. Every control below is traced to a test through the
[SRTM](docs/security-privacy-gap-analysis.md):

- **Execution bulkhead** — each repo is analysed in its own process with a
  wall-clock budget, so a pathological parse or catastrophic regex kills one
  repo, not the run.
- **Path containment** — crafted `.git/HEAD` symrefs and escaping symlinks cannot
  read outside the repo.
- **Hardened XML** — manifests are parsed with `defusedxml`; entity-expansion
  payloads do not expand.
- **Size, file-count, and line-length caps**, plus a content pre-screen that
  skips binary and minified/obfuscated files before a parser sees them. Every
  skip is recorded, never silent.
- **Untrusted-output neutralisation** — extracted content is escaped and fenced
  in Markdown and JSONL so a comment in a scanned repo cannot issue instructions
  to a downstream LLM.
- **Literal-PII redaction** in code snippets, and log hygiene that reports the
  exception *type* and repo id rather than paths or content.
- **Run provenance** — every build writes a secret-free `run-manifest.json`
  (tool version, per-repo SHAs, counts, UTC timestamps) for reproducibility and
  Article 30 records.

Operational guidance — data classification, least-privilege CI, retention and
erasure — is in [`docs/operations-security.md`](docs/operations-security.md);
the risk register is in [`docs/threat-model.md`](docs/threat-model.md).

### Quality gates

CI runs six gates on every push and pull request, and weekly:

| Gate | Enforces |
|---|---|
| `test` | 302 tests, 84% coverage overall, 90% on the security-critical modules |
| `srtm` | every requirement in the SRTM still has an implementing test or a documented audit |
| `mypy (strict)` | `mypy --strict`, clean across the package and scripts |
| `bandit` | Python SAST, clean |
| `pip-audit` | no known advisories against the locked dependency set |
| `opengrep` | pattern SAST with a pinned ruleset, clean at ERROR severity |

Dependencies are hash-pinned in a committed `uv.lock` and installed with
`uv sync --locked`. Known false positives are annotated inline with a stated
reason rather than suppressed wholesale.

### Fixed before release

- **`SKIP_DIRS` was matched against each manifest's absolute path**, so a repos
  root under any colliding path segment — `/tmp/repos`, `~/build/repos` — indexed
  nothing at all. Silently: no error, no note, every coordinate resolving to "not
  found" and every source-map lookup coming back empty. The first CI run caught
  it, because GitHub runners put pytest's temporary directories under `/tmp`;
  reproducible locally with `TMPDIR=/tmp`. `SKIP_DIRS` now applies only to
  segments *below* the scan root — the absolute prefix is the operator's
  filesystem layout, not part of the scanned tree. No published version is
  affected: the fix predates this release.

### Requirements

Python **3.14+**. Install with `pip install src2sink` or `uv add src2sink`.

### Known limitations

- Detection is heuristic and rated (`high` / `medium` / `low`); treat `low` as
  *investigate*, not *confirmed*.
- Scala files are counted in the language breakdown and get the language-agnostic
  regex passes, but there is no tree-sitter grammar for them yet, so no AST-level
  extraction.
- The metabase is a concentrated map of weaknesses and personal-data locations.
  Store it access-controlled and encrypted at rest — see the operations guide.

[3.0.0]: https://github.com/mimecast/src2sink/releases/tag/v3.0.0
[2.1.0]: https://github.com/mimecast/src2sink/releases/tag/v2.1.0
[2.0.0]: https://github.com/mimecast/src2sink/releases/tag/v2.0.0
[1.1.0]: https://github.com/mimecast/src2sink/releases/tag/v1.1.0
[1.0.3]: https://github.com/mimecast/src2sink/releases/tag/v1.0.3
[1.0.2]: https://github.com/mimecast/src2sink/releases/tag/v1.0.2
[1.0.1]: https://github.com/mimecast/src2sink/releases/tag/v1.0.1
[1.0.0]: https://github.com/mimecast/src2sink/releases/tag/v1.0.0
