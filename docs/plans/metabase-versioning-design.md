# Metabase versioning and drift — design document

**Status:** draft for review. Nothing here is agreed, and no code should be
written against it until `R1` (below) has an outcome.

**Why this exists:** `OI-15` proposes persisting the fleet indices to make a
large metabase tractable. The schema that work introduces will be very hard to
change afterwards, and it has to answer a question we have not designed for:
repositories move, and a metabase describes a moment. This document settles what
a "version" is *before* `OI-15` picks a storage layout, not after.

**Relationship to `OI-15`:** blocking. See §10.

---

## 1. Scope

**In scope.** What a version of a metabase entry *is*; how drift between the
metabase, the repositories and the tool is represented and queried; what the
storage model must therefore look like; retention.

**Out of scope.** Deep history analysis (blame, per-line evolution), scanning
arbitrary historical commits for their own sake, and any change to what the
extractors detect. This is about *when* a finding was true, not *what* is
detected.

**Non-goal.** A general-purpose time-series store. Everything below is justified
by a query someone actually needs; §4 lists them, and anything not serving one
of those queries should be cut in review.

**Two questions arrived during drafting and are answered in place, because both
would otherwise be settled implicitly by whatever `OI-15` happened to build:**
storage engine — graph, document or neither (§8) — and whether the tool should
gain a traversal engine (§9). Both were decided by measurement, and in both cases
the measurement contradicted the instinct.

**Status note.** §9 originally deferred traversal. That priority call was wrong
and is corrected at the end of that section: the reachability it depends on is
the tool's primary capability, now filed as `OI-17` at P0. The analysis is
unchanged; the sequencing is.

---

## 2. What exists today

Verified against `1.2.0`, not assumed:

| Fact | Where |
|---|---|
| Every repo record carries `git_sha`, read from `.git/HEAD` | `repo_utils.py:249` `detect_git_sha` |
| Every repo record carries `analysed_at` (UTC ISO-8601) | `build_metabase_v2.py:350` |
| A repo is **skipped** when its sha matches the sha in the existing JSON | `build_metabase_v2.py:409-412` |
| `run-manifest.json` records `tool_version`, counts, and the sha of each updated repo | `build_metabase_v2.py:505`, `:535` |
| `record_fleet_baseline.py` records fleet-wide family counts for regression | whole module |

So per-repo identity already exists and incremental scanning already works. Three
things do not.

**No history.** Each scan overwrites `repos/<group>/<name>.json`. The previous
content is gone. The manifest describes only the run that produced it. Nothing in
the metabase can answer "what changed", "when did this endpoint appear", or "was
this call path ever real".

**No coherent instant.** Because unchanged repos are skipped, `analysed_at`
differs per repo — potentially by months. The metabase is a patchwork, not a
snapshot, and it currently claims otherwise: `run-manifest.json` stamps one
`tool_version` and one run time over a fleet that was mostly produced by earlier
runs. Any "as of" query against today's metabase would be answering about an
instant that never existed.

**No tool identity per record.** See §3.

---

## 3. The live bug this uncovered — proposed as `OI-16`

The skip is keyed on the repo sha **alone**. It does not consider which version
of `src2sink` produced the existing record, and the record does not say.

Measured, not reasoned about — a repo containing `httpClient.execute(req)`, with
a prior record holding the false `sql` sink that `OI-7` removed:

```
scan result: {'_skipped': True, 'group': 'grp', 'name': 'svc'}
nodes still on disk: [('sql', 'execute')]
analysed_at still: 2025-01-01T00:00:00+00:00
record names the tool that made it: False
```

The defect `OI-7` fixed survives the fix indefinitely, for every repository that
has not happened to commit since. The consequences compound:

* **Detection fixes do not land.** `OI-1`, `OI-2`, `OI-7`..`OI-12` all changed
  extraction output. None of them applied to an unchanged repo. The measured
  improvements in those closed issues describe the repos that were rescanned,
  not the fleet.
