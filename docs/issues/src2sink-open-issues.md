# src2sink — Open Detection Issues and Proposed Fixes

**Version reviewed:** src2sink 2.0.0
**Status:** every issue in this document is **open**. Fixed issues are removed from here and recorded in [`src2sink-closed-issues.md`](src2sink-closed-issues.md) with their fix and commit sha, so the length of this file is the backlog. Earlier defects (empty-binding silent failure, `api-client-consumer` nodes never reaching the call graph, class-name-anchored call-site regexes, constant/enum indirection, binding aliases, unmatched-ref reporting) were fixed in 1.1.0 before that convention existed and are not repeated here.

**Citing an issue:** use the stable `OI-n` id shown on each heading, not the section number — section numbers do not survive the move to the closed-issues file. See §5.

**Anonymisation notice:** every repository name, package name, artifact id, service name, class name, constant name and **URL path** in this document is fictitious. The worked example throughout is an invented warehouse system. References to `src2sink`'s own source (file:line) and to third-party library names appearing in `src2sink`'s regexes (`RestTemplate`, `requests`, …) are real, as those are needed to locate the code being fixed.

---

## 0. Context: where these come from

Issues reach this document four ways, and only the first began with anyone
reporting a wrong result:

* **A fleet scan.** `OI-1` to `OI-4` came from measuring detection coverage for
  one heavily-consumed internal service across several hundred repositories, and
  investigating the callers that stayed invisible.
* **A targeted review.** `OI-7` to `OI-12` came from reading the SQL families
  and the dependency declarations directly, with measured `extract_from_file`
  output as the evidence rather than fleet statistics.
* **Work on the tests themselves.** `OI-13` was found while raising coverage on
  a module nobody had tested directly — not by anyone reporting a wrong result.
* **Measuring, rather than reasoning about, cost.** `OI-14` and `OI-15` came
  from profiling `trace` over a synthetic fleet. Both the reported symptom and
  the first hypothesis about its cause were wrong; see the `OI-14` resolution.
* **A design review that went sideways.** `OI-18` to `OI-23` and `OI-26` came
  from reviewing the versioning model against four questions about drift. Each
  is recorded with the measurement that found it in
  [`identity-versioning-boundaries.md`](../plans/identity-versioning-boundaries.md)
  and [`observe-then-classify.md`](../plans/observe-then-classify.md), which are
  kept as the record of *why* these decisions were taken.
* **Asking whether the stated purpose is delivered.** `OI-17` came from
  checking the claim in the tool's own name — source to sink — against a
  three-file layered service. It is not delivered, and nothing said so.
* **Designing the next thing.** `OI-16` surfaced while writing
  [`docs/plans/metabase-versioning-design.md`](../plans/metabase-versioning-design.md),
  from asking what a cache key must contain. It had been live through every
  release in this cycle, and is now fixed and recorded in the closed-issues
  file.

Everything fixed is recorded in
[`src2sink-closed-issues.md`](src2sink-closed-issues.md) with its resolution and
commit; this file holds only what is still open, so its length is the backlog.

The running example throughout is a fictitious service
`commerce/warehouse-service`, which publishes a client library
`warehouse-service-client` (group `com.example.commerce.warehouse.client`) and
exposes `POST /stock`. It is consumed by a fictitious repo
`fulfilment/fulfilment-commons`.

---

## 5. Priority

| id | # | Issue | Effort | Value | Priority |
|---|---|---|---|---|---|
| OI-17 | 17 | Nothing connects an entrypoint to a sink inside a service | large | the capability the tool is named for | **P0** |
| OI-20 | 20 | Only SQL has a library evidence catalogue | large | deserialization has no family at all; every other sink type is pattern-only | **P1** |
| OI-22 | 22 | No identity when git history is absent | medium | the incremental scan dies on stripped snapshots | **P2** |
| OI-23 | 23 | A repo's own declared version is never recorded | small | half of every version comparison is missing | **P2** |
| OI-27 | 27 | Internal-prefix and api-client configuration must be written by hand | medium | a first scan against an unconfigured fleet silently finds nothing internal | **P1** |
| OI-29 | 29 | A caller's reported confidence was whichever edge came last | small | fixed in passing while building the OI-15 index; recorded because it understated real findings | **closed** |

