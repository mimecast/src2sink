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

Every issue below is scheduled in
[`src2sink-4.0-plan.md`](../plans/src2sink-4.0-plan.md):

| issue | 4.0 phase |
|---|---|
| `OI-36` sweep | Phase 1 — **done**: 43 → 36, both named clusters fixed, before `OI-34` as planned |
| `OI-43` | **unscheduled** — the gate in step 1 is small and belongs early, beside `OI-36`'s remaining sweep |
| `OI-27`, `OI-34` | Phase 2 — one piece of work; both want an authoritative statement of the estate |
| `OI-20` | Phase 3 |
| `OI-22`, `OI-23` | Phase 4 — `OI-22` is load-bearing for `OI-34`, since `.git` cannot discriminate when a fifth of the fleet lacks it |
| `OI-32` | outside the sequence — measured at 2.4%, do it when someone is already in that code |
| — | §3.5 carries the larger performance lever `OI-32` surfaced: the metabase is **roughly twice the source it describes** |

| id | # | Issue | Effort | Value | Priority |
|---|---|---|---|---|---|
| OI-20 | 20 | Only SQL has a library evidence catalogue | large | deserialization has no family at all; every other sink type is pattern-only | **P1** |
| OI-22 | 22 | No identity when git history is absent | medium | the incremental scan dies on stripped snapshots | **P2** |
| OI-23 | 23 | A repo's own declared version is never recorded | small | half of every version comparison is missing | **P2** |
| OI-27 | 27 | Internal-prefix and api-client configuration must be written by hand | medium | a first scan against an unconfigured fleet silently finds nothing internal | **P1** |
| OI-32 | 32 | The checkout scan is single-threaded and I/O-bound | medium | **Measured:** reads thread 3.0x even on local NVMe but are worth 2.4% of the run; the 78% that dominates is CPU-bound aggregation, which threads cannot touch | **P2** |
| OI-41 | 41 | Aggregation parses the whole metabase 14 times per run | medium | 14 parses -> 3 and 3 edge builds -> 1; aggregation 2.4x faster with peak memory unchanged | **closed** |
| OI-42 | 42 | `--promote-api-clients` validates nothing and silently drops file keys | small | two of five review gates now enforced in code; 50 of 191 candidates would have failed one | **closed** |
| OI-29 | 29 | A caller's reported confidence was whichever edge came last | small | fixed in passing while building the OI-15 index; recorded because it understated real findings | **closed** |
| OI-30 | 30 | The producer scan reads the whole fleet once per binding | small | reported from the field at 70 minutes; the slowest step of a scan bar fleet-wide traces | **closed** |
| OI-31 | 31 | The checkout is walked once per filename, and phases share nothing | small | 25 traversals of a 34 GB tree per run; `--discover-api-clients` was also silently ignored outside a full scan | **closed** |
| OI-34 | 34 | Repo discovery is two levels deep, so nested-subgroup projects are merged | medium | 15 records subsume 111 sub-projects; calls between them vanish as self-edges. **Decided: the project is the unit.** Changes repo identity fleet-wide — a major | **P1** |
| OI-35 | 35 | Api-client discovery rescans the whole fleet once per class | small | reported from the field; node visits grew ~15x per doubling of the repo count | **closed** |
| OI-36 | 36 | Detection paths fail to empty, or to a wrong answer, without emitting a signal | large | **gate shipped in 3.0.0**; sweep phase 1 shipped in 4.0 — the two named clusters fixed, debt **43 → 36**, and the run manifest now counts what could not be parsed. The remaining 36 are the tail | **P1** |
| OI-43 | 43 | Language support is a matrix, and only the JVM column is filled | medium | outside Java/Kotlin, `OI-17`'s T1 and T2 tiers are structurally unreachable — every non-JVM call resolves `low` or not at all, undocumented. Go type declarations are discarded outright | **P1** |
| OI-39 | 39 | The test-path predicate excluded production code and admitted test code | small | `api/latest/` contributed nothing to the metabase, silently; test files beside their code were extracted as though they shipped | **closed** |
| OI-40 | 40 | A candidate's `target_repo` names the client library when the library is its own repo | small | 42 of 191 candidates named the wrong node; the correction agrees with the hand-authored bindings 11/11 | **closed** |

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

**This principle has since been violated at least six more times, and is now tracked as `OI-36`.** Writing it down was not enough: `OI-18`, `OI-13`, `OI-31` and three separate Kotlin gaps in `OI-17` are all the same shape, all shipped after this paragraph was written, and none was found by the tool. A principle with no gate behind it is a statement of intent. `OI-36` carries the measurement — 12 handlers that empty a whole function's result silently — and proposes the gate.

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

---

## 32. The checkout scan is single-threaded and I/O-bound  `OI-32`

**Severity:** Medium — a speed ceiling on a step that now dominates the run, not
a correctness problem.

**Found:** by asking, after `OI-30` and `OI-31` removed the redundant traversals,
whether the *remaining* one should be parallel. The fleet scans in 14 minutes;
what is left is mostly waiting for the filesystem.

### The question

`checkout_scan` walks the tree in one thread, and the producer scan reads every
source file in one thread. Both are I/O-bound, and CPython releases the GIL
around blocking I/O syscalls — so threads genuinely do help here, unlike
CPU-bound Python.

### Why this is not simply "add a ThreadPoolExecutor"