* **The metabase mixes detector semantics silently.** One record says
  `parameterised: false` (pre-`OI-10` boolean), another says
  `parameterised: "mixed"` (post-`OI-10` posture). Aggregations run across both.
* **The manifest is misleading.** It reports the tool version of the *run*, and a
  reader reasonably takes that as the version that produced the contents.

This is the same cross-cutting shape §6 of the open-issues document already
names: **a detection input that resolves to nothing without saying so.** Here the
input is "the detector that produced this record", and it resolves to unknown.

It is filed separately because it is a present defect with a small fix, whereas
everything else here is a design. It is *also* the cheapest possible validation
of this design: the fix falls straight out of §5.

---

## 4. The queries that justify any of this

Design is judged against these. If a proposal cannot serve one, it is out.

| # | Query | Needed by |
|---|---|---|
| Q1 | "Is this record current, or was it produced by an older detector?" | correctness (`OI-16`) |
| Q2 | "What changed between this scan and the last?" — endpoints, sinks, edges | review, alerting |
| Q3 | "When did this endpoint first appear? When did it disappear?" | audit, incident timeline |
| Q4 | "This trace was true in March. Is it still true?" | the reason traces get written down |
| Q5 | "Which callers reach an endpoint that no longer exists?" | stale-consumer detection |
| Q6 | "Which endpoints have no caller in any version?" | dead API surface |
| Q7 | "Consumer C pins client `1.4.2`. Which provider routes did *that* version expose?" | the version-skew problem, §7 |
| Q8 | "Rescan only what changed" — repos, and the index entries they affect | `OI-15` at 34 GB |
| Q9 | "Which internet-facing endpoints can reach the PII store?" — multi-hop | the tool's name, §9 |

Q1–Q6 need history. Q7 needs provider history *addressable by artifact version*.
Q8 needs content-addressed identity. Q9 needs something none of the others do —
see §9, which concludes it is blocked on evidence rather than on storage. They
are not the same requirement and the design should not pretend they are.

---

## 5. Decision 1 — the unit of versioning

**This is the decision that constrains everything else, and the one `R1` exists
to settle.**

### Option A — snapshot-oriented

Every scan produces a complete immutable snapshot; queries name a snapshot.

*For:* clean semantics, trivially coherent, diffing is a set operation.
*Against:* naive implementation re-stores unchanged repos every run; and it
conflicts with the existing skip, which is the thing making scans affordable.

### Option B — repo-version-oriented (recommended)

The unit is a **repo version**: the tuple `(repo, git_sha, detection_version,
schema_version)`. Repo-version records are immutable and content-addressed. A
*snapshot* is then a cheap manifest of pointers to repo versions.

*For:*
* An unchanged repo scanned again adds **no** new record — the snapshot points at
  the existing one. Storage grows with *change*, not with scan count.
* Gives both coherent snapshots (Q2, Q4) and per-leaf timelines (Q3) from one
  model, because a snapshot is just a set and a timeline is just a filter.
* **It makes the existing skip correct instead of incidental.** Today we skip
  because a file exists with a matching sha. Under B we skip because we already
  hold the record for that key — and since `detection_version` is *in* the key, a
  detector change invalidates it automatically. `OI-16` disappears as a
  consequence of the model rather than needing its own mechanism.

*Against:* two levels of indirection; and it forces us to define
`detection_version` honestly (Decision 2), which is real work.

### Option C — current state plus a change journal

Keep overwriting; append a diff log.

*For:* smallest storage.
*Against:* "state at time T" requires replay, so Q4 and Q7 become expensive and
fragile; and a journal that is ever wrong is undetectably wrong, because there is
nothing to reconcile it against.

### Recommendation

**Option B.** It is Option A's semantics with Option C's storage profile, and it
is the only one of the three that turns the incremental scan from a hack into an
invariant.

