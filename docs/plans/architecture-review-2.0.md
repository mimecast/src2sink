# Architecture review — src2sink 2.0.0

**Purpose:** establish what the codebase actually looks like before committing to
the 3.0 work (persisted index, versioning, intra-repo reachability), so that plan
is built on measurements rather than impressions.

**Method:** every claim below is measured against the 2.0.0 tree. Where a finding
warrants tracked work it is filed as an `OI-n` and cross-referenced; where it is
a judgement about shape it says so.

**Verdict up front:** the codebase is in good order — gated, typed, tested, and
documented well beyond what its size would suggest. The findings are about
*shape*, and they matter now only because 3.0 will pull hard on exactly the seams
that are weakest: the aggregation/rendering boundary, the record-at-a-time data
model, and module-level mutable configuration.

---

## 1. What is strong

Worth stating plainly, because the rest of this document is criticism and would
otherwise read as a worse picture than the truth.

| | |
|---|---|
| Tests | 626 passing, **86.6%** overall, per-module floors enforced |
| Types | `mypy --strict` clean across 79 files |
| Mutation | 56 curated mutants, all killed, now covering `scripts/` too |
| Complexity | cyclomatic + cognitive ratchet, 52 hot spots frozen |
| Traceability | SRTM, 18 requirements with evidence |
| Detector identity | fingerprint gate (`OI-16`) |
| Imports | acyclic, module-level, `PLC0415` enforced |
| Docs | architecture, threat model, SLSA, SAST report, schema, releasing |

Coverage is even across layers, which is unusual and means the tests are not
concentrated where they were easy to write:

```
  extractors    714 stmts   91.5%
  aggregators  1877 stmts   87.3%
  core         2058 stmts   83.7%
  renderers      58 stmts   96.6%
  models         62 stmts   96.8%
```

The gate design in particular is the codebase's best feature: each gate exists
because something specific went wrong, and each fails *loudly* rather than
degrading. That discipline is why the defects found this cycle were found at all.

---

## 2. Finding A — aggregation and rendering are fused

**Measured:** 14 of 32 aggregator modules import `renderers.markdown`; 16 write
files directly; 23 functions are side-effect-only (`write_*`, `aggregate_*`,
`generate_*` returning `None`).

`renderers.markdown` is the **second most imported** module in the package, after
`graph_common`.

**Why it matters now.** An aggregator that computes *and* writes Markdown cannot
be reused to write SQLite rows or Parquet. The 3.0 persisted-index work needs the
computation without the rendering, and today they are the same function. This is
the single biggest structural obstacle to `OI-15`, and it is not visible from the
issue text.

**Shape of the fix.** Split each aggregator into a pure `compute_*` returning
data and a thin `render_*`/`write_*` at the edge. This can be done incrementally,
aggregator by aggregator, and each split is independently testable — a pure
function is easier to test than the file-writing one it replaces, so the change
pays for itself in test clarity rather than costing it.

**Not filed as an issue** because it produces no wrong output today; it is
refactoring work that belongs *inside* the 3.0 plan as a prerequisite phase.

---

## 3. Finding B — the data model is record-at-a-time, and everything assumes it

**Measured:** `graph_common` is the most-imported module (19 importers), and its
`load_v2_repo_records` returns a `list` of every record. Deserialised JSON
expands **6.5×** over its on-disk size.

Already tracked as `OI-15`. Recorded here because the review confirms the blast
radius: the assumption is not local to one function, it is the shape of the
package's most-depended-upon module. Any streaming change touches 19 importers.

**Implication for sequencing:** `OI-15` cannot be done as a leaf change. It needs
Finding A resolved first, or the aggregators will have to be touched twice.

---

## 4. Finding C — module-level mutable configuration

**Measured:** 10 module-level globals reconfigured at runtime, across 7 modules:

```
build_metabase_v2   _MAX_FILES_PER_REPO, _MAX_FILE_BYTES
extractors.http_out _BINDING_CLASS_CACHE
internal_groups     INTERNAL_GROUP_PATTERNS
known_api_clients   _BINDINGS, _ALIAS_MATCHER_CACHE
prescreen           _INDICATORS, _MAX_LINE_BYTES
repo_utils          _component_identity_index_cache, _repo_artifact_index_cache
```

Four are caches (fine, and two now invalidate correctly). The other six are
**configuration**, set once per process and read everywhere.

