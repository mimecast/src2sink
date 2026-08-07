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
| OI-20 | 20 | Only SQL has a library evidence catalogue | large | deserialization has no family at all; every other sink type is pattern-only | **P1** |
| OI-22 | 22 | No identity when git history is absent | medium | the incremental scan dies on stripped snapshots | **P2** |
| OI-23 | 23 | A repo's own declared version is never recorded | small | half of every version comparison is missing | **P2** |
| OI-27 | 27 | Internal-prefix and api-client configuration must be written by hand | medium | a first scan against an unconfigured fleet silently finds nothing internal | **P1** |
| OI-32 | 32 | The checkout scan is single-threaded and I/O-bound | medium | 14-minute fleet scan is now dominated by file I/O; gains are filesystem-dependent and must be measured first | **P2** |
| OI-29 | 29 | A caller's reported confidence was whichever edge came last | small | fixed in passing while building the OI-15 index; recorded because it understated real findings | **closed** |
| OI-30 | 30 | The producer scan reads the whole fleet once per binding | small | reported from the field at 70 minutes; the slowest step of a scan bar fleet-wide traces | **closed** |
| OI-31 | 31 | The checkout is walked once per filename, and phases share nothing | small | 25 traversals of a 34 GB tree per run; `--discover-api-clients` was also silently ignored outside a full scan | **closed** |
| OI-33 | 33 | Discovery's two passes never agree, so `discovery_method: "both"` is unreachable | small | 23 candidates that two independent evidence paths corroborate are split into weaker halves; 79 would promote to broken edges | **P1** |
| OI-34 | 34 | Repo discovery is two levels deep, so nested-subgroup projects are merged | medium | 15 records subsume 111 sub-projects; calls between them vanish as self-edges. **Needs a product decision first** | **P1** |
| OI-35 | 35 | Api-client discovery rescans the whole fleet once per class | small | reported from the field; node visits grew ~15x per doubling of the repo count | **closed** |
| OI-36 | 36 | Detection paths fail to empty, or to a wrong answer, without emitting a signal | large | cross-cutting: 12 whole-function silent failures found; the pattern behind `OI-18`, `OI-31`, `OI-13` and three Kotlin gaps | **P0** |
| OI-37 | 37 | The Express inbound-endpoint pattern has no receiver, so any `.get("…")` is an HTTP route | small | 10,225 of 10,245 Express endpoints are false, all at `confidence: "high"`; two thirds of every inbound endpoint in the fleet, and 43 of 94 fleet traces | **P1** |
| OI-38 | 38 | Only the build indexes traces, so the trace index never describes the batch that just ran | small | the index and its catalogue-coverage figure are always one run stale, and absent entirely on a first run | **P2** |

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

**And it is filesystem-dependent.** On a network or cloud-backed mount the win is
large, because the cost is per-request latency and concurrency hides it. On local
NVMe it is much smaller, because the cost is bandwidth and one thread can
saturate it.

**The risk that matters here specifically:** the fleet has been observed
*swapping*. Concurrency raises peak resident memory, so on a memory-constrained
host more threads can make the run slower, not faster — the opposite of the
intended effect, and the failure mode `OI-15` was filed about.

### Proposed approach

1. **Measure before building.** Is the remaining 14 minutes traversal, reads, or
   parsing? Is the checkout on local or network storage? A run with `strace -c`
   or a simple phase timer answers both, and decides whether this is worth doing
   at all. Cheap, and it is the step that stops this being a guess.
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

### Suggested tests

* A thread pool never wraps a call that parses repository content — asserted
  structurally, so the `TA-001` boundary cannot erode by accident.
* Output is byte-identical to the single-threaded run over the same fixture,
  including ordering.
* The cache survives concurrent widening from two threads.
* Peak memory does not scale with worker count beyond a stated bound.

## 33. Discovery's two passes never agree: `discovery_method: "both"` is unreachable OI-33

**Severity:** Medium — no wrong output, but the highest-value candidates are silently split into two weaker halves.
**Status:** open. Found on the first successful discovery run (226 candidates), once the quadratic scan was fixed and the pass could complete at all.