> **`R1` decides this.** Everything below assumes B; if review picks otherwise,
> §6 to §11 need rewriting rather than amending.

---

## 6. Decision 2 — what identifies a detector

Under Option B, `detection_version` is in the identity key, so it determines when
work is redone. Too coarse and every release rescans the fleet; too fine and
stale records survive. Four candidates:

| Candidate | Invalidates correctly | Invalidates unnecessarily | Fails how |
|---|---|---|---|
| Package version (`1.2.0`) | yes | **every release**, including docs-only | expensive, not wrong |
| Hand-maintained `DETECTION_VERSION` | only when bumped | never | **silently, if someone forgets** |
| Hash of `extractors/` sources | yes | on comments, refactors, renames | expensive, not wrong |
| Hybrid: `DETECTION_VERSION` + hash of declarative rule inputs | yes | rarely | as above, if forgotten |

The failure modes are not symmetric. Over-invalidation costs CPU; under-
invalidation produces confidently wrong output that nobody can see — which is the
`OI-16` failure we are trying to remove.

**Recommendation:** hand-maintained `DETECTION_VERSION`, made safe by a **CI gate
in the same family as the ratchets this repo already runs**: fail the build if
anything under `src2sink/extractors/` (or the pattern/vocabulary/binding inputs)
changes in a commit that does not bump `DETECTION_VERSION`. That converts the
"someone forgets" failure from silent-and-permanent into a red build. The
allowlist escape hatch — for a genuinely cosmetic edit — should require naming
the file, so the exception is reviewed rather than assumed.

> **`R2` decides this**, and should specifically stress-test the gate: what
> happens on a refactor that moves code between extractor modules without
> changing behaviour, and is that annoyance worth the safety.

---

## 7. Decision 3 — version skew, and what "the paths for the current version of a leaf" means

This is the hardest part and the one most likely to be got wrong quietly.

### The problem

The service-call graph matches consumer call sites against provider `http-in`
routes **as most recently scanned**. But a consumer does not call the provider's
HEAD — it calls whatever it was built against. If `fulfilment-commons` depends on
`warehouse-service-client:1.4.2`, its call sites describe the API *at 1.4.2*.

If the provider has since renamed `/stock` to `/inventory/stock`, then today:

* the edge to `/stock` no longer matches any provider route, so it is **lost** —
  reported as unmatched, when in reality the consumer is calling a route that
  existed and may still be served; or
* worse, `/stock` fuzzily matches some *other* repo's route and the edge is
  **wrong** — the `OI-1` failure mode, arriving through time rather than through
  path shape.

Neither is distinguishable from a correct result in the output.

### What is available

`dependencies_internal` already carries `{groupId, artifactId, version}`, and
`api_client_discovery` already resolves coordinates to publishing repos. So the
consumer's *pinned version* is known today. What is missing is the mapping from
that version to a provider commit, and the provider's routes at it.

### Proposed model

Resolve each cross-repo edge against a **named provider repo version**, and make
the basis of that choice part of the edge:

```
edge.resolved_against = {
    provider_repo_version: <repo-version key, or null>,
    basis: "pinned-tag" | "pinned-untagged" | "head-fallback" | "unpinned",
    confidence: "high" | "medium" | "low",
}
```

* `pinned-tag` — the consumer's declared version matched a provider tag
  (`1.4.2` → `v1.4.2`), and that provider version has been scanned. Strongest.
* `pinned-untagged` — the version is declared but no tag matches, so it could not
  be resolved to a commit.
* `head-fallback` — resolved against the provider's latest scanned version
  because the pinned one is unavailable. **This is today's behaviour for every
  edge**; the change is that it becomes visible.
* `unpinned` — no client dependency at all (hand-rolled callers, `OI-4`).

The design principle here is the document's own §6: an input that resolves to
nothing must say so. We cannot always have the historical provider version — so
the edge declares which version it used and whether that was the right one. A
labelled fallback is filterable; a silent one is a wrong answer.

