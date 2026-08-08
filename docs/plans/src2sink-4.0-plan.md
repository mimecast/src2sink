# src2sink 4.0 — unified plan

**Supersedes** the unfinished parts of [`src2sink-3.0-plan.md`](src2sink-3.0-plan.md),
which is otherwise complete: `OI-13`, `OI-15`, `OI-17`, `OI-29`, `OI-30`, `OI-31`,
`OI-33`, `OI-35` through `OI-42` all shipped in 3.0.0 or the 3.1.0 cycle. That
plan's Phase 4 never started and moves here intact.

**Inputs:** the seven remaining open issues, the fleet measurements taken during
3.0.0, and one decision already made — `OI-34`'s.

---

## 1. What 4.0 is for

**Make the answer trustworthy at estate scale, and make the estate's shape
something the tool is told rather than something it infers.**

3.0 made the tool *capable*: it says which entry points reach which sinks, over a
fleet too large to hold in memory. 4.0 is about the two things that limit whether
those answers can be believed:

| Clause | Work | Tracked as |
|---|---|---|
| "the unit of analysis is the project, not the directory layout" | repo identity, driven by an authoritative list | `OI-34`, `OI-27` |
| "a detection path that finds nothing says so" | the silent-failure sweep | `OI-36` |
| "every sink type has evidence, not just SQL" | the boundary catalogue | `OI-20` |
| "identity survives a stripped checkout" | version and identity gaps | `OI-22`, `OI-23` |
| "the run reports its own cost" | phase timings, record size, threading | `OI-32` and §3.5 |
| (from the 3.0 plan) "retention is bounded by policy" | retention, privacy, drift | Phase 4 below |

**`OI-34` is why this is a major.** It changes what `group`/`name` mean and breaks
any consumer that splits a repo id into two parts — the project's own definition
of a major bump.

---

## 2. The decision already made, and what it changed

**The project is the unit of analysis**, answered by the fleet owner. The stated
reason reshaped the fix:

> a repo can contain both a service and its client

That is not a nesting problem. It is a **multi-module** problem in one repo at one
depth — `warehouse-service` and `warehouse-service-client` side by side — and its
consequences are worse than under-counting:

* *"who depends on the client"* and *"who calls the service"* collapse into one
  node and cannot be told apart;
* a call **from the client to the service** — the entire point of a client
  library — is a call within one repo id, so `_append_path_edge` drops it as a
  self-edge. **The hop the client exists to make is the hop that disappears.**

So the discriminator is *"does this directory publish its own artefact"*, which
`_build_component_identity_index` already answers. The original analysis set build
files aside as weak — correct for *"is this a nested repo?"*, backwards for *"is
this a separate project?"*.

Already forward-compatible: `_canonical_repo_id` (`OI-33`) matches the longest
*known* id, so it stops collapsing module paths the moment the metabase knows
them.

---

## 3. Phases

Each phase is independently releasable and leaves the tool working.

### Phase 0 — see the cost (`OI-32`, part)

**Small, and first, because everything below is judged by it.**

`run-manifest.json` records only `started_at` and `finished_at`. Every number in
the 3.0.0 measurements required external instrumentation — including the finding
that aggregation was 78% of the run, which nobody suspected. Phase timings in the
manifest make that a property of every run instead of a one-off exercise.

**Exit:** a run reports where its time went, per phase, without a profiler.

### Phase 1 — the silent-failure sweep (`OI-36`)

The gate shipped in 3.0.0 and holds the line at **43 frozen handlers**. This is
the sweep it was deliberately scoped ahead of.

Two clusters first, because they are the issue in its purest form:

* **the four dependency parsers** — `OI-18` in four more places: a malformed
  `pyproject.toml`, `package.json` or lockfile yields zero dependencies;
* **the three `ts_extractors` handlers** — the `OI-17` foundation, where a parse
  failure means a file takes part in no path and the answer is *"nothing reaches a
  sink here"*, at full confidence.

Each fix is small: record to `summary.notes`, which already exists and which
`unparsed_ecosystem_notes` already uses. The ratchet then locks the gain.

**Do this before `OI-34`**, so a fleet-wide identity change cannot add new silent
paths while nobody is watching.

**Exit:** `_KNOWN_SILENT` materially smaller; the run manifest carries a count of
what could not be parsed.

