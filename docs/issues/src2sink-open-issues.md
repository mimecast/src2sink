# src2sink 1.1.0 — Open Detection Issues and Proposed Fixes

**Version reviewed:** src2sink 1.1.0
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
* **Designing the next thing.** `OI-16` surfaced while writing
  [`docs/plans/metabase-versioning-design.md`](../plans/metabase-versioning-design.md),
  from asking what a cache key must contain. It had been live through every
  release in this cycle.

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
| OI-13 | 13 | Kotlin call sites invisible to the AST pass | medium | a supported language silently loses its SQL sinks | **P1** |
| OI-15 | 15 | The whole fleet is held in memory, so a large metabase cannot be read at all | large | decides whether the tool works at 34 GB and above | **P1** |
| OI-16 | 16 | A detection fix never reaches a repo that has not changed | small | every detection fix silently fails to land fleet-wide | **P0** |

Every issue from the original review is fixed and recorded in
[`src2sink-closed-issues.md`](src2sink-closed-issues.md), with the ordering
rationale preserved alongside each one. Both entries above were found later and
by different means — `OI-13` while raising test coverage, `OI-15` while
profiling — rather than while investigating a report. That is the argument for
the coverage and measurement work paying for itself.

`OI-15` is P1 despite nobody having hit it, because it is a ceiling rather than
a slowdown and the work to lift it is large. Finding out at 34 GB that the
answer is a redesign is a much worse position than knowing now.

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

## 13. Kotlin call sites are invisible to the AST pass  `OI-13`

**Severity:** High — a whole supported language silently loses its SQL sinks.

**Found:** while raising `extractors/ast_walk.py` coverage for WI-12; asserted there rather than fixed, because the fix changes detection output.

### Symptom

The same query, in the two languages the JVM half of the fleet is written in:

```java
// Java
class StockDao { List<Stock> find(long id) {
    return jdbcTemplate.query("SELECT ref FROM stock WHERE id = ?", mapper, id); } }
```

```kotlin
// Kotlin — identical call, identical receiver, identical SQL
class StockDao { fun find(id: Long): List<Stock> {
    return jdbcTemplate.query("SELECT ref FROM stock WHERE id = ?", mapper, id) } }
```

Measured:

```
java    -> ['data-class-field/source', 'sql/sink']
kotlin  -> ['data-class-field/source']
```

The Java file yields a `sql` execution sink. The Kotlin file yields none — and reports no error, no warning and no note.

### Root cause

`CALL_NODE_TYPES` correctly names Kotlin's call nodes:

```python
"kotlin": frozenset({"call_expression", "navigation_expression"}),
```

but `extract_call_name` routes Kotlin to the **Java** walker:

```python
if language in ("java", "kotlin"):
    return call_name_java_kotlin(source, node)
```

and that walker's first line rejects everything Kotlin produces:

```python
if node.type != "method_invocation":      # a Java grammar type
    return None
```

`method_invocation` does not exist in the Kotlin grammar — it uses `call_expression` wrapping a `navigation_expression`. So `iter_calls` finds the nodes, asks for their names, receives `None` for every one, and yields nothing. The two halves of the dispatch disagree and neither says so.

### What is lost

Everything downstream of the AST pass, for Kotlin only:

* **`sql` sinks** — no execution sinks, so no SQL family from the AST tier at all;
* **`raw-code-payload`** — requires `ctx.sql_execution_sinks`, which is always empty, so a Kotlin endpoint accepting a `sql` field and executing it is never correlated;
* **`script-exec`** — `eval`/`exec`/`compile` call sites are never seen;
* **receiver evidence** (`OI-7`) — with no call sites there is nothing to gate, so Kotlin SQL is found only when a *regex* tier happens to match a literal.

Kotlin is a first-class target: `tree-sitter-kotlin` is one of the eight runtime dependencies and `.kt` files are scanned like any other. This is not an unsupported language degrading gracefully; it is a supported one failing silently.

### Proposed fix

A Kotlin-specific walker, `call_name_kotlin`, reading the grammar's own shapes: a `call_expression` whose callee is a `simple_identifier` (a bare call) or a `navigation_expression` (a call on a receiver, where the name is the last `navigation_suffix`). `extract_call_receiver` needs the matching branch — the receiver is the navigation expression's first child rather than an `object` field.