### The expensive part, and how to bound it

`pinned-tag` requires having scanned the provider at that tag. Options:

1. **Never** — always `head-fallback`. Free, and strictly better than today
   because it is labelled. This alone answers Q4 partially and nothing else.
2. **On demand** — scan a provider tag only when some consumer pins it. The work
   is bounded by *distinct pinned versions actually referenced*, which is far
   smaller than "all tags" and is exactly the set that matters.
3. **All tags** — complete and unaffordable.

**Recommendation: (1) first, then (2).** (1) is small, ships independently, and
makes the problem visible — which will tell us how much (2) is worth before we
build it. Shipping (2) first would be designing against a guess about how much
skew the fleet actually has.

> **`R3` decides this.** It is the review most likely to reject the proposal, and
> the one where a wrong call is most expensive, because it changes the meaning of
> every cross-repo edge — the tool's primary claim.

---

## 8. Decision 4 — storage engine: graph database, document database, or neither

The natural instinct, given a versioned cross-repo call graph, is a graph
database; the natural counter-instinct, given variable-shaped `detail` payloads,
is a document database. The measurement below says **neither**, and it says so
clearly enough to be worth leading with.

### The deciding measurement

For a 300-repo synthetic metabase with realistic snippet-bearing records:

| | size | share of metabase |
|---|---|---|
| whole metabase | 4.61 MB | 100% |
| service-call edges, full records | 0.059 MB | 1.28% |
| **traversable skeleton** (`source`, `target`, `path`, `confidence`) | **0.024 MB** | **0.52%** |

0.025 edges per node. Extrapolated:

| metabase | graph skeleton |
|---|---|
| 34 GB | **~178 MB** |
| 500 GB | **~2.6 GB** |

**The graph is small. The payload is large.** At the fleet size that motivated
`OI-15`, the entire traversable structure fits in memory with room to spare —
and at 500 GB it still fits on any machine that could hold the metabase at all.
The 34 GB problem is a *payload storage* problem wearing a graph problem's
clothes.

A graph database would therefore be bought to solve the half that was never
hard, while doing nothing for the half that is.

### What the tool actually asks of a graph

Verified, not assumed: there is **no traversal engine**. No BFS, no visited set,
no frontier, no recursion over edges. Every use of "hop" in the codebase is
singular — `trace` finds direct callers of a target, `pii_cross_repo` finds A→B
links. The workload is *joins and filtered scans*, not traversal.

The fair objection is that the tool is called `src2sink` and multi-hop
source-to-sink paths across repos are the obvious next capability. Granting that
entirely: the depth is small (2–4 hops), the fan-out modest, and the graph is
~10⁵ edges even for a fleet ten times the size measured above. Recursive CTEs
handle that comfortably. Graph databases earn their cost on deep, variable-length,
high-fan-out traversal over structures too large to hold in memory. None of those
three conditions applies here, and the measurement says none of them will.

### Against a graph database specifically

* **Temporal modelling is its weak spot, and versioning is the entire point of
  this document.** Bitemporal graphs are notoriously awkward: validity intervals
  become properties every traversal must filter at every hop, or worse, version
  nodes that triple the graph's size and complexity. Relational engines express
  "valid between snapshot X and Y" natively.
* **Licensing conflicts with a position already taken.** Neo4j Community is
  GPLv3; Memgraph is BSL; ArangoDB moved to BUSL. `OI-12` deliberately trimmed
  the runtime closure to 8 packages, all MIT or PSFL, with no copyleft anywhere.
  Adopting a GPLv3 engine would reverse that decision, and it should not happen
  as a side effect of a storage choice.
* **It changes the deployment model.** Today this is a CLI run in CI with 8
  runtime dependencies. A server-based store makes it a CLI *plus infrastructure*
  — provisioning, backup, upgrades, access control. That is a large, permanent
  operational cost.

### Against a document database specifically