### Phase 2 — the estate's shape, told rather than inferred (`OI-27`, `OI-34`)

These are one piece of work with two issue numbers. Both want the same thing: an
authoritative statement of what the estate contains, rather than a guess from the
filesystem.

1. **`OI-27` first** — a first scan against an unconfigured fleet silently finds
   nothing internal, which is the `OI-36` shape in the place a new user meets it
   first. Candidate discovery exists (`--discover-api-clients`) and 3.1.0 made
   `promote` validate; what remains is the internal-prefix half and refusing to
   proceed silently when neither is configured.
2. **`OI-34`** — the project becomes the unit. Split on *published artefact*,
   optionally corroborated by a `--repo-manifest` listing true project paths.

**This is the expensive one.** Repo identity keys the metabase layout, every
edge, every trace filename, and the persisted index:

| what assumes two segments | where |
|---|---|
| `repo_id()` returns `f"{group}/{name}"` | `graph_common.py` |
| the record layout `repos/<group>/<name>.json` and its `repos/*/*.json` glob | `build_metabase_v2.py`, three sites |
| `_discover_repos` walks exactly two levels | `build_metabase_v2.py` |

Already ready: `_canonical_repo_id`, `_safe_slug` (any depth), and the `OI-15`
index (opaque string key).

**Migration is solved in shape but not direction.** `_load_discovered` indexes a
stored candidate under alternate derivable keys, so reviewer decisions survive a
target reshaping — written for `OI-33`, reused unchanged for `OI-40`. But
`_canonical_repo_id` maps long → short by prefix:

| change | direction | works today |
|---|---|---|
| `OI-33` | `group/repo/module` → `group/repo` | ✅ prefix match |
| `OI-40` | client repo → the service it fronts | ✅ separate derivation, same indexing |
| `OI-34` | `group/repo` → `group/repo/project` | ❌ **not a prefix relation, and ambiguous** |

The disambiguator is already in the key: `_key(target, artifact)` carries the
artifact, and the identity index knows which project publishes it. So it is a
third derivation plugged into the same indexing, **not a new mechanism**.

**Exit:** a service and its client are separate nodes; a call between them is an
edge rather than a dropped self-edge; promoted bindings survive the identity
change.

### Phase 3 — evidence for every sink type (`OI-20`)

Deserialization has no family at all, and every sink type except SQL is
pattern-only. The `sql` family's evidence model — a library hint, a receiver, and
file-scoped evidence weighted by locality — is the shape to generalise, and
`OI-26` is the record of what happens when those signals are collapsed.

The observation layer makes this cheaper than it was: what a library *means* is a
derivation, so a catalogue change costs a re-derive rather than a fleet rescan.

**Exit:** at least one non-SQL family with a library catalogue; the confidence a
match earns is derived from its anchoring, as `OI-37` established for `http-in`.

### Phase 4 — identity without git, and the repo's own version (`OI-22`, `OI-23`)

`OI-22`: 65 of 746 repos in the observed fleet have **no `.git` at all** — history
stripped during staging — and the incremental scan keys on the git sha. `OI-23`:
a repo's own declared version is never recorded, so half of every version
comparison is missing.

Both are small, both are prerequisites for the versioning design's drift queries,
and `OI-22` is already load-bearing for `OI-34`: `.git` presence cannot be used as
a discriminator precisely because a fifth of the fleet lacks it.

**Exit:** a stripped checkout scans incrementally; a record states the version its
own build declares.

### Phase 5 — retention, privacy, and drift reporting

*Moved from the 3.0 plan's Phase 4, unstarted and unchanged.*

`R4` from the versioning design: a stated maximum retention with a privacy
rationale, merged into the threat model. Then the drift queries the versioning
exists for — Q2 (*what changed*), Q4 (*is this trace still true*), Q5 (*callers of
an endpoint that no longer exists*).

**Exit:** retention is bounded by policy, not by disk; the threat model records it
as a control.

---

## 3.5. Performance: what is actually left

`OI-41` took aggregation from 14 metabase parses to 3 and 3 service-edge builds
to 1 — **2.4x on aggregation with peak memory unchanged**. What remains, in order
of size:

### The metabase is roughly twice the source it describes

The measurement that mattered and has not been acted on:

> its input is not the source tree at all but **2.2 GB of metabase JSON across
> 746 files, roughly double the source it derives from**