**Extraction already uses processes, and deliberately.** `limits.py` runs each
repo in its own process because a crafted file can hang a tree-sitter C parse or
peg a CPU with catastrophic backtracking, and *neither a Python signal nor a
`concurrent.futures` cancellation can stop C-level work*. Killing the process is
the only reliable reclaim. That is `SEC-2` / `TA-001`, and a thread pool cannot
provide it: **a thread stuck in a C parse cannot be killed.**

So threading is only admissible for work that does not parse untrusted content in
the calling process — the directory traversal and the file *reads*, not anything
downstream of them. Applying it more widely would silently dismantle the bulkhead
while looking like a performance change.

### Where the benefit actually is, and where it is not

| Work | Parallelises? | Why |
|---|---|---|
| **File reads** (producer scan) | **Best candidate** | many independent reads; latency-bound; bounded buffers via `MAX_FILE_BYTES` |
| **Directory traversal** | Partly | sibling directories are independent, but a directory cannot be scanned before its parent is read |
| Parsing / extraction | **No** | must stay in processes for `TA-001` |

~~**And it is filesystem-dependent.** On a network or cloud-backed mount the win
is large, because the cost is per-request latency and concurrency hides it. On
local NVMe it is much smaller, because the cost is bandwidth and one thread can
saturate it.~~

**Corrected by measurement — the discriminator is file *count*, not storage
class.** A cold single-threaded pass over a real fleet on local NVMe ran at
**44 MiB/s**, orders below the device's bandwidth, because the cost is
per-file open latency (**136 µs/file** across 177,693 files averaging 6 KB) and
not throughput. Threads hide that latency on local storage exactly as they would
on a network mount: **3.0x at 16 workers**. The original claim would have
predicted no win and been wrong. See the measurement below.

**The risk that matters here specifically:** the fleet has been observed
*swapping*. Concurrency raises peak resident memory, so on a memory-constrained
host more threads can make the run slower, not faster — the opposite of the
intended effect, and the failure mode `OI-15` was filed about. This risk is
**real but not in the reads** — see below: the read sweep peaked at 430 MiB,
while the phase that dominates the run holds 5.75 GiB in a single process.

### Measured — step 1, on a complete 746-repo fleet

Step 1 asked whether the remaining time is traversal, reads or parsing, and
whether the checkout is local or networked. Both are now answered. Host: 12
cores, 36 GB RAM, checkout on **local internal NVMe (APFS)**. Fleet: 36 GB on
disk, of which the scan touches **1.07 GiB across 177,693 source files** in
119,042 directories. Reference run: **657 s** end to end.

**Where the time goes.** Aggregation timed directly with `--graphs-only`;
extraction obtained by difference.

| Phase | Wall | Nature | Threads help? |
|---|---|---|---|
| **Aggregation** (main process) | **510 s — 78%** | **CPU-bound Python** | **No — GIL** |
| Extraction (746 repos) | ~150 s | already 11 processes | already parallel |
| — directory traversal | 16 s | I/O-bound (42% CPU) | partly |
| — every source file, read cold | 24 s | latency-bound | **yes, 3.0x** |

**Reads do parallelise, on local disk.** All 177,693 files, `F_NOCACHE` to defeat
the page cache:

| workers | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---|---|---|---|---|---|
| uncached | 24.2 s | 15.8 s | 12.5 s | 9.9 s | 8.6 s | **8.1 s** |
| speedup | 1.00x | 1.53x | 1.94x | 2.43x | 2.81x | **3.00x** |

Warm-cache figures track the same curve (17.7 s → 7.4 s, 2.38x). Peak RSS for the
whole sweep: **430 MiB**.

**But the prize is 2.4%.** Threading every read in the fleet at 16 workers saves
**16 s of 657 s**. That is the entire upside of step 2, and it is an upper bound:
reads inside extraction are already spread across 11 processes, so only the
producer scan's share is actually recoverable.

**The premise inverts for the phase that dominates.** This issue argues threads
are admissible because the work is I/O-bound and CPython drops the GIL around
blocking syscalls. That holds for the reads. It does not hold for aggregation,
which is where the run actually goes:

```
--graphs-only:  wall 510.0s   user 422.7s   sys 45.9s
                91.9% of one core, 90.2% of CPU time in user space
                peak RSS 5.75 GiB
```

Only **45.9 s** is system time; **423 s** is Python bytecode. Sampling the live
process shows a flat 97–100% of a single core. Threads cannot touch this — the
GIL argument runs the other way — and its input is not the source tree at all but
**2.2 GB of metabase JSON across 746 files**, roughly double the source it
derives from.

**Processes are not the escape hatch either.** The only mechanism that helps
CPU-bound Python is multiprocessing, and aggregation holds **5.75 GiB resident**.
Four workers would want ~23 GB on a 36 GB host — and the observed host was
already carrying 1.4M pageouts with 2.2 GiB free on the volume. That is this
issue's own stated risk, arriving through the fix rather than the defect.

**What the measurement changes.** Step 2 is still correct and still cheap, but it
is now known to be worth 2.4% and to leave 78% of the run untouched. Nothing here
argues against threading the reads; it argues against expecting it to matter.

**Caveat on method.** Extraction was derived by differencing `--graphs-only`
against a full build that also ran discovery, so ~150 s is an *upper* bound and
aggregation's share is if anything understated. `run-manifest.json` records only
`started_at` and `finished_at`, so none of this was answerable from the tool's own
output — every number above required external instrumentation. Phase timings in
the manifest would make step 1 a property of every run instead of a one-off
exercise, and would have surfaced the 78% without anyone asking.
**This part is now fixed** — see step 0 below.