The one genuine advantage — schemaless `detail` payloads — is a JSON column in
any engine below, both of which support JSON indexing and extraction. Set against
that: MongoDB is SSPL (not OSI-approved, and the same concern as above), joins
and traversal are weaker than relational, and the deployment-model objection
applies identically. It buys a feature already available and charges the full
operational price for it.

### Recommendation — split the two, and keep the choice reversible

Treat the skeleton and the payload as different problems, because the measurement
says they are:

* **Skeleton** — repos, repo versions, nodes-without-detail, edges, snapshot
  membership. Small, highly structured, heavily queried, temporal. → an
  **embedded relational store**.
* **Payload** — `detail` bodies, snippets, raw matched text. Large, never
  traversed, fetched by key only when rendering. → **content-addressed immutable
  files, one per repo version**.

This maps exactly onto Option B: a repo version *is* one immutable payload file
plus a set of skeleton rows, which is why the two decisions fit together rather
than merely coexisting.

**Engine: SQLite first.** It is in the standard library (no new dependency, which
matters after `OI-12`), public domain (no licence question at all), and entirely
adequate for a skeleton of this size — 178 MB at 34 GB of metabase. Recursive
CTEs cover multi-hop if and when it arrives.

**DuckDB is the upgrade path, and the payload format is what keeps it open.** The
aggregation phase is scan-heavy — every node, filtered by family, counted — which
is precisely the columnar case, and DuckDB is MIT. If the payload files are
written as **Parquet** rather than opaque blobs, DuckDB can later query them in
place and the skeleton can move with no data migration. If they are written as
compressed JSONL, that door is harder to open.

> **The reversibility argument is the important one.** Choosing SQLite now is a
> low-cost decision *provided* the payload format is chosen so that the engine
> can change later. Choosing the payload format badly is the expensive mistake,
> and it is the one that looks harmless at the time.

> **`R1b` decides this**, immediately after `R1`, because the answer depends on
> the unit of versioning but constrains nothing before it. Review should test the
> claim that traversal stays shallow — if anyone intends deep transitive
> source-to-sink analysis over the full node graph rather than the service graph,
> the measurement above needs redoing against *that* graph before this
> recommendation stands.

---

## 9. Decision 5 — should there be a traversal engine?

Two questions, and they have opposite answers.

### Would it improve performance? No — measured

Over a synthetic 10,000-repo fleet with 11,723 edges and hub-heavy topology (a
few shared services called by many, the shape that makes traversal costly):

| | |
|---|---|
| reachability BFS, depth 2 | **1 µs** per source |
| reachability BFS, depth 4 | **1 µs** per source |
| reachability BFS, depth 6 | **1 µs** per source — 5,585 repos reached |

500 sources traversed to depth 6 in **0.7 ms**. Traversal over this graph is
free, which follows directly from §8: the skeleton is 0.5% of the metabase and
fits in memory at any fleet size we have projected.

So a traversal engine is **not a performance feature**. It cannot make the 1-hop
queries the tool asks today any faster — those are already answered, and `OI-14`
removed the recomputation that made them slow. What makes queries faster is
indexing (`OI-15`). A traversal engine adds *capability*, and should be argued
for on that basis or not at all.

*(Path **enumeration** from a high-fan-out hub is the one traversal operation
that can explode combinatorially, and it was not measured here — the probe
started from a low-degree node. If enumeration is ever offered, it needs an
explicit bound. Reachability, which is what the useful queries need, does not.)*

### Should it exist? Yes eventually — but the blocker is not the traversal

The tool is called `src2sink`, and the query a security analyst actually wants is
"which internet-facing endpoints can reach the customer PII store", across repos.
That is inherently multi-hop, and nothing about the graph's size argues against
it.

**What argues against it now is that there is no intra-repo reachability to
chain.** `link_raw_code_payload_endpoints` operates on a `FileExtractionContext`
— a **single file**. It fires only when an HTTP endpoint, a SQL-shaped field and
an execution sink all appear in the *same file*. There is no repo-local call
graph, no dataflow between methods, nothing connecting a controller in `Api.java`
to a DAO in `StockDao.java`.