### Symptom

```
discovery_method: dependency  125
discovery_method: call-site   101
discovery_method: both          0
```

Never one. `both` is the strongest signal the design produces — a declared dependency *and* an observed call site independently resolving to the same service — and §4 explicitly calls for it. It cannot currently occur.

### Root cause

The two passes emit `target_repo` in different shapes. Segment counts across the 226 candidates:

| pass | 2 segments | 3 | 4 | empty |
|---|---|---|---|---|
| call-site (`_apply_demand_side`) | **101** | 0 | 0 | 0 |
| dependency (`_collect_candidates`) | 32 | **79** | 6 | **8** |

The demand-side pass derives `target_repo` from `repo_id(data)`, which is `f"{group}/{name}"` — the metabase's canonical identity. The supply-side pass takes it from `resolve(coord)`, the component-identity index, which resolves a coordinate to *the directory declaring it*. When the declaring directory is a build module inside a repo, that is one or more segments deeper than the repo id.

`_apply_demand_side` then does an exact-string lookup:

```python
by_target = {c["target_repo"]: (k, c) for k, c in cands.items() if c["target_repo"]}
...
existing = by_target.get(target)          # target is "group/repo"
if existing is not None:                  # key is "group/repo/module" -> never hits
    ...
    cand["discovery_method"] = "both"     # unreachable for 79 of 117 candidates
```

The merge logic is correct. It is fed a key it can never match.

### Impact

Resolving the supply-side values to their owning repo id yields **23 candidates that should be `both`** — the ones two independent evidence paths agree on. Today a reviewer sees them as two unrelated `dependency` and `call-site` entries and triages each on weaker evidence, with nothing indicating they corroborate.

Two further consequences of the same malformed value:

- A 3- or 4-segment `target_repo` matches no node in the graph, so those 79 candidates would produce **broken edges** if promoted as-is — not merely unmerged ones.
- The **8 empty-string** targets are the same resolver failing outright. They should be dropped or flagged, not emitted as candidates with `target_repo: ""`.

### Proposed fix

**Do not truncate to two segments.** `group/subgroup/repo` is a legitimate GitLab path and this estate contains such repos (see `OI-34`), so a segment-count rule would corrupt them. Resolve against the set of ids the metabase actually knows, longest match first:

```python
def _canonical_repo_id(resolved: str, known: frozenset[str]) -> str | None:
    """Map a resolved coordinate path to the repo id that owns it.

    The identity index resolves a coordinate to the directory that declares it,
    which may be a build module *inside* a repo. Truncating to two segments is
    wrong — `group/subgroup/repo` is a valid path — so match the longest known
    repo id instead. That resolves in-repo modules and nested repos alike, and
    depends on neither path depth nor on `.git` being present (65 of 746 repos
    in the observed fleet have no `.git` at all).
    """
    parts = resolved.split("/")
    for n in range(len(parts), 0, -1):
        candidate = "/".join(parts[:n])
        if candidate in known:
            return candidate
    return None
```

Apply it where `_collect_candidates` sets `target_repo`, so both passes speak one identity from the start. Verified against the fleet: **117 of 117** supply-side targets resolve to a known repo id, none unresolvable.

Keep the raw resolved path — it is strictly more information than the repo id, and `OI-34` may want it:

```python
cand["target_repo"] = _canonical_repo_id(resolved, known) or ""
cand["target_module"] = resolved      # e.g. "group/repo/some-client"
```

### Review — confirmed, and the blast radius is larger than stated

Root cause verified in the source. `_collect_candidates` sets
`target_repo = resolve(coord) or ""` (the declaring *directory*), while the
demand-side target comes from the inbound index, built from `repo_id(data)` —
`f"{group}/{name}"`. The exact-string lookup in `_apply_demand_side` cannot
bridge them. The proposed longest-known-match fix is the right shape: it needs no
depth rule and no `.git`, both of which this estate defeats.

Three further consequences of the same malformed value, none of them in the
report:

