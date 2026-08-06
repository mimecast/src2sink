# src2sink 3.0 — unified plan

> **Amended.** [`observe-then-classify.md`](observe-then-classify.md) revises
> Phase 1 (observation is separated from classification, not just computation
> from rendering), rescopes Phase 2 (`OI-17` is bipartite reachability at
> unbounded depth, with interface expansion), and settles the boundary-catalogue
> question. Read it alongside this plan.


**Supersedes** [`open-issues-fix-plan.md`](open-issues-fix-plan.md), which
covered the 2.0.0 detection-correctness cycle and is complete. It **absorbs**
[`metabase-versioning-design.md`](metabase-versioning-design.md) as its design
input — that document keeps the decision records and review gates; this one
sequences the work.

**Inputs:** the 2.0.0 [architecture review](architecture-review-2.0.md), the
open issues (`OI-13`, `OI-15`, `OI-17`), and the versioning design's five
decisions.

---

## 1. What 3.0 is for, in one sentence

**Say which entrypoints reach which sinks, with evidence, over a fleet too large
to hold in memory, and know when the answer went stale.**

Each clause is a workstream, and they are not independent:

| Clause | Work | Tracked as |
|---|---|---|
| "which entrypoints reach which sinks, with evidence" | intra-repo reachability | `OI-17` (P0) |
| "over a fleet too large to hold in memory" | persisted, incremental index | `OI-15` (P1) |
| "know when the answer went stale" | metabase versioning | design doc, D1–D3 |
| (prerequisite to all three) | separate computation from rendering | Finding A |

---

## 2. The finding that shapes the whole plan

`OI-15` and `OI-17` both look like features that could be built independently.
Neither can.

* **`OI-15` is blocked by Finding A.** 14 of 32 aggregator modules import
  `renderers.markdown`, and 23 functions compute-and-write in one step. There is
  no computation to persist without first separating it from its rendering.
* **`OI-17` is blocked by Finding E and a schema change.** Nodes carry no
  enclosing method, and `FlowEdge` advertises `intra-repo` edges that nothing
  emits while cross-repo relationships live in a *separate* `CallEdge` type.
* **Both are blocked by the versioning decisions**, because both introduce
  persisted structures whose keys are extremely expensive to change later.

Sequencing them as three parallel deliverables means discovering this in the
middle. So the plan front-loads the unglamorous phase.

---

## 3. Phases

Each phase is independently releasable and leaves the tool working. No phase
depends on a later one.

### Phase 0 — decide (no code)

Hold `R0`, `R1`, `R1b`, `R2` from the versioning design. Deliverables: the
repo-version key, the payload format, and the `DETECTION_VERSION` policy —
already half-delivered, since `OI-16` shipped the version and its gate in 2.0.0.