**What it has already cost.** `tests/conftest.py` carries a fixture whose
docstring records the incident: a test that configured bindings and did not
restore them changed the *output* of every test after it, and a committed
extractor snapshot came to depend on collection order. The fixture exists because
the state is global.

**Why it matters now.** The scan runs in a worker pool, so this configuration is
already propagated by hand through `_worker_init`. Adding per-repo-version
context (3.0) means either extending that hand-propagation again or passing an
explicit config object. The second is the answer; the first is how you get an
eighth global.

**Shape of the fix.** A frozen `ScanConfig` dataclass threaded through
extraction, replacing the globals. `known_api_clients`'s registry is the natural
first candidate — the recent push/pull inversion already removed its worst
consequence.

---

## 5. Finding D — `build_metabase_v2.py` does five jobs

**Measured:** 953 lines, the largest module. It contains CLI argument parsing,
worker-pool orchestration, Gradle/Maven dependency parsing, record serialisation
with redaction, the incremental-skip logic, and run-manifest writing.

Dependency parsing in particular (`_parse_gradle_deps`, `_parse_version_catalog`,
`_CATALOG_*` regexes — roughly 120 lines) has nothing to do with orchestration
and belongs with the other manifest parsing in `repo_utils`.

**Not urgent.** It is well tested and the complexity ratchet holds it in place.
Worth splitting when 3.0 touches the build path anyway, not before — a
gratuitous move would churn the mutation catalogue's line anchors for no gain.

---

## 6. Finding E — `FlowEdge` declares more than it delivers

**Measured:** `FlowEdge.kind` documents `intra-file | intra-repo | cross-repo`.
Only `intra-file` is ever emitted — one `make_edge` call in the entire package.
Cross-repo relationships live in a *separate* structure (`CallEdge`, in the
aggregators) that is not a `FlowEdge` at all.

So the schema advertises a graph the extractor never builds, and the graph that
does exist is modelled twice, differently.

This is the structural face of `OI-17`. The fix is not to delete the unused
values but to make them real — and to decide whether `CallEdge` and `FlowEdge`
should converge, which the 3.0 plan has to answer since both land in the
persisted index.

---

## 7. Finding F — `Any` concentrated at the boundaries

**Measured:** `limits.py` (10), `trace.py` (4), `service_call_collect.py` (3),
`ropa.py` (3), `api_client_discovery.py` (3).

`limits.py` is a generic execution bulkhead, so `Any` there is honest. The others
are mostly `dict[str, Any]` standing in for a record or a node — the same
untyped-record problem as Finding B, seen from the type system.

**Worth noting:** a `TypedDict` for the record and node shapes would make
`mypy --strict` catch schema drift that currently only tests catch, and 3.0 is
introducing new fields (`detection_version` landed in 2.0; scope and edges come
next). Cheap to do at the same time.

---

## 8. What this review did *not* find

Stated explicitly, because a review that only lists problems is not calibrated:

* **No import cycles.** Verified structurally, and now gated.
* **No dead modules.** Every module in the package is reachable from an entry
  point.
* **No untested security-critical path.** Coverage floors are per-module and the
  security modules carry the highest ones.
* **No copyleft dependency.** Runtime closure is 8 packages, MIT/PSFL.
* **No unbounded regex on an untrusted path.** ReDoS bounds are gated
  (`TA-005`), and the one that escaped was caught by the harvest check.
* **No silent-failure path of the kind §6 of the open-issues document
  describes**, other than those already filed.

---

## 9. Findings, ranked, and where they land

| | Finding | Severity | Disposition |
|---|---|---|---|
| A | Aggregation fused with rendering | High | Prerequisite phase in the 3.0 plan |
| B | Record-at-a-time data model | High | `OI-15`, sequenced after A |
| C | Module-level mutable configuration | Medium | Phase in the 3.0 plan |
| E | `FlowEdge` declares an unbuilt graph | Medium | Part of `OI-17` |
| D | `build_metabase_v2.py` does five jobs | Low | Opportunistic, when 3.0 touches it |
| F | `Any` at record boundaries | Low | Fold into the schema work |

**The single most important conclusion:** `OI-15` and `OI-17` both look like leaf
features and neither is. `OI-15` is blocked behind Finding A, and `OI-17` is
blocked behind Finding E and the node-scope schema change. A plan that sequences
them as independent deliverables will discover this halfway through — which is
why the unified plan opens with the refactoring phase rather than the features.