In any layered service — controller → service → repository, which is most of the
JVM fleet — the endpoint and the sink are in different files, so nothing links
them at all. The existing link fires mainly on the SQL-in-controller
anti-pattern, which is precisely why it is an interesting *finding*, and equally
why it is not a foundation.

The consequence for traversal is decisive. A path `A → B → C` assembled from
today's edges asserts only:

> A calls B; and B has an inbound endpoint *somewhere*; and B makes an outbound
> call to C *somewhere*.

It does **not** assert that A's request reaches C. The two halves of B may be
entirely unrelated code. Rendering that as a source-to-sink path would be the
same class of error as `OI-1` (confidence from the rule that fired rather than
the meaning that matched), `OI-7` (a name mistaken for evidence) and `OI-10`
(reporting a safety property the analysis cannot establish) — repeated at path
length, where it looks more authoritative and is less checkable.

### Confidence does not survive chaining

Each hop is a heuristic carrying `high`/`medium`/`low`. A path is only as good as
its weakest link and in truth rather worse, because the errors are independent:
four chained `medium` hops is not a `medium` path. Any traversal must degrade
confidence along the path — multiplicatively, or by taking the minimum and
penalising length — and refuse to emit paths below a floor. Emitting everything
and letting the reader judge is not an option when the output is consumed by an
LLM or pasted into a ticket.

### Recommendation — **superseded, see the note below**

**Not now, and not as a performance measure.** Sequence it:

1. **Intra-repo reachability first** — a repo-local call graph linking inbound
   endpoints to outbound calls and sinks through the repo's own functions. This
   is the binding constraint, it is substantial work, and it is what makes every
   multi-hop path mean something.
2. **Then traversal**, which by the measurement above is close to free once the
   edges are trustworthy, with confidence degradation and a path floor designed
   in from the start rather than added after someone believes a bad path.
3. **If traversal ships before (1)** — a legitimate choice for exploration — the
   output must be labelled as **structural adjacency, not dataflow**, in the
   renderer and not merely in a doc. "These repos are connected" is a defensible
   claim; "this endpoint reaches this database" is not, yet.

> **`R6` decides this**, and it is deliberately sequenced last: it depends on the
> storage decisions but constrains none of them, precisely because the graph is
> small enough that no storage choice forecloses it. Review should push back
> hardest on step 3 — a label is a weak defence against a diagram that looks
> like a dataflow path.

### Correction — this is the roadmap, not a deferral

The analysis above stands; the **priority** it assigned was wrong, and the error
is worth naming because it is a common one.

Having established that intra-repo reachability blocks useful traversal, this
document filed traversal as "later" and moved on. But connecting an entrypoint
to a sink *is what the tool is for* — the name says so. A dependency that blocks
the primary capability is not a reason to defer the capability; it is the next
piece of work. Treating a blocker as a postponement is how a stated purpose
quietly becomes a backlog item.

That gap is now `OI-17`, filed at **P0**, with the measurement: on a three-file
layered service, both the endpoint and the concatenated SQL sink are detected and
**zero edges** connect them.

Two things in the analysis above also turned out to be too pessimistic, and both
were settled by prototype rather than argument:

* **Resolution is syntactic for the languages that matter.** Java and Kotlin
  declare field types, so `private final StockService stockService` resolves
  `stockService.process()` with no compiler. ~60 lines of tree-sitter produced
  the full path on the fixture, and correctly declined to resolve calls into
  external types.
* **The precision problem has a cheap answer.** Chaining *argument into
  parameter* along the path turns reachability into evidence, and prunes hops
  that carry nothing tainted — a decoy method calling a static query was excluded
  rather than merely ranked lower.