Also run the **skew measurement** the design doc asks for (`R3`'s input): how
many cross-repo edges would resolve `head-fallback`. It is cheap and it decides
whether §7 of the design is important or theoretical.

**Exit:** written key definitions. Nothing below starts first.

### Phase 1 — separate computation from rendering

The prerequisite. For each of the 14 aggregators that render: split into a pure
`compute_*` returning data and a thin `write_*` at the edge.

* Mechanical, incremental, one aggregator per commit.
* Each split makes the tested surface *purer*, so coverage should hold or improve.
* No behaviour change; the rendered Markdown must stay byte-identical, which is
  the regression test.

**Why first:** every later phase consumes computed data. Doing this after
`OI-15` means touching all 14 twice.

**Exit:** no aggregator imports `renderers.*`; a golden-output test proves the
Markdown is unchanged.

### Phase 2 — intra-repo reachability (`OI-17`)

The primary capability, and deliberately before the storage work: it produces
*more* data, so it should be settled before choosing how to store data.

1. **Kotlin AST parity (`OI-13`)** — a prerequisite, not a side quest. Half the
   JVM fleet is Kotlin, and reachability that silently covers one language is
   worse than none because its absence looks like a clean result.
2. **Method-level structure** — declarations extracted; every node stamped with
   its enclosing method. Schema change.
3. **Repo symbol table** — class → field types, method signatures, superclass.
4. **Tiered resolution** — T1 receiver-typed (`high`), T2 interface→impl
   (`medium`, explicitly ambiguous when >1), T3 name-unique (`low`), else
   dropped. Tier recorded on every edge.
5. **Tainted-path search** — BFS from entrypoint parameters to sinks, pruning
   hops that carry no tainted argument; confidence degrades along the path, with
   a floor below which nothing is emitted.

Prototyped already: ~60 lines of tree-sitter resolved the canonical
controller→service→DAO chain at high confidence and pruned a decoy.

**Exit:** the three-file fixture yields exactly one path, `unrelated`/`countAll`
appear in none, Java and Kotlin agree, and a four-hop `medium` chain does not
report as `medium`.

### Phase 3 — versioning and the persisted index (`OI-15`)

Now that the data is separable and its shape is settled:

1. **Stream records** — `load_v2_repo_records` becomes a generator; the 19
   importers consume once or declare what they retain.
2. **Persist per repo version** — content-addressed payload files, skeleton rows
   in SQLite. Keys from Phase 0.
3. **Query rather than load** — `trace` takes a snapshot argument, defaults to
   latest, and answers from indexed lookups.
4. **Incremental index maintenance** — only changed repo versions recompute,
   which is what makes 34 GB tractable.

**Exit:** peak memory for a trace is flat as the fleet grows (ratio assertion
across two fleet sizes, not an absolute threshold); a persisted index and a
freshly computed one produce identical edges.

### Phase 4 — retention, privacy, and drift reporting

`R4` from the design: a stated maximum retention with a privacy rationale, merged
into the threat model. Then the drift queries the versioning exists for — Q2
("what changed"), Q4 ("is this trace still true"), Q5 ("callers of an endpoint
that no longer exists").

**Exit:** retention is bounded by policy, not by disk; the threat model records
it as a control.

---

## 4. Carried through every phase

Not a phase, because doing it at the end never happens:

* **`ScanConfig` replaces module-level configuration** (Finding C). Six
  configuration globals, hand-propagated through `_worker_init`. Phase 2 and
  Phase 3 both add per-run context; each is the natural moment to convert the
  globals they touch.
* **`TypedDict` for records and nodes** (Finding F). Both Phase 2 and Phase 3 add
  fields; typing them as they are added is nearly free and lets `mypy --strict`
  catch schema drift that only tests catch today.
* **`build_metabase_v2.py` shrinks** (Finding D) — dependency parsing moves to
  `repo_utils` when Phase 3 touches the build path. Not before: a gratuitous move
  churns the mutation catalogue's line anchors for nothing.

---

## 5. Method

Unchanged from the 2.0.0 cycle, because it worked — it is what found `OI-14`
through `OI-17`:

1. **Measure before designing.** Every significant decision this cycle was
   changed by a measurement: the trace hotspot was not the file walk; the graph
   is 0.5% of the metabase; traversal costs 1 µs; resolution is syntactic.
2. **TDD with a recorded red.** The failure output goes in the commit message, so
   the test is provably a regression test.
3. **A curated mutant per fix.** Coverage says the test ran; the mutant says it
   would notice. This cycle it repeatedly found vacuous tests.
4. **Gate the discipline, do not rely on it.** Every "remember to X" became a
   gate: complexity, detection version, imports, ReDoS bounds, SRTM.
5. **Record deviations.** The most valuable field in the closed-issues records is
   where the implemented fix differed from the proposed one, and why.

---

## 6. Risks

| Risk | Why it is plausible | Mitigation |
|---|---|---|
| Phase 1 stalls as "just refactoring" | It has no user-visible payoff | Time-box it and let byte-identical output prove it is safe; it is 14 mechanical splits |
| `OI-17` produces confident wrong paths | Chained heuristics look authoritative | Tier on every edge, confidence degradation, a path floor, and `R6` reviewing exactly this |
| Interface-heavy code resolves poorly | T1 needs a concrete declared type | Measure T1/T2/T3 mix on the real fleet **during** Phase 2, not after; if T1 is rare the design needs revisiting |
| Phase 3 schema needs changing later | Persisted keys are the expensive mistake | Phase 0 gates it, and Phase 2 lands first so the data shape is known |
| The forced rescan surprises someone | 2.0.0 already triggers one | Already in the changelog; repeat it in the 3.0 release notes |

---

## 7. What is explicitly not in 3.0

* **Full dataflow analysis.** Phase 2 tracks arguments into parameters. Field
  writes, collections and returned values are out, and the output must say so —
  calling it taint analysis would overclaim.
* **Python and JavaScript reachability.** No declared types, so T1 is unavailable
  and results would be much weaker. JVM first; revisit with measured evidence.
* **A graph or document database.** Settled by measurement in the design doc: the
  traversable skeleton is 0.5% of the metabase, so a graph engine would buy the
  half that was never hard — and Neo4j Community's GPLv3 would reverse the
  no-copyleft position taken in `OI-12`.
* **Scanning historical tags.** Deferred pending the `R3` skew measurement.

---

## 8. Sequencing summary

```
Phase 0  decide            R0, R1, R1b, R2  + skew measurement
   |
Phase 1  separate          14 aggregators, byte-identical output
   |
Phase 2  reach   (OI-13 -> OI-17)     the capability the tool is named for
   |
Phase 3  persist (OI-15)              streaming, SQLite skeleton, Parquet payload
   |
Phase 4  retain  (R4)                 retention policy, threat model, drift queries
```

Phase 0 gates everything. Phases 1–4 are strictly ordered: each consumes the
previous phase's output, and every one of them leaves a working, releasable tool.