### Proposed approach

0. ~~**Make the run report its own cost.**~~ **Done — 4.0 phase 0.**
   `run-manifest.json` now carries a `timing` block: total wall clock plus a
   nested per-phase breakdown, shares of the whole run at every depth, and the
   same table printed at the end of every run. Time no phase claimed is reported
   as `unattributed` rather than folded into a neighbour, so the breakdown never
   implies coverage it does not have. `src2sink/run_timing.py`; the recorder
   ignores worker threads by design, so step 2 cannot silently mis-nest it.
   The measurement above would have been a manifest field rather than a
   fortnight's instrumentation.
1. ~~**Measure before building.**~~ **Done — see the measurement above.** The
   answer was not the expected one: the reads parallelise 3.0x even on local
   NVMe, and are worth 2.4% of the run; the 78% that dominates is CPU-bound and
   out of reach of threads entirely.
2. **Thread the reads first**, bounded and configurable, reusing the existing
   `--workers` semantics rather than adding a second unrelated knob.
3. **Keep output deterministic.** Results are sorted today and must stay sorted;
   the producer scan's `seen` dedup is order-sensitive, so parallel reads must
   collect first and order after.
4. **Make `checkout_scan`'s cache thread-safe** — it is a plain dict, and a
   widening walk is a read-modify-write.
5. **Do not thread anything that parses.** `TA-001` is not negotiable, and a test
   should assert the boundary rather than a comment describing it.

### Why it is not urgent

`OI-30` and `OI-31` took the fleet from a 70-minute producer scan to a
14-minute run, and the next structural lever is larger than threading: skipping
repos whose content-addressed version has not changed, which is `OI-15`'s
unfinished step 4 and turns a full rescan into an incremental one. Threading
speeds up work that versioning would let us **not do at all**.

**The measurement now makes that argument quantitative rather than intuitive.**
The dominant cost is 423 s of Python re-deriving graphs from 2.2 GB of metabase
JSON, on every run, for 746 repos of which only a handful changed. Threading
cannot reduce it and multiprocessing cannot afford it at 5.75 GiB resident.
Skipping unchanged repos removes it. `OI-15` step 4 is not merely the larger
lever — on this evidence it is the *only* one that reaches the 78%.

### Suggested tests

* A thread pool never wraps a call that parses repository content — asserted
  structurally, so the `TA-001` boundary cannot erode by accident.
* Output is byte-identical to the single-threaded run over the same fixture,
  including ordering.
* The cache survives concurrent widening from two threads.
* Peak memory does not scale with worker count beyond a stated bound.

## 34. Repo discovery is two levels deep, so nested-subgroup projects are merged OI-34

**Severity:** Medium–High, but **needs a product decision before any fix** — the current behaviour may be intended.
**Status:** open question, not a confirmed defect.

### Observation

`_discover_repos` walks exactly two levels:

```python
for group_dir in sorted(repos_root.iterdir()):
    for repo_dir in sorted(group_dir.iterdir()):
        ...   # everything below repo_dir is treated as repo *content*
```

Every metabase record therefore has a two-segment id — 746 of 746 in the observed fleet, zero nested. But the estate does contain nested-subgroup projects: the staging manifests list paths such as `group/connector/vendor-a/topic-service` and `group/connector/vendor-b/task-service` as **separate projects**, and on disk they sit under one second-level directory.

The result is a single node standing in for many projects:

| metabase record | build-bearing subdirectories it subsumes |
|---|---|
| `group/connector` | 41 |

Across the fleet, **15 records have three or more build-bearing subdirectories and no build file of their own**, collectively subsuming **111 sub-projects**. Those are aggregates rather than repos in any meaningful sense.

### Why it matters for detection

- Every edge to or from any subsumed project is attributed to the aggregate, so the graph cannot say *which* service is involved.
- A call *between* two subsumed projects becomes a self-edge, and `_append_path_edge` drops self-edges outright — so genuine cross-project calls disappear entirely.
- It explains `OI-33`'s identity mismatch from the other side: the resolver produces a *finer* identity than the metabase can represent. `group/repo/some-client` is a real thing; the metabase simply has no node for it.

### Decided: the project is the unit of analysis

**Answered by the fleet owner: option 2.** Nested-subgroup projects get their own
nodes, and the two-level walk is under-counting the fleet.

**And the stated reason changes the fix.** The question above argued from GitLab
nested subgroups, which points at splitting on an external manifest. The owner's
reason is different and sharper:

> a repo can contain both a service and its client

That is the `warehouse-service` / `warehouse-service-client` shape, and it is not
a nesting problem — it is a **multi-module** problem, in a single repo at a single
depth. Its consequences are worse than under-counting:

* A consumer depending on the client resolves to the repo. The service lives in
  the same repo. So *"who depends on the client"* and *"who calls the service"*
  collapse into one node, and the two cannot be told apart.
* A call **from the client to the service** — the entire point of a client
  library — is a call within one repo id, so `_append_path_edge` drops it as a
  self-edge. The hop the client exists to make is the hop that disappears.
* It is the other half of `OI-33`. The resolver returns
  `group/repo/warehouse-client` because that module *is* a distinct publishable
  thing; `OI-33` normalises it back to `group/repo` because the metabase has no
  node for it. Under this decision the resolver was right and the metabase was
  wrong.

**This makes the build module the project boundary**, at least for JVM
multi-module repos — which the original analysis explicitly set aside:

> A build file at the sub-directory is weak on its own: multi-module builds put
> one in every module.