Every issue from the original review is fixed and recorded in
[`src2sink-closed-issues.md`](src2sink-closed-issues.md), with the ordering
rationale preserved alongside each one.

**On sequencing.** Three of these gate each other and the order is not the id
order. `OI-21` lands before `OI-17`, because reachability from an incomplete
entry-point set produces confident, incomplete answers. `OI-26` and `OI-20` are
both cheaper after the observation layer in
[`observe-then-classify.md`](../plans/observe-then-classify.md), which turns a
classification change from a fleet rescan into a re-aggregation. See
[`src2sink-3.0-plan.md`](../plans/src2sink-3.0-plan.md) for the phase order. Both entries above were found later and
by different means — `OI-13` while raising test coverage, `OI-15` while
profiling — rather than while investigating a report. That is the argument for
the coverage and measurement work paying for itself.

~~`OI-15` is P1 despite nobody having hit it, because it is a ceiling rather than
a slowdown and the work to lift it is large. Finding out at 34 GB that the
answer is a redesign is a much worse position than knowing now.~~

**Closed 2026-08**, and the bet paid: the fleet reached 34 GB and traces began
swapping exactly as projected, but the shape of the fix was already written down
and turned out to be smaller than the plan feared — the trace path needed no
redesign, only a persisted index. Having measured it early is why the trigger
arrived as scheduled work rather than as an outage.

### Issue ids and lifecycle

Each issue carries a stable `OI-n` id **in addition to** its section number,
because section numbers do not survive the move to
[`src2sink-closed-issues.md`](src2sink-closed-issues.md). Cite `OI-n` — never `§n` —
from test docstrings, commit messages, and code comments.

When an issue is fixed it is **removed from this document** and its section moved
verbatim to the closed-issues document, with a fix description and the commit sha
appended. This file is therefore always and only the open set: its length is the
backlog. See the closed-issues header for the exact move procedure.

**`OI-5` and `OI-6` do not exist.** The ids were originally minted from section
numbers, and §5 and §6 were the Priority table and the Cross-cutting principle
rather than issues — so the sequence inherited a hole. Nothing was dropped or
withdrawn.

That was a mistake worth naming, because it defeated half the point: ids exist so
that a reference survives a section moving, and deriving them from section numbers
imported exactly the accidents they were meant to escape. **Ids are now allocated
sequentially from `OI-13` onward, independently of any section number, and are
never reused or renumbered.** Renumbering to close the gap would be worse than the
gap: these ids are cited from test docstrings, commit messages and the closed-issues
records, and every one of those citations would silently start pointing at the
wrong issue.

---

## 6. Cross-cutting principle

Three of these four defects share one shape: **a detection path that fails to empty without emitting a signal.**

- An empty bindings file disabled all client detection (fixed in 1.1.0 by a hard error plus a manifest count).
- A guard that never matches produces zero nodes and no note (§2).
- An unparsed dependency format produces `dependencies_internal: []` and no note (§3).

The 1.1.0 work established the right pattern — the manifest binding count, the unconditional `service-call-unmatched.jsonl`, the recorded oversized-file skips. Extending it consistently is the durable fix: **any detection input that resolves to nothing should say so in the run manifest or the repo's notes.** A count of zero is a finding; an absent field is not.

---

## 17. Nothing connects an entrypoint to a sink inside a service  `OI-17`

**Severity:** Critical — this is the capability the tool is named for. `src2sink`
finds sources and it finds sinks; it does not connect them.

**Found:** by asking directly whether the stated purpose — untrusted input
reaching component A, passed to B, arriving at a database call in B — is
actually delivered. It is not, and no existing issue says so.

### Symptom

The canonical layered shape, three files, with the request value concatenated
into SQL at the end of it:

```java
// StockController.java
@RestController class StockController {
    private final StockService stockService;
    @PostMapping("/stock")
    public StockResult submit(@RequestBody StockRequest req) {
        return stockService.process(req.getFilter());
    }
}
// StockService.java
@Service class StockService {
    private final StockDao stockDao;
    public StockResult process(String filter) { return stockDao.findMatching(filter); }
}
// StockDao.java
@Repository class StockDao {
    private final JdbcTemplate jdbcTemplate;
    public StockResult findMatching(String filter) {
        return jdbcTemplate.query("SELECT ref FROM stock WHERE " + filter, mapper);
    }
}
```

Measured on 2.0.0:

```
StockController.java   -> ['http-in/source']
StockService.java      -> (nothing)
StockDao.java          -> ['sql/source', 'data-class-field/source', 'sql/sink']
edges produced: 0
```

Both ends are found. The injectable sink is correctly reported as concatenated.
**Nothing links them**, so the finding a reviewer needs — *this endpoint reaches
that injection* — is never stated.

### Root cause

Three gaps, each of which alone would be enough.

**Nodes have no scope.** A `FlowNode` records `file` and `line` and nothing about
the method it sits in. There is no "entrypoint 1 of B" to reason about, only a
line number.

**No declarations are extracted.** `CALL_NODE_TYPES` covers call *sites* only.
Nothing indexes `method_declaration`, so there is nothing for a call to resolve
*to*.

**The only intra-service link is same-file co-occurrence.**
`link_raw_code_payload_endpoints` takes a `FileExtractionContext` — one file — and
fires when an HTTP endpoint, a SQL-shaped field name and an execution sink all
appear in it. In a layered service they are in three different files, so it never
fires. The `intra-repo` and `cross-repo` values of `FlowEdge.kind` are declared in
`schema.py` and **never emitted**; the only `make_edge` call in the codebase
produces `intra-file`.

### Feasibility — prototyped, not assumed

Around 60 lines of tree-sitter over those three files, using only declared field
types:

```
class index:
  StockController   fields={'stockService': 'StockService'}  methods=['submit']
  StockService      fields={'stockDao': 'StockDao'}          methods=['process','unrelated']
  StockDao          fields={'jdbcTemplate': 'JdbcTemplate'}  methods=['findMatching']

  T1 high  StockController.submit -> StockService.process
  T1 high  StockService.process   -> StockDao.findMatching
  --  unresolved: jdbcTemplate.query   (external type — and already the flagged sink)

PATH: StockController.submit -> StockService.process -> StockDao.findMatching
```

**Java and Kotlin declare field types, so receiver resolution is syntactic.**
`private final StockService stockService` says what `stockService.process()`
binds to; no compiler, no type inference. The constructor-injected Spring shape
that dominates the fleet is precisely the resolvable case. `unrelated()` was
correctly excluded.

### Reachability is not proof — and the difference is cheap

Call reachability alone would report every endpoint as reaching every sink its
service can touch. Chaining *argument into parameter* makes it evidence:

```
StockController.submit:5 -> StockService.process   arg 'req.getFilter()' binds param 'filter'
  StockService.process:3 -> StockDao.findMatching  arg 'filter' binds param 'filter'
    SINK jdbcTemplate.query(...)  <- carries ['"SELECT ref FROM stock WHERE " + filter']  ** TAINTED **
```

A decoy `safe(String x)` calling `countAll()` — a static query — was **pruned**,
because no tainted argument reaches it. That pruning is the difference between a
finding and a list of everything.

### Progress