1.07 GiB of scanned source produces 2.2 GB of records. Aggregation's remaining
cost is three passes over that, and 423 s of CPU-bound Python is largely the cost
of decoding and walking it. **Shrinking the record shrinks the dominant phase**,
and does so without touching a single aggregator.

Where the weight is: `OI-17` step 3 widened call observation to every call, and
call sites are ~75% of nodes even after the unresolvable-call prune. Each carries
the full node envelope — a constructed `id` string, `repo`, `file`, `language`,
`framework`, `kind`, `family`, `confidence`, `pii_classification`, `data_class` —
most of which is identical across every node in a file.

Candidates, cheapest first, each to be **measured before building** since that
discipline is what caught every wrong prediction this cycle:

1. **Drop what is derivable.** `repo` is on every node and is also the record's
   identity. `id` is a deterministic function of the other fields.
2. **Hoist per-file constants.** `file`, `language` and `framework` repeat for
   every node in a file.
3. **Omit defaults.** `pii_classification` and `data_class` are null on the large
   majority of nodes.

This is a `SCHEMA_VERSION` change and therefore a major — which is why it belongs
here rather than in a 3.x.

### Threading the reads (`OI-32`, step 2)

Measured and honest: **worth 2.4% of the run.** Reads parallelise 3.0x even on
local NVMe, because the cost is per-file open latency (136 µs across 177,693
files) and not bandwidth. But threading every read saves 16 s of 657 s, and it is
an upper bound — extraction's reads are already spread across 11 processes.

Still correct, still cheap, and **not to be expected to matter**. Do it after the
record-size work, and only if the manifest timings from Phase 0 still show reads
as worth attacking.

Constraints if it is done: keep output deterministic (the producer scan's `seen`
dedup is order-sensitive, so collect first and order after); make
`checkout_scan`'s cache thread-safe; and **thread nothing that parses**, because a
thread stuck in a tree-sitter C parse cannot be killed and that is the whole
reason `limits.py` uses processes (`TA-001`).

### What is closed off

**Multiprocessing aggregation is not the escape hatch.** It holds 5.75 GiB
resident; four workers would want ~23 GB on a 36 GB host already carrying 1.4M
pageouts. That is `OI-15`'s risk arriving through the fix.

---

## 4. Carried through every phase

Unchanged from the 3.0 plan, because none of it happened and all of it still
applies:

* **`ScanConfig` replaces module-level configuration** — six globals
  hand-propagated through `_worker_init`. Phase 2 adds per-run context and is the
  natural moment.
* **`TypedDict` for records and nodes** — Phase 2 and §3.5 both change record
  shape; typing fields as they move is nearly free and lets `mypy --strict` catch
  drift that only tests catch today.
* **`build_metabase_v2.py` shrinks** — dependency parsing moves to `repo_utils`
  when Phase 1 touches those parsers anyway.

---

## 5. Method

Unchanged, because it is what found everything in the 3.0 cycle:

1. **Measure before designing.** Every significant prediction that was *not*
   measured this cycle turned out wrong: threading was assumed useless on local
   disk and is 3.0x; the widening was estimated at +20% and was 3.4x; Phase 1 was
   judged unnecessary and was 67% of the dominant phase.
2. **TDD with a recorded red**, so the test is provably a regression test.
3. **A curated mutant per fix.** Coverage says the test ran; the mutant says it
   would notice. It repeatedly found vacuous tests this cycle.
4. **Gate the class, not just the instance.** `OI-36`'s silent-failure gate and
   the import-style gate both exist because the same defect shipped repeatedly and
   was caught by review every time. A mistake review always catches is a mistake
   nothing prevents.

---

## 6. Sequencing, and why

```
Phase 0  see the cost      manifest phase timings                  small
   |
Phase 1  say nothing       OI-36 sweep, 43 handlers                medium
   |                       (before OI-34, so identity work cannot add silent paths)
Phase 2  estate shape      OI-27 then OI-34  ← the major           large
   |
Phase 3  evidence          OI-20 boundary catalogue                large
   |
Phase 4  identity gaps     OI-22, OI-23                            small
   |
Phase 5  retain            R4 retention, drift queries             medium
   |
§3.5     record size       the 2x metabase — schema change, major  medium
```

`OI-32`'s threading sits outside the sequence: it is worth 2.4%, it is cheap, and
it should be done when someone is already in that code — not scheduled.