* **It suppresses `paths` too.** `paths_by_repo.get(target, ())` is keyed by repo
  id, so a 3-segment target yields *no* paths — and a binding with no paths
  cannot match a route. The 79 candidates are not merely unmerged, they are
  substantively weaker.
* **It suppresses confidence, as a direct consequence.** `_confidence` returns
  `"high" if has_paths else "medium"`, so those candidates are capped at `medium`
  *because* of the bad key. Fixing the identity will move a number of them to
  `high` — expect the confidence distribution to shift, and do not read that as a
  regression.
* **`service_aliases` is corrupted.** It is `[target.split("/")[-1]]`, which for
  `group/repo/some-client` yields `some-client` — the build module, not the
  service. Alias matching is how a hand-rolled caller is recognised, so this
  misdirects the very pass the alias exists for.

**The defect was rationalised into the design.** `_confidence` already carries
the comment `# resolved to a path not present as a scanned repo` for exactly this
case — the code knew the state occurred and treated it as legitimate rather than
as a symptom. Worth changing that comment when the fix lands, or the next reader
will re-derive the same wrong conclusion.

**On the tests:** `test_both_is_reachable` is the important one and should assert
against a fixture where a supply-side and a demand-side candidate genuinely
describe one service, not a hand-built pair — `both` has never occurred in a real
run, so nothing has ever exercised that merge branch and a mock would only prove
the mock. A guard that no emitted candidate carries an empty `target_repo` is
worth adding alongside, since the 8 empties are the resolver failing silently.

### Suggested tests

```python
KNOWN = frozenset({"group/repo", "group/subgroup"})

def test_module_path_resolves_to_owning_repo():
    assert _canonical_repo_id("group/repo/some-client", KNOWN) == "group/repo"

def test_nested_repo_id_is_not_truncated():
    assert _canonical_repo_id("group/subgroup", KNOWN) == "group/subgroup"

def test_unknown_path_is_not_guessed():
    assert _canonical_repo_id("other/thing/deep", KNOWN) is None

def test_both_is_reachable():
    # supply-side and demand-side candidates for one service must merge
    ...  # assert discovery_method == "both"
```

The last one matters most: `both` has never occurred in a real run, so nothing currently exercises that branch.

---

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

### Why this is a question, not a fix

Two defensible positions, and the right answer depends on intent:

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

### Proposed fix

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

* A deliberately malformed manifest of each supported ecosystem produces a
  **note**, not merely an empty list — one test per parser, since each was
  written separately and each forgot separately.
* An unparsable source file is counted in the run manifest.
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

## 37. The Express inbound-endpoint pattern has no receiver, so any `.get("…")` is an HTTP route  `OI-37`

**Severity:** High — this is a wrong answer, not a missing one, and it is emitted at `confidence: "high"`.
**Status:** open. Found on the first completed fleet-wide trace batch (94 traces), when nearly half the corpus turned out to be drawn from one vendored JavaScript bundle.

### Symptom

Inbound endpoints across a 746-repo fleet, by framework:

| framework | nodes | share |
|---|---|---|
| **express** | **10,245** | **66.5%** |
| jax-rs | 4,671 | 30.3% |
| spring | 456 | 3.0% |
| flask | 14 | 0.1% |
| gin | 8 | 0.1% |
| fastapi | 3 | 0.0% |

Two thirds of every inbound endpoint in a predominantly JVM estate is attributed
to Express. That ratio was the tell.

Resolving each of the 10,245 against its actual source line — reading the file
at the recorded `file:line` and recovering the receiver the regex discarded:

```
express http-in nodes        : 10245
  receiver app/router/server :    20      <-- 0.2%
  distinct receivers         :   353
```

**20 are real routes. 10,225 are not.**

### Root cause

`extractors/patterns.py:382`:

```python
"javascript": [
    (re.compile(r'\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'), "express"),
],
```

The pattern begins at the dot. It matches a method call named after an HTTP verb
taking a string literal — on *any* receiver, anywhere in any JavaScript or
TypeScript file.