So the sequencing changes: intra-repo reachability (`OI-17`) is the primary
deliverable, and traversal falls out of it nearly free. `R6` still governs the
confidence model — degradation along a path, and a floor below which nothing is
emitted — which is the part that keeps this from manufacturing authoritative
nonsense. See the unified plan for how it sequences against the storage work.

---

## 10. How this changes `OI-15`

Directly, which is why this document blocks that one.

**Keys.** Every persisted table must be keyed on **repo version**, not repo. This
is not retrofittable in any pleasant way: an index keyed on `repo` has to be
rebuilt from scratch to become one keyed on `(repo, version)`, and by definition
that rebuild happens when the index is too large to rebuild.

**The index becomes incremental, which is the point.** Under Option B a rescan
touches only changed repo versions, so only the index entries deriving from them
need recomputation. This matters far more than the constant-factor work done in
`OI-14`: at 34 GB the question is not how fast a full build is, but whether a
full build is ever required. Content-addressed identity is what makes the answer
"only on a `detection_version` bump".

**Queries gain a snapshot argument.** `trace` becomes "trace at snapshot N",
defaulting to latest. Q4 — "was this still true?" — becomes running the same
query against two snapshot ids, which is the cheapest possible implementation of
drift reporting and needs no separate diff engine.

**Retention becomes a first-class constraint rather than an afterthought**, see
§11 — and it is the same constraint `OI-15` exists to respect.

**Consequence for sequencing:** `OI-15` should not begin before `R1` and `R2`
have outcomes. `R3` can run in parallel, because the skew work adds columns
rather than changing keys.

---

## 11. Retention, growth and privacy

History and `OI-15` pull in opposite directions and the tension must be resolved
explicitly, not discovered.

Content-addressing bounds growth to *change*, but a fleet where most repos commit
weekly still accrues a repo version per repo per week. Candidate policies:

* **Keep all snapshot manifests, prune record bodies.** Manifests are pointer
  lists and tiny; keeping them all preserves the *timeline* (Q3) cheaply even
  once bodies are gone. Bodies beyond the retention window get replaced by their
  family counts — enough for Q2/Q6, not enough for Q4.
* **Keep every repo version referenced by a retained snapshot; GC the rest.**
  Simple, predictable, and expressible as one SQL statement.
* **Tiered.** Full detail for the last K snapshots, counts beyond, manifests
  forever.

**Privacy is part of this, not adjacent to it.** Node detail carries content
extracted from scanned repositories. `redact_literals` masks value-shaped tokens
on the way in, but redaction is best-effort pattern matching over untrusted
input, and retention multiplies the window in which anything it missed persists.
Today a rescan overwrites the record and the miss disappears; under this design
it would not. **Retention depth is therefore a privacy control** and belongs in
`docs/threat-model.md` alongside `PT-002` / `PRV-NEW-2`, with a stated maximum
rather than an unbounded default.

> **`R4` decides retention and updates the threat model.** A default of "keep
> everything" should be rejected on privacy grounds even if storage allows it.

---

## 12. Migration

The existing metabase has no history and cannot be given one retroactively — the
prior states were overwritten and are not recoverable. So:

* Existing records become the **first** repo version of each repo, with
  `detection_version` recorded as `unknown`.
* `unknown` must be treated as *stale*, not as *current*, so the first run under
  the new model rescans the fleet once. That is the honest reading: we genuinely
  do not know which detector produced those records, and §3 shows assuming the
  current one is how we got here.
* That first rescan is expensive and should be planned as an operational event,
  not discovered by whoever runs the next build.

---

## 13. Design review plan

Each gate has an entry artefact, named reviewers, and an exit criterion. A gate
that ends "looks fine" has not been held.