That reasoning was correct for *"is this a nested repo?"* and is exactly backwards
for *"is this a separate project?"*. If a module publishes its own artefact, it is
a thing consumers depend on by name, and that is the definition being adopted.

**Consequence for the fix.** The manifest remains useful — it is authoritative
for what the estate contains — but it is no longer sufficient on its own, because
a service and its client inside one manifest-listed project still need splitting.
The discriminator becomes *"does this directory publish its own artefact"*, which
the identity index already answers: `_build_component_identity_index` maps
coordinates to declaring directories, and that is precisely the set of publishable
modules.

**Migration is already solved in shape, but not in direction.** `_load_discovered`
indexes a stored candidate under its canonical key as well as its stored one, so
a reviewer's accepts survive a target reshaping — written for `OI-33`, and reused
unchanged for `OI-40`.

The *structure* carries to `OI-34`; the *derivation* does not. `_canonical_repo_id`
maps long → short by prefix:

| change | direction | works today |
|---|---|---|
| `OI-33` | `group/repo/module` → `group/repo` | ✅ prefix match |
| `OI-40` | client repo → the service it fronts | ✅ separate derivation, same indexing |
| `OI-34` | `group/repo` → `group/repo/project` | ❌ **not a prefix relation, and ambiguous** |

Under `OI-34` a stored `group/repo` must resolve to one of several children, and
nothing in the path says which. The disambiguator is already in the key:
`_key(target, artifact)` carries the artifact, and the identity index knows which
project publishes it. So the work is a third derivation plugged into the same
indexing, not a new mechanism.

**Consequence for `OI-33`.** No conflict, and no rework. `_canonical_repo_id`
matches the longest *known* id, so the moment the metabase knows
`group/repo/warehouse-client` as a project, the same function stops collapsing it.
It degrades correctly under both models, which is what longest-match bought.

That settles the question and turns this into an implementation task with a known
shape — and a known cost. Repo identity is the most expensive thing in this
system to change, because it keys the metabase layout, every edge, every trace
filename, and the persisted index:

| what assumes two segments | where |
|---|---|
| `repo_id()` returns `f"{group}/{name}"` | `graph_common.py` |
| the record layout is `repos/<group>/<name>.json` | `build_metabase_v2.py`, and a `repos/*/*.json` glob in three places |
| `_discover_repos` walks exactly two levels | `build_metabase_v2.py` |

Already ready for it: `_canonical_repo_id` (`OI-33`) matches the longest known id
rather than truncating, so it handles `group/subgroup/project` correctly today;
`_safe_slug` in `trace_batch` handles any depth; and the `OI-15` index keys on the
id as an opaque string.

**This is a major version.** It changes what `group`/`name` mean and breaks any
consumer that splits a repo id into two parts, which is the project's own
definition of a major bump.

### Why the original question was worth asking anyway

Two defensible positions, and the answer depended on intent:

1. **The repo is the unit of ownership and deployment.** Monorepo sub-projects should be aggregated, and the resolver should normalise down to the repo id (i.e. `OI-33`'s fix is the whole answer).
2. **The project is the unit of analysis.** Nested-subgroup projects deserve their own nodes, and the two-level walk is under-counting the fleet.

These conflict, and the same directory layout supports both readings — an aggregate of 41 sub-projects and a monorepo with 41 modules look identical on disk.

### If option 2 is chosen, note what will not work

- **`.git` presence is not a usable discriminator.** 65 of 746 scanned repos have no `.git` at all, history having been stripped during staging. Absence proves nothing.
- **Path depth is not a discriminator either.** Both a nested repo and an in-repo module are "one level deeper".
- A build file at the sub-directory is weak on its own: multi-module builds put one in every module.

The most reliable signal available is **external**: the staging manifest already enumerates true project paths (`path_with_namespace` per project). Feeding that list in — as an optional `--repo-manifest` — would let the scanner split on real project boundaries without inferring them from the filesystem at all.

### Review — agreed, and it is a question rather than a defect

The two-level walk is confirmed in `_discover_repos`, and framing this as needing
a product decision is right: the same directory layout genuinely supports both
readings, and no filesystem signal separates them. The report is also right that
`.git` is unusable here — and that matters beyond this issue, because `OI-22` is
filed on the same absence.

Two additions:

* **The self-edge consequence is the most serious part and is understated.** A
  call between two subsumed projects becomes a self-edge and is *dropped*, so
  this is not a loss of resolution but a loss of the finding entirely. An
  aggregate of 41 sub-projects is exactly where intra-estate calls concentrate,
  so the edges most likely to be lost are the ones most worth having.
* **The `--repo-manifest` proposal is the strongest option and is also the
  cheapest.** It sidesteps inference completely, and it composes with `OI-27`
  (configuration that must be written by hand): both want an authoritative
  external list of what the estate contains, and one input could serve both.

**Recommendation: take the minimum change now regardless of the decision.**
Recording the aggregation in `summary.notes` is small, unblocks nothing, and
stops a 41-project aggregate reading as a single service in the interim. The
product decision can then be made on evidence — the note makes the scale of the
aggregation visible per repo, which is exactly the input needed to choose.

### Minimum change regardless of the decision

Record what was aggregated, so the coarseness is visible rather than assumed:

```python
summary.notes.append(
    f"aggregate: {n} build-bearing subdirectories treated as one repo; "
    "edges involving them cannot be attributed to a sub-project"
)
```

Today a 41-project aggregate and a single-service repo are indistinguishable in the output. That is `OI-36`
again — a detection path resolving to a wrong answer without emitting a signal.

---

## 36. Detection paths fail to empty, or to a wrong answer, without emitting a signal  `OI-36`

**Severity:** Critical as a *class*, even though each instance looks minor. A tool
whose failure mode is "found nothing" is indistinguishable, on the day it breaks,
from a tool reporting good news.

**Found:** named by the fleet operator after a run, as the recurring shape behind
several separately-filed issues.

**This document already said it.** §6 has carried this principle since 1.1.0, in
almost the same words: *"a detection path that fails to empty without emitting a
signal"*, with the durable fix stated correctly — any detection input resolving
to nothing should say so in the run manifest or the repo's notes.

So the finding here is not the diagnosis. It is that **the principle was written
down and then violated at least six more times**, in releases that shipped after
it. `OI-18`, `OI-13`, `OI-31` and three separate Kotlin gaps inside `OI-17` are
all this shape. A principle with nothing enforcing it is a statement of intent,
and this one has been tested against reality and lost. What is new below is the
measurement and the gate.

### The shape

A detection path encounters something it cannot handle, and returns an empty
result or a wrong one. Nothing is recorded, nothing is printed, and the output is
well-formed. The scan reports success. A reviewer sees fewer findings and has no
way to tell whether that means the estate is clean or the parser broke.

**This is worse than a crash.** A crash is loud, dated, and attributable. A silent
empty is indistinguishable from a genuine result and survives indefinitely,
because there is nothing to notice.

### It is not hypothetical — it is the pattern behind issues already fixed

| issue | what failed | what was emitted |
|---|---|---|
| `OI-18` | namespaced POMs — the standard shape every IDE emits — hit `except ParseError: return []` | nothing; every such repo reported **zero dependencies** |
| `OI-31` | `SKIP_DIRS` matched the absolute path, so a checkout under `/tmp/build` excluded its own tree | nothing; **every manifest** resolved to "not found" |
| `OI-31` | `--discover-api-clients` was unreachable outside a full scan | `Done.`, and no candidates file |
| `OI-13` | Kotlin call sites routed to the Java walker, which needs a node Kotlin never produces | nothing; Kotlin SQL sinks simply absent |
| `OI-17` | Kotlin `is_interface` always false; every Kotlin method's params empty; every Kotlin call's arguments empty | nothing; **no Kotlin path existed anywhere in the fleet** |
| `OI-33` | the resolver failing outright on 8 candidates | `target_repo: ""`, emitted as a candidate |

Six of these were found by accident — by building something else on top and
noticing the foundation was empty. None was reported by the tool.

### Measured: how much of the codebase has this shape

53 exception handlers whose entire body is `return`/`continue`/`pass`. Most are
legitimate — skipping one unreadable file among thousands is correct, and the
per-repo bulkhead (`TA-001`) deliberately isolates failures.

The dangerous subset is where a whole *function's* result empties. There are
**12**:

```
_load_discovered                 (OSError, json.JSONDecodeError)   api_client_discovery.py:97
_load_bindings                   (OSError, json.JSONDecodeError)   api_client_discovery.py:409
load_library_source_map          (OSError, json.JSONDecodeError)   library_source_map.py:27
_read_json                       (OSError, json.JSONDecodeError)   build_metabase_v2.py:488
parse_python_dependencies        tomllib.TOMLDecodeError           dependencies.py:113
_npm_lock_versions               json.JSONDecodeError              dependencies.py:142
parse_npm_dependencies           json.JSONDecodeError              dependencies.py:172
extract_tree_sitter_calls        (KeyError, OSError, ValueError)   ts_extractors.py:162
extract_method_declarations      (KeyError, OSError, ValueError)   ts_extractors.py:286
extract_type_declarations        (KeyError, OSError, ValueError)   ts_extractors.py:327
parse_package_json_dependencies  json.JSONDecodeError              repo_utils.py:184
_literal_hits_in_file            OSError                           trace.py:386
```

Two groups stand out:

* **The three `ts_extractors` handlers are the `OI-17` foundation.** A parse
  failure means that file contributes no calls, no method declarations and no
  type declarations — so it takes part in no path, and the answer is "nothing
  reaches a sink here". Stated with full confidence.
* **The four dependency parsers are `OI-18` in four more places.** A malformed
  `pyproject.toml`, `package.json` or lockfile yields zero dependencies, which is
  the exact failure reported from the field against 2.0.0 for Maven.

### The principle is already established in this codebase — in one place

`--allow-empty-api-clients` exists precisely because of this, and its help text
states it outright:

> Without this, an empty/malformed bindings file is a hard error, **because it
> silently disables all cross-repo API-client detection**.

That is the whole issue, already understood and already acted on — for one file,
once. `_load_bindings` in the promote path still returns `[]` silently, so even
the fix is not applied consistently to its own subject.

### The gate shipped in 3.0.0

`tests/test_silent_failure_gate.py`. An exception handler whose body discards the
error must emit a signal — a note, a warning, a counter, a re-raise — or be
listed with a reason.

**The real surface is 59, not 12.** The earlier count was only handlers that
empty a *whole function's* result; walking every handler found 59. They split:

| | count | |
|---|---|---|
| `_SIGNAL_NOT_NEEDED` | 15 | predicates and path arithmetic where the fallback **is** the answer, plus three documented designs (`open_index`'s cache miss, the `TA-001` bulkhead). Each carries a written reason. |
| `_KNOWN_SILENT` | 44 | frozen debt. A baseline, not an endorsement. |

**What the gate buys today is that the list cannot grow.** A new silent handler
fails the build. A companion test ratchets `_KNOWN_SILENT` downward, so it cannot
become a parking space — which is the failure mode §6 itself demonstrates.

Two entries in the debt list are worth naming, because they are the issue in its
purest form:

* the **four dependency parsers** are `OI-18` in four more places — a malformed
  `pyproject.toml`, `package.json` or lockfile yields zero dependencies;
* the **three `ts_extractors` handlers** are the `OI-17` foundation — a parse
  failure means that file contributes no calls, no methods and no types, so it
  takes part in no path and the answer is *"nothing reaches a sink here"*, stated
  at full confidence.

The gate is also tested against a handler it must catch, because an enforcement
that cannot fire would be this same mistake one level up.

### Remaining: the sweep

**Phase 1 of the 4.0 plan is done — steps 1 and 2 below, for the two clusters
that were the issue in its purest form.** The debt ratchet moved **43 → 36**:

* the **four dependency parsers** now return `(deps, notes)`. Two consequences
  are distinguished, because they differ — a *manifest* that will not parse means
  `dependencies_internal` is incomplete rather than empty; a *lockfile* that will
  not parse means every dependency silently demoted from a resolved version to a
  range, so `resolved` counts understate what is pinned;
* the **three `ts_extractors` passes** share one `_parse_or_note` helper, so a
  file tree-sitter cannot read no longer answers "nothing reaches a sink here" at
  full confidence. All three parse the same file and fail identically, so the note
  is deduplicated: one unreadable file reads as one problem;
* `run-manifest.json` carries `counts.unparsed` —
  `{source_files, manifests, repos_affected, records_unreadable}`. The markers are
  shared constants, because a note whose wording drifts stops being counted and
  the run then reports *fewer* failures than happened.

`records_unreadable` is there because the gate caught the **counter itself**
failing silently — a record it cannot read is a repo whose failures go uncounted,
while the number still looks authoritative. It is counted, warned about, and the
manifest figure is explicitly a lower bound.

**Steps 3 and 4 below, and the remaining 36 handlers, are still open.** So is one
thing phase 1 surfaced and deliberately did not fix:

> **A language with no grammar is still silent, and it is not an `except` block.**
> All three `ts_extractors` passes begin `if ctx.language not in
> supported_languages(): return` — no exception, so the gate cannot see it, and
> no note, so the file contributes no calls, declarations or types and nothing
> says why. **Scala is scanned today and has no grammar**, which is exactly the
> shape `OI-13` and `OI-17` had for Kotlin: counted in the language breakdown,
> present in the report, contributing to no path, reported as clean.
>
> It was left out of phase 1 because the fix is not the same fix. A note per file
> would fire for every Scala file in the estate, so the signal has to be per repo
> per language — a count on the summary rather than a note — and that is a design
> question, not a two-line change. It is also the second time `OI-39`'s lesson has
> landed: **the surface is wider than any list of `except` blocks.**
>
> **Now tracked as `OI-43`**, which is where following the question led: Scala is
> the *honest* gap. The languages that do have grammars resolve at `low` or not
> at all, because `OI-17`'s T1 and T2 tiers read tables filled in for Java and
> Kotlin only — and Go's type declarations are discarded outright.

Not "raise everywhere" — the bulkhead exists for good reasons and a hostile repo
must not stop a run. The rule is **degrade loudly, never silently**:

1. **Every silent handler either records or justifies.** A handler that empties a
   whole function's result must append to `summary.notes` (the mechanism already
   exists and `unparsed_ecosystem_notes` already does this) or carry a comment
   saying why silence is right there. A gate can enforce the choice, the way
   `test_detection_input_coverage` enforces fingerprint coverage.
2. **Surface the count in the run manifest.** "4,318 files parsed, 11 unparsable"
   turns an invisible failure into a number someone can watch move. A run where
   that number jumps is a run to investigate.
3. **Distinguish "no findings" from "no data" at the report level.** A repo whose
   extraction produced nothing must not render identically to a repo that was
   examined and found clean.
4. **Apply the `--allow-empty-*` pattern to every config load**, not just
   api-clients: a config that parses to nothing disables whatever it configures,
   and that is a decision the operator should make explicitly.

### Suggested tests

* ~~A deliberately malformed manifest of each supported ecosystem produces a
  **note**, not merely an empty list~~ — **done** for Python and npm in
  `tests/test_oi36_parse_failures.py`; the remaining ecosystems are identity-only
  and already covered by `unparsed_ecosystem_notes`.
* ~~An unparsable source file is counted in the run manifest.~~ **Done** —
  `counts.unparsed.source_files`.
* A repo with zero findings and a repo with zero *data* are distinguishable in
  the rendered output.
* A structural gate: every `except` whose body is a lone `return`/`continue`/`pass`
  is either on an allowlist with a stated reason or records something. This is the
  test that stops the class recurring, and it is the one worth writing first.

### Why P0

`OI-13` and `OI-17` between them meant **the entire Kotlin half of a JVM fleet
returned clean results while detecting nothing**, across two releases, and neither
was found by the tool. The estate's exposure was reported as low, confidently, on
no evidence. Every other issue here is about finding more; this one is about
knowing when we have found nothing because we broke.

---

## 43. Language support is a matrix, and only the JVM column is filled  `OI-43`

**Severity:** High. Not a crash and not an empty result — a **systematically
degraded** one that is indistinguishable from a good one. Outside Java and
Kotlin, `OI-17`'s two strong resolution tiers are structurally unreachable, and
nothing says so.

**Found:** by asking, after `OI-36` phase 1 flagged Scala as a language with no
grammar, whether any other language was in the same position. Scala turned out to
be the *safe* case.

### Scala is the honest gap; the others pretend

Scala has no tree-sitter grammar, so every AST pass returns before doing
anything. That is documented under Known limitations, and `tree-sitter-scala` is
on PyPI (0.26.2) — it was deferred, not blocked.

The languages that **do** have grammars are the problem. `FIELD_NODE_TYPES` and
`SUPERTYPE_NODE_TYPES` in `extractors/ast_walk.py` have entries for **Java and
Kotlin only**. Those two tables are exactly what `OI-17` step 3 resolves against:
**T1** is a declared field's type, **T2** is an interface expanded to its
implementations.

| language | grammar | class | method | call | **field** | **supertype** |
|---|---|---|---|---|---|---|
| java | yes | yes | yes | yes | **yes** | **yes** |
| kotlin | yes | yes | yes | yes | **yes** | **yes** |
| typescript / tsx | yes | yes | yes | yes | **no** | **no** |
| javascript | yes | yes | yes | yes | **no** | **no** |
| python | yes | yes | yes | yes | **no** | **no** |
| go | yes | *broken* | yes | yes | **no** | **no** |
| scala | **no** | no | no | no | no | no |

Measured on the same three-type source (interface, implementation, caller holding
the interface as a field) written in each language:

```
java        type-decls=3  fields=1  supertypes=1   resolved by T2  (medium)
typescript  type-decls=2  fields=0  supertypes=0   resolved by T3  (low)
go          type-decls=0  fields=0  supertypes=0   resolved by T3  (low)
python      type-decls=2  fields=0  supertypes=0   resolved by T3  (low)
```

**Every non-JVM call resolves at `low` or not at all.** T3 is a unique-name
match, so where a method name is not unique in the repo the call is dropped and
there is no path. TypeScript is the sharpest case: it *has* interfaces and
declared field types, and neither is read.

Nothing in `README.md`, `SCHEMA.md` or the 3.0.0 notes scopes tainted paths to
the JVM. A reader is told paths are found, and is not told for which languages.

### Go is a defect, not a gap

`type_declaration` **is** listed in `CLASS_NODE_TYPES` for Go, so the wiring
looks complete. But Go puts the name on the child `type_spec`, while
`_declaration_name` asks the node itself for a `name` field, gets `None`, and
`continue`s. **Every Go type declaration is silently discarded.**

That is precisely `OI-13`'s shape: *routed to a walker that needs a node the
grammar never produces.* It has now happened for Kotlin calls, for Kotlin
interfaces and parameters, and for Go types.

### A fourth surface, found while building the gate

**Nothing anywhere checks `tree.root_node.has_error`.** tree-sitter does not
raise on source it cannot parse — it returns a tree with `ERROR` nodes — so
`_parse_or_note` never fires and the file yields whatever the regex passes
managed plus nothing from the AST.

Demonstrated with valid Kotlin that this grammar rejects (`tree_sitter_kotlin`
1.1.0 does not accept a single-line class body):

```
class Svc { fun go() { db.query("SELECT 1") } }
  -> root_node.has_error = True
  -> 5 observations, 0 notes, exit code 0
```

This is the one most likely to bite in practice, because **grammar versions lag
language versions**. Every pinned grammar in `pyproject.toml` is a snapshot; a
repo using syntax newer than the pin degrades quietly across however many files
use it, and the run reports success.

It is not fixed here because sizing it needs the fleet: a note per file could be
very loud if a pinned grammar disagrees with a common idiom, which is the same
noise question that kept the per-language note out of `OI-36` phase 1. Measure
`has_error` rates across the estate first, then decide between a per-file note
and a per-repo count.

### Why no gate caught this

`OI-36`'s gate looks for an `except` whose body discards the error. There is no
exception here. There is a `dict.get(language, frozenset())` returning empty and
a loop that runs zero times.

This is the **third** distinct surface for the same failure, after the handlers
themselves and `OI-39`'s over-broad regex. The lesson `OI-39` already taught —
*the surface is wider than any list of `except` blocks* — applies again, and the
enforcement has to follow the same pattern: a table that must be complete, or
carry a stated reason for each hole.

### Proposed approach

1. ~~**A language-support gate, first.**~~ **Done** —
   `tests/test_language_support_matrix.py`. Two checks, because one of them would
   have missed the worst case: a *structural* check that every scanned language
   has a grammar and every grammar-backed language appears in every table (or is
   exempted by name with a reason, exactly as `_SIGNAL_NOT_NEEDED` does), and a
   *behavioural* check that runs a three-type sample through each language and
   compares what comes out against a frozen record. The second is what catches
   Go, whose table entry is present and whose output is empty — **a table can be
   complete and wrong.**

   The frozen record was measured, not predicted: the hand-written first draft
   was wrong for four of the seven languages, which is its own argument for the
   behavioural half.
2. ~~**Fix Go's `type_spec` lookup.**~~ **Done.** `CLASS_NODE_TYPES["go"]` reads
   `type_spec` / `type_alias` rather than `type_declaration`, which also fixes the
   grouped `type ( A struct{}; B interface{} )` form — keying on the declaration
   could at best have found the first spec. `_is_interface` reads Go's answer from
   the spec's `type` child.

   **A second defect surfaced doing it, and is fixed alongside**, because step 2
   would otherwise have been inert. Every other grammar nests a method inside its
   class, so ownership is answered by containment; **Go declares methods outside
   the type** and names the owner in the receiver, so every Go method was recorded
   with no class at all. Types without that fix would have been indexed with
   nothing ever resolving to them. `_go_receiver_owner` reads the receiver's
   declared type, and `*T` and `T` name the same type.

   Go still has no fields or supertypes, so **T1 and T2 remain out of reach until
   step 3** — the tier a Go call resolves at is unchanged by this. What changed is
   that the declarations exist to be resolved against at all.

   The language gate needed a new column to see the second fix: the receiver
   change altered no method *count*, only whether each method knew its owner, so
   `owned_methods` is now recorded beside `methods`. A gate that watches the wrong
   number watches nothing.
3. ~~**Fill `FIELD_NODE_TYPES` and `SUPERTYPE_NODE_TYPES`.**~~ **Done**, and the
   tiers moved:

   | language | before | after |
   |---|---|---|
   | TypeScript / TSX | T3 `low` | **T2 `medium`** |
   | Python | T3 `low` | **T1 `high`** |
   | Go | T3 `low` | T3 `low` — see below |
   | JavaScript | T3 `low` | supertypes only; T1 is **intrinsically** impossible |

   TypeScript reads both member forms, and the second is the one that matters:
   `constructor(private dao: Dao)` declares a member and injects it in one line,
   which is the Angular/NestJS shape — the same trap `class_parameter` was for
   Kotlin. A parameter *without* an accessibility modifier declares nothing, so
   without that check every method's arguments would become fields of its class.

   **`interface_declaration` was also missing from TypeScript's
   `CLASS_NODE_TYPES`**, so a TS interface was never recorded as a type at all.
   Supertypes alone would not have helped: the declaration a call resolves *to*
   was missing, not merely the edge to it.

   Python's bases are read from the `superclasses` **field**, never by walking:
   `class Svc(Repo, Base)` is an `argument_list`, and so is `helper(alpha, beta)`
   inside a method. Only annotated attributes count — `plain = 1` states no type,
   and recording it with an empty one would leave a reader unable to tell
   "untyped" from "typed as nothing".

   Go's embedding is its only syntactic supertype: an unnamed struct field, or a
   bare `type_elem` in an interface. Both promote another type's methods, which
   is exactly T2's question.

   **Go's fixable half is now fixed too: it reaches T1 `high` on a concrete
   field.** `_normalise_receiver` knew only `this.` and `self.`, while Go's
   receiver name is whatever the author chose — `func (s *Svc)` makes `s.repo`
   exactly `this.repo`, but nothing carried `s` from the declaration to the call,
   so every Go field access was discarded as an unfollowable chain. Go had the
   facts and could not use them.

   A Go method declaration now records `self_name`, and resolution looks it up by
   `(enclosing_class, enclosing_method)` — the only place both facts exist. The
   key is absent on every other language's methods rather than carrying a null
   across the fleet. Pointer and value receivers behave identically, because the
   name is arbitrary either way.

   **What is not fixable is that Go interface satisfaction is structural.**
   `JdbcRepo` declares no link to `Repo`, so no syntactic read can connect them
   and T2 can never fire through a Go interface. That is the one gap in this
   issue that is a property of the language rather than unfilled work, and
   `test_go_interfaces_remain_unreachable_by_design` says so.

   | language | before step 3 | now |
   |---|---|---|
   | TypeScript / TSX | T3 `low` | **T2 `medium`** |
   | Python | T3 `low` | **T1 `high`** |
   | Go, concrete field | T3 `low` | **T1 `high`** |
   | Go, interface field | T3 `low` | unreachable — structural typing |
   | JavaScript | T3 `low` | supertypes only; T1 impossible |
4. ~~**Say so in the output, not only in a changelog.**~~ **Done.** One note per
   repo per language whose extraction is limited, never per file — Scala alone
   would otherwise put a note on every Scala file in the estate, and a signal
   that loud stops being read.

   **The gaps are computed from the grammar tables, never restated.** A
   hand-maintained list of "languages we do not fully support" is exactly what
   rotted into this issue, and it would go stale the moment a table gained an
   entry; reading the live tables means a repo's note and the code's behaviour
   cannot disagree. `coverage_gaps()` returns empty for Java and Kotlin, so a
   fully covered repo stays quiet.

   `run-manifest.json` carries `counts.resolution_gaps` — repos affected, per
   language — computed in the **same pass** as `counts.unparsed`, because reading
   the fleet twice to produce two numbers would be `OI-41`'s defect reintroduced
   for the sake of tidiness.
5. **Add Scala last**, unless the fleet is Scala-heavy. Adding the grammar
   without steps 1–3 buys calls and methods with no resolution behind them, which
   is how this state was reached in the first place.
6. **Check `has_error` and report it.** Size it against the fleet first — count
   how many files parse to an `ERROR` tree today — then choose a per-file note or
   a per-repo count. `_parse_or_note` is already the single choke point all three
   AST passes go through, so the change itself is small; only the noise question
   is open.

### Why it is not P0

Nothing here is wrong in the sense of *false*. A `low`-confidence T3 path is a
real path, honestly labelled; the estate's Java and Kotlin — the bulk of the
observed fleet — has the full picture. What is missing is coverage the reader has
no way to know is missing, which is why the gate and the note matter more than
the grammars.