The same dispatch bug is worth checking for elsewhere: any language whose entry in `CALL_NODE_TYPES` names node types the corresponding walker does not accept has this defect. A test that asserts *every* language in `CALL_NODE_TYPES` yields at least one named call for a representative source would catch the whole class, and is cheaper than auditing each walker by hand.

### Suggested tests

* The Kotlin fixture above yields a `sql` sink with `receiver == "jdbcTemplate"`, matching the Java result.
* A bare Kotlin call (`execute(x)`) yields a name and no receiver.
* A Kotlin endpoint with a `sql` field plus an execution sink yields `raw-code-payload`, as the Java equivalent does.
* Every language in `CALL_NODE_TYPES` names at least one call in a representative snippet — the class-wide guard.
* `tests/test_ast_walk.py::test_kotlin_calls_are_not_named_by_the_java_walker` asserts the *current* behaviour and must be replaced, not deleted: it is what would otherwise let this regress quietly.

### Residual not covered

Kotlin's regex tiers already work, so string-built SQL in Kotlin is detected today; this issue is only about the AST pass. Scala and other JVM languages are not scanned at all and are out of scope.

---

## 15. The whole fleet is held in memory, so a large metabase cannot be read at all  `OI-15`

**Severity:** High — this is a ceiling, not a slowdown. Past it the tool does not
run slowly; it does not run.

### Symptom

None yet, on the fleets scanned so far. This issue is a projection from measured
numbers rather than a report, which is why it is written down before anyone hits
it: the failure mode when it arrives is an out-of-memory kill with no partial
result and nothing to bisect.

### Root cause

`load_v2_repo_records` returns a `list` holding every repo record, and the
aggregators are written against that list. Deserialised JSON is much larger than
its text: measured over a 300-repo synthetic metabase, 1.9 MB on disk became
12.5 MB resident, an expansion of **6.5x**. Extrapolating:

| metabase on disk | resident, just to hold it |
|---|---|
| 1 GB | ~7 GB |
| 34 GB | ~222 GB |
| 500 GB | ~3.2 TB |

The expansion factor will vary with record shape — a fleet of many small nodes
costs more per byte than one with few large ones — but no plausible factor makes
34 GB fit on a machine anyone runs this on.

This is independent of `OI-14`. That was about doing fleet-wide work repeatedly;
this is about the fleet not fitting at all, and it is not helped by caching or by
removing the quadratic scan, because both require holding the fleet first.

### Why it is not urgent yet, and what would make it so

At the scale scanned today the fleet fits comfortably and `OI-14` removed the
cost that was actually being felt. The trigger to act is a metabase in the tens
of GB, or a requirement to run on a memory-constrained host.

### Proposed fix

Stop holding the fleet. Three changes, in dependency order:

1. **Stream records.** `load_v2_repo_records` becomes a generator, and each
   aggregator either consumes it once or declares what it needs to retain. This
   is the invasive part: several aggregators iterate `records` more than once,
   and each such site has to be made single-pass or explicitly re-open the
   stream.
2. **Persist the fleet indices as build artefacts.** The build phase already
   walks every repo, so it is the natural place to emit the inbound-route index,
   the repo-alias index and the service-call edge list. SQLite is the obvious
   store: single file, standard library, indexed lookups, no server.
3. **Make `trace` query rather than load.** For one target a trace needs the
   edges arriving at it plus that repo's own record — one indexed query and one
   file read, with no fleet-wide structure in memory at any point.

Step 2 also removes the quadratic scan `OI-14` left in place: a route index built
once and keyed by significant segments turns each lookup into a hash hit rather
than a scan over every route in the fleet.

### Suggested tests

* A metabase whose records are larger than a deliberately small memory budget
  still traces, demonstrating streaming rather than loading. Assert peak RSS via
  `resource.getrusage`, with a generous bound — the point is the shape of the
  curve, not a number.
* Peak memory for a trace is flat as the fleet grows, where today it is linear.
  Two fleet sizes and a ratio assertion is enough; an absolute threshold would
  be machine-dependent and flaky.
* The persisted index and a freshly-computed one produce identical edges, so the
  artefact cannot drift from the code that reads it.