| Gate | Decides | Entry artefact | Exit criterion |
|---|---|---|---|
| **R0** | Problem framing and scope | this document | Agreement that Q1–Q8 are the right queries, and that anything not serving them is cut |
| **R1** | Unit of versioning (§5) | schema sketch: tables, keys, cardinality, worked example of one rescan | A written key definition, and an explicit statement of what becomes hard to change afterwards |
| **R1b** | Storage engine and payload format (§8) | skeleton-vs-payload split, engine comparison, licence position | A decision on the **payload format** specifically — that is the irreversible half; the engine can change later if it is chosen well |
| **R2** | Detector identity and invalidation (§6) | `DETECTION_VERSION` proposal plus the CI gate, dry-run against the last 20 commits | Evidence on how often the gate would have fired, and whether it would have been right each time |
| **R3** | Version-skew resolution (§7) | edge provenance model, plus **measured** skew on a real fleet: how many edges would be `head-fallback` | A decision on option 1 vs 2, justified by that measurement rather than by intuition |
| **R4** | Retention and privacy (§11) | retention policy, growth projection at 3 fleet sizes, threat-model diff | A stated maximum retention with a privacy rationale, merged into `docs/threat-model.md` |
| **R5** | Migration and rollout (§12) | migration steps, cost of the forced rescan, rollback plan | Named owner for the first rescan, and a rollback that does not require the old data |
| **R6** | Traversal engine (§9) | intra-repo reachability proposal, confidence-degradation model, path floor | A decision on whether multi-hop output may ship before intra-repo reachability exists, and if so how it is labelled |

**Sequencing.** R0 → R1 → R1b → R2 gate `OI-15`. R3 runs in parallel. R4 must
complete before any history is retained in a real environment — retaining first
and deciding retention later is how the privacy exposure happens. R6 is last and
gates nothing: §9 shows the graph is small enough that no storage decision
forecloses traversal, so it can be taken on its merits whenever the intra-repo
work is ready.

**Before R1, do the measurement R3 needs.** How much skew exists is currently
unknown, and it determines whether §7 is important or theoretical. It is cheap:
count edges where the consumer declares a pinned client version, compare against
the provider's current tag. That number should be on the table at R1, because a
fleet with almost no skew justifies a much simpler design than one riddled with it.

---

## 14. Open questions for review

These need a decision from someone; they are not resolvable from the code.

1. **Is the metabase a system of record, or a cache?** If it can always be
   rebuilt from the repos, retention is cheap and losing history is an
   inconvenience. If it is evidence — cited in an audit, or describing repos that
   have since been deleted — then it needs integrity and backup, and §9 becomes
   much more constrained. This changes the design more than anything else here
   and is not a technical decision.
2. **Coherent snapshots, or per-repo timelines, as the primary query model?**
   Option B supports both, but one must be the default that `trace` uses, and
   the answer decides whether "as of" means an instant or a set of pins.
3. **How far back?** A number, from §11. "As long as possible" is not an answer.
4. **Does a provider's *deleted* endpoint invalidate a stored trace, or annotate
   it?** Invalidation is safer; annotation preserves the audit trail. Probably
   annotate and mark, but it is a judgement about who reads these.
5. **Do we scan tags at all (§7 option 2)?** Defer until the R3 measurement
   exists; recorded here so it is not forgotten.

---

## 15. What I recommend doing first

Independent of every decision above, and useful under all of them:

1. **Fix `OI-16`** — record `detection_version` on each repo record and include
   it in the skip key. Small, testable, fixes a live bug, and is the minimum
   viable piece of this design. It does not commit us to Option B.
2. **Measure the skew** (§13) so R3 argues from a number.
3. **Hold R0/R1/R1b** before `OI-15` starts.

Doing (1) first is deliberate: it delivers the correctness win immediately and
validates the central idea — that detector identity belongs in the record — at a
scale where being wrong is cheap.

**What not to do first.** Neither a graph database (§8) nor a traversal engine
(§9), on the evidence gathered here. Both are the intuitive response to "a
versioned cross-repo call graph at 34 GB", and the measurements say the graph is
0.5% of the problem and traversal costs 1 µs. The expensive, irreversible
decisions are the payload format and the identity key — the two that look like
implementation detail.