Every sibling in `HTTP_IN_RX` is anchored to something that means "route
declaration". Flask requires `@app.route`, FastAPI requires `@router.`, Spring
and JAX-RS require an annotation, Gin at least requires the uppercase verb its
router idiom uses. The JavaScript entry is the only one with no anchor at all,
and JavaScript is the language where `.get(key)` is ubiquitous for reasons that
have nothing to do with HTTP.

What the 10,225 actually are, by receiver:

| what it is | nodes | share |
|---|---|---|
| Angular reactive-form field access — `form.get('quantity')` | 4,391 | 42.9% |
| other non-router receivers | 1,942 | 19.0% |
| unresolved — match spans lines, or the file is minified | 1,537 | 15.0% |
| Cypress selectors — `cy.get('[data-test="…"]')` | 1,104 | 10.8% |
| map / cache / header lookups — `headers.get('…')`, `$templateCache.put('…')` | 646 | 6.3% |
| **outbound** HTTP client calls — `$http.post('/orders')`, `httpClient.get(…)` | 605 | 5.9% |
| genuine Express router | 20 | 0.2% |

Two properties of that table matter more than the headline:

* **The 605 outbound calls are direction-inverted, not merely spurious.** A call
  the service *makes* is recorded as a door into it. That is a wrong edge, and
  it points the wrong way.
* **64% of all matches (6,575) are in test, spec, Cypress or e2e files**, and 5%
  are in vendored, bundled or minified assets. Neither is excluded from inbound
  endpoint extraction.

The worked example is the clearest single case. A vendored copy of a
client-side framework contains its own template cache:

```js
$templateCache.put("template/banner/banner.html", "<div …>");
```

recorded as:

```json
{"family": "http-in", "framework": "express", "confidence": "high",
 "detail": {"method": "PUT", "path": "template/banner/banner.html",
            "raw": ".put(\"template/banner/banner.html\""},
 "file": "…/framework.js", "line": 30444}
```

An inbound `PUT` endpoint, at high confidence, from a cache write in a
third-party library the team does not own.

### Why the confidence is `high`

`extractors/regex_extractors.py:112` hardcodes it:

```python
node = make_node(
    ...
    family="http-in",
    framework=framework,
    detail={"method": method, "path": path, "raw": m.group(0)[:140]},
    confidence="high",
)
```

Every `http-in` node from every language shares one literal. An annotation-anchored
Spring route and a bare `.get("key")` in a minified bundle are indistinguishable
downstream. This is worth fixing independently of the regex: a pattern with no
anchor cannot honestly report `high`, and the value is not derived from anything.

### Impact

`http-in` is not a leaf. It is the entry-point set:

* **It is the trace corpus.** 43 of the 94 traces in the first completed
  fleet-wide batch derive from Express endpoints. A reviewer opening the output
  finds most of it is template-cache keys, Cypress fixtures and form field names.
  The genuine findings are outnumbered roughly two to one, which is the failure
  mode that gets a tool switched off.
* **It is what reachability is computed from.** `OI-21` derives entry points from
  these nodes; anything built on "is this sink reachable from outside" inherits
  10,225 fictitious front doors, 6,575 of them in test code that does not ship.
* **`raw` cannot be used to audit it.** The stored `raw` starts at the dot, so it
  never contains the receiver — all 10,245 look identical in the output. The
  distinction is only recoverable by re-reading the original source, which is
  how the numbers above were obtained. Worth widening `raw` to include the
  receiver regardless of the fix, since it is the field a reviewer would reach
  for first.

This is the `OI-36` shape with the sign flipped: not a path that fails to empty
without a signal, but one that fails to a **confident wrong answer** without a
signal. Nothing in the run manifest suggests that two thirds of the fleet's
inbound endpoints came from one unanchored pattern.

### Proposed fix

Anchor the receiver. The routers are a small, closed, conventional set, and
everything else is noise:

```python
"javascript": [
    # Express/Koa/Fastify route declarations only. The receiver is required:
    # `.get("…")` with no receiver constraint matches reactive-form field
    # access, Cypress selectors, header and cache lookups, and outbound client
    # calls — 10,225 of 10,245 matches in the observed 746-repo fleet.
    (re.compile(
        r'\b(?:app|router|server|api|fastify|_router|[A-Za-z_$][\w$]*[Rr]outer)'
        r'\s*\.\s*(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'
    ), "express"),
],
```

Verified against the fleet: this keeps the 20 genuine routes and drops the
10,225. That is the whole population — there is no middle band to tune.

Two changes worth making alongside, neither of which the regex covers:

1. **Exclude test and vendored trees from inbound-endpoint extraction**, or mark
   the nodes with their provenance. 64% of current matches are in `*.spec.*`,
   `*.test.*`, `*.cy.*`, `__tests__/`, `cypress/` or `e2e/`, and a route declared
   only in a test is not an entry point of the deployed service. If they are kept,
   they should be distinguishable — reachability must be able to exclude them.
2. **Derive `confidence` from how anchored the match was**, rather than hardcoding
   `high`. An annotation or a required receiver earns `high`; a bare pattern
   earns `medium` at best. This is the change that would have surfaced the defect
   without a fleet run.

**Residual, not fixed by the above.** The Gin pattern (`\.(GET|POST|…)\(`) is
unanchored in the same way and relies on Go's uppercase-verb convention to stay
honest. Only 8 nodes in this fleet, so there is no evidence either way — worth a
look when someone is next in that file, not worth a change on its own.

### Suggested tests

```python
def test_express_route_is_detected():
    assert _paths("router.get('/stock', handler)") == ["/stock"]
    assert _paths("app.post('/stock/adjust', handler)") == ["/stock/adjust"]

def test_reactive_form_field_is_not_an_endpoint():
    assert _paths("this.stockAdjustmentForm.get('quantity').setValue(0)") == []

def test_cypress_selector_is_not_an_endpoint():
    assert _paths("cy.get('[data-test=\"stock-total\"]')") == []

def test_template_cache_write_is_not_an_endpoint():
    assert _paths('$templateCache.put("template/banner/banner.html", tpl)') == []

def test_outbound_client_call_is_not_an_inbound_endpoint():
    # direction matters: a call the service makes is not a door into it
    assert _paths("$http.post('/orders', body)") == []

def test_header_and_map_lookups_are_not_endpoints():
    assert _paths("req.headers.get('x-request-id')") == []

def test_confidence_reflects_the_anchor():
    # a bare pattern must not claim the confidence an annotation earns
    assert _node("router.get('/stock', h)").confidence == "high"
```

The direction test is the one to write first. The other cases are noise a
reviewer learns to skip; an outbound call recorded as an inbound endpoint is a
wrong edge that survives into whatever is built on the entry-point set.

A fleet-level guard is also cheap and would have caught this on any run: if one
framework accounts for the overwhelming majority of inbound endpoints in an
estate whose primary languages are something else, say so in the run manifest.
The ratio was the symptom long before anyone read a single node.

---

## 38. Only the build indexes traces, so the trace index never describes the batch that just ran  `OI-38`

**Severity:** Low as a defect, higher as a reporting error — the index states a
coverage figure, and the figure is wrong whenever it is most likely to be read.
**Status:** open. Found on the first completed fleet-wide trace batch: 94 reports
written, no index produced.

### Symptom

`write_traces_index()` is called from exactly one place, `aggregators/graphs.py:82`,
inside `aggregate_graphs_v2()` — the **build's** aggregation phase:

```python
write_index_v2(metabase_root, repo_jsons)
write_pii_flow_v2(metabase_root, repo_jsons)
write_traces_index(metabase_root)          # <-- the only caller
```

Neither entry point that produces traces calls it. `trace_batch.py` ends its run
at `return written, skipped, errors`, and `trace.py` never references the index at
all. So the index is written by the one command that does not create traces, and
not written by the two that do.

The normal workflow is build, then trace. Under that order the index is generated
in the middle — before any trace from this cycle exists — and therefore always
describes the **previous** batch.