* A stale index — one built from a metabase that has since changed — is detected
  rather than silently trusted.

### Residual not covered

The build phase itself still has to hold whatever the extractors need per repo.
This issue is about the *aggregate* structures; a single pathological repository
large enough to exhaust memory on its own is `TA-001`'s territory, not this one.

---

## 16. A detection fix never reaches a repo that has not changed  `OI-16`

**Severity:** High — every detection fix this project has shipped is affected,
and the failure is silent in both directions: the stale finding looks current,
and the fix looks applied.

**Found:** while designing metabase versioning
([`docs/plans/metabase-versioning-design.md`](../plans/metabase-versioning-design.md)),
not by anyone reporting a wrong result — which is the point.

### Symptom

Measured. A repo containing `httpClient.execute(req)`, with a prior record
holding the false `sql` sink that `OI-7` removed, scanned by a build that
*contains* the `OI-7` fix:

```
scan result: {'_skipped': True, 'group': 'grp', 'name': 'svc'}
nodes still on disk: [('sql', 'execute')]
analysed_at still: 2025-01-01T00:00:00+00:00
record names the tool that made it: False
```

The false sink survives the fix that removed it, indefinitely, for as long as the
repository does not happen to commit.

### Root cause

`build_metabase_v2.py:409-412` skips re-analysis when the repo's current git sha
matches the sha in the existing JSON:

```python
if not force:
    current_sha = detect_git_sha(repo_root)
    if current_sha and current_sha == _read_existing_sha(json_path):
        return {"_skipped": True, "group": group, "name": name}
```

The skip is keyed on **what was scanned** and not at all on **what scanned it**.
A record's content is a function of both, so the cache key is missing half its
inputs. Nothing detects this afterwards, because the record does not record which
version of `src2sink` produced it — `tool_version` exists only in
`run-manifest.json`, describing the *run*, not the contents.

`schema_version` is not a substitute. It is checked on load, so a schema bump does
force a rebuild — but every detection fix so far (`OI-1`, `OI-2`, `OI-7`..`OI-12`)
changed extraction output *within* schema 2 and therefore did not.

### What is lost

* **Detection fixes do not land fleet-wide.** The improvements recorded against
  each closed issue describe the repos that were rescanned, not the fleet.
* **Detector semantics mix silently within one metabase.** A record written
  before `OI-10` carries `parameterised: false`; one written after carries
  `parameterised: "mixed"`. Aggregations run across both without noticing.
* **The manifest misleads.** It stamps the current `tool_version` over a fleet
  that was mostly produced by earlier versions.

This is the cross-cutting shape §6 already names — a detection input that
resolves to nothing without saying so. Here the input is "the detector that
produced this record", and it resolves to unknown.

### Proposed fix

1. Record the detector identity on every repo record — a `detection_version`
   field, distinct from the package version so that a docs-only release does not
   invalidate the fleet.
2. Include it in the skip key: skip only when the sha **and** the detection
   version both match.
3. Treat a record with no `detection_version` as stale, since we genuinely cannot
   know what produced it. This forces one full rescan on upgrade, which should be
   announced rather than discovered.
4. Gate the version in CI, in the same family as the existing ratchets: fail the
   build when anything under `src2sink/extractors/` (or the pattern, vocabulary
   and binding inputs) changes without a `detection_version` bump. Without the
   gate this fix degrades to "remember to bump it", which is the failure mode it
   exists to remove.

See §6 of the design document for why a hand-maintained version plus a gate is
preferred to hashing the extractor sources.

### Suggested tests

* A repo whose sha is unchanged but whose record carries an older
  `detection_version` **is** rescanned.
* A repo whose sha and detection version both match is skipped — the existing
  incremental behaviour must survive.
* A record with no `detection_version` is treated as stale.
* The CI gate fires on an extractor change with no bump, and does not fire on a
  change elsewhere. This is the test that matters: the gate is the fix.
* Regression: a record holding a pre-`OI-7` false `sql` sink is replaced, not
  preserved, by a build containing the fix — the exact scenario measured above.

### Residual not covered

Version-skew between a consumer and the provider it pins is a different problem
with a different fix; see §7 of the design document. This issue is only about the
metabase drifting from the *tool*.