**Step 1 landed** (method-level structure, PR #35). Method declarations are
recorded as `method-decl` observations with class, parameters and span, and every
node carries the `enclosing_class`/`enclosing_method` it sits in — assigned
innermost-first, and left unset rather than guessed for a node inside no method.
Derived nodes inherit scope from the observation they came from, because
derivation also runs over a stored record with no extraction context.

**Step 2 landed** (type facts, PR #36). `type-decl` observations record each
declared type's field types, supertypes and whether it is an interface. Kotlin
constructor properties count as fields — that is how the Spring shape declares
its collaborators, and missing them would leave Kotlin resolvable only by
accident. Supertypes are one list rather than extends/implements split, because
Kotlin gives both as `delegation_specifier` and for resolution they answer the
same question.

**Step 3 landed** (widened observation and tiered resolution). Every call is now
observed, and `resolve.py` binds it to a declaration at T1/T2/T3 with the tier
recorded on every edge — the first `intra-repo` edges the schema has ever
carried. Arguments are captured in the same pass: step 4 needs them, recording
them costs a `DETECTION_VERSION` bump, and widening already forced one.

**The volume estimate was wrong, and the warning above is why it was caught.**
The `+20%` came from a 12-file synthetic corpus. Measured on a real repository
before committing:

```
  synthetic corpus  :  +21 nodes            (+18%)  <- the original estimate
  real repository   :  1,667 -> 5,711 nodes (3.4x)  <- what it actually costs
                       call sites are 75% of all nodes, ~54 per file
```

At fleet scale that is 34 GB becoming roughly 130 GB. Two changes brought it
down to **1.6x nodes / 1.7x bytes**:

* an ordinary call records only what resolving it needs — symbol, receiver,
  arguments, scope — and drops `raw` along with the SQL-evidence fields. `raw`
  alone is up to 160 bytes per call and says nothing a plain call's other fields
  do not;
* calls naming nothing declared anywhere in the repo are pruned once per repo,
  after the method table is complete. **77% of observed calls were these** —
  `get`, `append`, `len`, `str`, `join` — calls into the standard library and
  third-party packages, which an *intra-repo* path cannot pass through by
  definition. The prune applies the same test T3 does, so it can only remove
  calls every tier would have dropped.

The lesson is the one the warning stated: a synthetic corpus understates
per-file call density by more than an order of magnitude, and "+20%" would have
been a 4x surprise at fleet scale.

### Proposed fix

Four pieces, in dependency order:

1. **Method-level structure.** Extract declarations (class, name, parameters,
   line span) and stamp the enclosing method onto every node. `OI-13` (Kotlin
   call sites invisible to the AST pass) becomes a prerequisite rather than a
   nice-to-have, since half the fleet is Kotlin.
2. **A repo symbol table.** Class → field types, method signatures, superclass.
   Built once per repo version, which fits the content-addressed model in the
   versioning design.
3. ~~**Tiered call resolution**~~ — ✅ shipped, in `src2sink/resolve.py`:
   * **T1** receiver typed from a declared field — `high`;
   * **T2** interface resolved to implementations — `medium`, and explicitly
     ambiguous (dropped to `low`) when there is more than one;
   * **T3** name unique in the repo — `low`; not unique — dropped.

   *Narrowed from the proposal:* T1 covers declared **fields** only. Parameters
   and locals are not sources, because `method-decl` records parameter *names*
   and not their types, so `void f(StockService s) { s.process() }` falls to T3.
   Recording parameter types costs another `DETECTION_VERSION` bump and it is
   worth measuring how often the shape occurs first.

   *Found while shipping it:* Kotlin interfaces were never recognised as
   interfaces. Kotlin has no `interface_declaration` node — `interface Foo { }`
   is a `class_declaration` whose first child is the `interface` keyword — so
   `is_interface` was `False` for every Kotlin interface from the moment
   `type-decl` shipped in 2.1.0. A Kotlin call on an interface-typed field bound
   to the bodiless interface method and the chain stopped there: a confident dead
   end for the standard Spring shape across half the JVM fleet, while the Java
   half passed throughout. Exactly the `OI-13` failure mode of a language being
   invisible.
4. **Tainted-path search.** BFS from entrypoint parameters to sinks, pruning any
   hop that carries no tainted argument, with confidence degrading along the path
   and a floor below which nothing is emitted.

The path itself is the proof: each hop cites `file:line`, the resolution tier,
and the argument that carried the value.

### Suggested tests

* The three-file fixture above yields one path, from `submit` to the concatenated
  `jdbcTemplate.query`, and `unrelated`/`countAll` appear in none.
* A decoy method taking no tainted argument is pruned, not merely ranked lower.
* An interface with two implementations produces an explicitly ambiguous `T2`
  edge, never a confident one.
* A path of four `medium` hops does not report as `medium`.
* Kotlin and Java equivalents of the fixture produce the same path — the
  cross-language parity guard `OI-13` also needs.
* A cycle (`a` calls `b` calls `a`) terminates.

### Residual not covered

Reflection, dynamic proxies, lambdas passed as callbacks, and queue or event
hops are out of reach syntactically and will be missed. Python and JavaScript
lack declared types, so T1 is largely unavailable there and results will be much
weaker — this issue's value is concentrated in the JVM fleet. Full dataflow
(field writes, collections, returned values) is out of scope: the proposal
tracks arguments into parameters, which covers the common shape and should be
described as exactly that rather than as taint analysis.

---

## 20. Only SQL has a library evidence catalogue  `OI-20`

**Severity:** High — whole classes of dangerous boundary are invisible, and one
of them has no family at all.

### Symptom

19 families are emitted, but `SQL_DB_IMPORT_RX` is the **only** library keyword
list in the codebase:

```python
SQL_DB_IMPORT_RX = r"\b(?:java\.sql|javax\.sql|jakarta\.persistence
   |org\.springframework\.jdbc|org\.hibernate|org\.jooq|mybatis
   |sqlalchemy|psycopg2?|pymysql|sqlite3|asyncpg
   |database/sql|gorm\.io|jmoiron/sqlx|knex|typeorm|sequelize)\b"
```

Already polyglot, already curated, never generalised. So `script-exec` is
detected by call pattern alone, and `file`, `data-store` and `crypto-*` likewise.

**Deserialization has no family at all** — the archetypal "dangerous if used
incorrectly": Jackson with polymorphic typing, SnakeYAML `Constructor`, Java
`ObjectInputStream`, Python `pickle`, PHP `unserialize`. Same for LDAP, XPath,
template engines and SSRF-capable clients.

### Proposed fix

One declarative catalogue mapping library identifiers to `(role, family,
mechanism)`, generalising `SQL_DB_IMPORT_RX`.

**Imports are the evidence channel; the manifest is not.** An import is
file-scoped by language design, reflects use rather than declared intent, and is
immune to the stale-POM drift a manifest suffers. A manifest is repo-scoped, and
applying repo-scoped evidence to a file-level decision is the `OI-7` scope error
one level up. The manifest keeps a role as a **recall check** — "this repo
declares `spring-jdbc` and we found no `sql` nodes" — which never emits a node
and only questions an absence.

**Sequencing:** this should follow the observation layer in
[`observe-then-classify.md`](../plans/observe-then-classify.md) §3. Once
classification is downstream of extraction, a catalogue entry is a
re-aggregation rather than a detection change, so the catalogue can live in
config and additions cost no fleet rescan.

### Suggested tests

* A deserialization sink is detected in each supported language.
* A catalogue entry with no matching import produces nothing — the catalogue
  informs classification, it does not assert.
* The manifest recall check reports a declared library with no corresponding
  nodes, and emits no node itself.
* Adding a catalogue entry changes no record, only aggregate output (valid only
  after the observation layer lands).

---

## 22. No identity when git history is absent  `OI-22`

**Severity:** Medium — the incremental scan stops working entirely on stripped
snapshots.

### Symptom

Measured with no `.git` present:

```
detect_git_sha -> None
first scan  -> analysed
second scan -> analysed   <-- every scan re-analyses everything
```

Fail-safe — it rescans rather than serving stale records — but the incremental
scan is gone. POC snapshots, exported tarballs, vendored copies and `--depth 1`
mirrors are all affected.

### Why it matters beyond speed

The versioning model keys a repo version on `(repo, git_sha, detection_version,
schema_version)`. With no sha that key has a **null component**, so
content-addressing collapses and "storage grows with change, not with scan count"
stops holding.

### Proposed fix

A **content-hash fallback** over the scanned file set — the scan reads every file
anyway, so the marginal cost is one hash per file. Record which source produced
the identity (`git-sha` or `content-hash`): they are not interchangeable, so
switching invalidates once, and the hash must cover only scanned files or a stray
build artefact causes spurious churn.

### Suggested tests

* A repo with no `.git` is skipped on the second scan when nothing changed.
* Touching a scanned file forces a rescan; touching an ignored file does not.
* The identity source is recorded, and a record switching source is treated as
  stale exactly once.

---

## 23. A repo's own declared version is never recorded  `OI-23`

**Severity:** Medium — half of every version comparison is missing.

### Symptom

The metabase is asymmetric:

| Side | Recorded |
|---|---|
| **Consumer** | `dependencies_internal` → `{groupId, artifactId, version, kind}` |
| **Provider** | identity index → `(group, name)` → clone path. **No version.** |

Nothing reads a repo's own `<version>`. So the tool knows what every repo
*consumes*, including which version, and nothing about what any repo
*publishes* — the comparison cannot be made even in principle.

### Why it is worth more than it looks

Fixing it enables skew detection with **no historical scanning at all**: consumer
pins `warehouse-client:1.4.2`, provider declares `2.1.0`, a major version behind
and actionable immediately.

It is also robust where the sha is not: a stripped export (`OI-22`) has no `.git`
but still has `<version>2.3.1</version>`.

### Proposed fix

Capture the project's own declared version alongside its coordinates in the
identity index and on the record, per ecosystem. Where the version is itself
unresolved (`${revision}`), record it as unresolved — the `OI-18` rule.

### Suggested tests

* A repo's own version is recorded for each supported ecosystem.
* An unresolvable own-version is recorded as unresolved, not as the placeholder.
* A consumer pinning an older version of a scanned provider is reportable
  without any historical scan.

---

## 27. Internal-prefix and api-client configuration must be written by hand  `OI-27`

**Severity:** High — a first scan against an unconfigured fleet produces a clean,
empty result rather than an error, which is the failure §6 names.

**Found:** requested by a 2.0.0 user, alongside the namespaced-POM defect.

### Symptom

`internal-groups.json` decides which coordinates count as internal, and
`api-clients.json` declares the client-library bindings. Both must be written by
hand before a scan means anything. Without them:

* `is_internal_coordinate` matches nothing, so **every** dependency is external
  and `dependencies_internal` is empty fleet-wide;
* no bindings load, so cross-repo API-client detection is off — the defect a hard
  error was added for in 1.1.0, which only covers a file that exists and yields
  zero, not a file that was never written.

Someone running the tool for the first time gets a metabase that parses, renders,
and says almost nothing — with no indication that the reason is configuration.

### Proposed fix

**Propose candidates from what the scan already saw, and require review before
they take effect.**

The evidence is present without any new extraction:

| Signal | Source | Proposes |
|---|---|---|
| Common coordinate prefixes across repos | every parsed manifest | `internal-groups` candidates |
| Repos that publish a `*-client` artifact | identity index | `api-clients` candidates |
| Coordinates depended on but never published in the fleet | dependencies vs identity | likely external, so *not* a candidate |
| Repo name appearing as a string literal | existing extraction (`OI-4`) | `service_aliases` |

Group prefixes cluster hard in practice — a fleet of several hundred repos
usually shares two or three — so the top candidates by repo count are close to
the answer.

**Review is not optional.** A wrong internal prefix is not a small error: it
decides every dependency's `kind`, so `^com\.` would make the entire Maven world
internal. Candidates go to a file the tool refuses to use until a human has
accepted them, in the shape `OI-4` already established for discovered bindings
(`status: pending`), and the run manifest records how many were accepted.

**A missing config file must not read as an empty one.** If neither the config
nor an accepted candidate file exists, the scan should stop and say what to do,
rather than produce a metabase in which nothing is internal.

### Suggested tests

* An unconfigured fleet produces candidates rather than an empty result.
* A pending candidate does not affect classification until accepted.
* A prefix matching an implausible share of the fleet is flagged rather than
  proposed silently — the distinctiveness safeguard `OI-4` specifies for
  `class_patterns`.
* A scan with no configuration and no accepted candidates fails loudly.
* Accepting a candidate is recorded in the run manifest.

### Residual not covered

Discovery cannot see a repository the fleet does not contain, so an internal
library consumed but never scanned still looks external. That is the same
boundary `OI-18`'s `parent-in-fleet` tier has, and for the same reason.