Two consequences, in increasing order of how much they matter:

* **After a rebuild-and-retrace, the index is one run stale.** It lists the prior
  set of reports and links to filenames that may no longer exist.
* **On a first run the index never appears at all.** `write_traces_index` opens
  with `if not traces_dir.is_dir(): return 0`, and on a clean metabase the traces
  directory does not exist when aggregation runs. The batch then creates it and
  writes every report. Observed exactly this: a batch wrote 94 reports and left no
  `INDEX.md` behind. Running `write_traces_index()` by hand afterwards indexed all
  94, in under a second.

### Why this is more than cosmetic

The index is not only a list of links. It computes and states catalogue coverage:

```python
md.append(
    f"\n**Catalogue coverage:** {len(traced & catalogue)} / "
    f"{len(catalogue)} endpoints have traces.\n",
)
```

followed by a **Missing traces** table and the instruction to run
`trace_batch.py --skip-existing`. That is the operational signal telling an
operator what is left to do — and it is computed from whichever traces happened to
be on disk during the last aggregation, not from the batch that just finished.

So the one number in the output that says *how complete this is* is stale by
construction, and it is stale in the direction that matters: immediately after a
batch, when someone is checking whether the batch covered everything. An operator
following the file's own advice would re-run `--skip-existing` against a coverage
figure describing a different set of traces.

This is the `OI-36` shape once more, in a milder form: not silence, but a
confident figure derived from inputs that were not the ones the reader believes
they were.

### Proposed fix

Call it where the traces are written. It is idempotent, it takes a single
argument the batch already has, and it globs a directory that has just been
written — sub-second on 94 reports:

```python
    written, skipped, errors = batch_trace(
        metabase_root,
        limit=args.limit,
        skip_existing=args.skip_existing,
        scan_repos=repos_root,
    )
    # The index reports catalogue coverage, so it must be written by whatever
    # last changed the trace set — not by the build, which runs before any
    # trace exists and therefore always describes the previous batch (OI-38).
    write_traces_index(metabase_root)
    print(
        f"Batch trace done: wrote={written} skipped={skipped} errors={errors}",
    )
    return 1 if errors and not written else 0
```

Do the same in `trace.py`, so a single re-traced endpoint does not leave the
coverage figure describing a set it is no longer part of.

**Keep the call in `aggregate_graphs_v2` as well.** It costs nothing when the
directory is absent — the early `return 0` handles that — and it keeps the index
consistent when a rebuild changes the catalogue without any trace being re-run.
The two calls are complements, not duplicates: the build call refreshes coverage
when the *catalogue* moves, the batch call refreshes it when the *traces* move.
Today only the first exists, which is why the figure drifts.

### Suggested tests

```python
def test_batch_writes_its_own_index(tmp_metabase):
    # a batch run on a metabase with no traces directory must leave an index
    run_batch(tmp_metabase, limit=2)
    index = tmp_metabase / "graphs" / "traces" / "INDEX.md"
    assert index.is_file()
    assert "2 reports" in index.read_text()

def test_index_counts_the_batch_that_just_ran(tmp_metabase):
    run_batch(tmp_metabase, limit=1)
    run_batch(tmp_metabase, limit=3)          # index must not still say 1
    assert "3 reports" in (tmp_metabase / "graphs" / "traces" / "INDEX.md").read_text()

def test_coverage_figure_matches_traces_on_disk(tmp_metabase):
    # the stated coverage must be derived from the current trace set
    run_batch(tmp_metabase, limit=0)
    text = (tmp_metabase / "graphs" / "traces" / "INDEX.md").read_text()
    traced = len(list((tmp_metabase / "graphs" / "traces").glob("*.md"))) - 1  # minus INDEX
    assert f"{traced} / " in text

def test_single_trace_refreshes_the_index(tmp_metabase):
    ...
```

The second is the one that fails today and the one worth writing first: it
reproduces the drift without needing a clean metabase, and it fails for the right
reason rather than on the absence of a file.

