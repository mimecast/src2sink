# Fix plan — src2sink 1.1.0 open detection issues

**Input:** `docs/issues/src2sink-open-issues.md` (issues `OI-1`–`OI-9`)
**Target release:** 1.2.0 (Phase 1), 1.3.0 (Phase 2)
**Method:** strict TDD (red → green → refactor) per work item, with a regression
suite and a mutation-testing gate that proves the new tests actually constrain
the new code.

* **Phase 1 (§1–§8, WI-1 to WI-9)** — fix the known defects.
* **Phase 2 (§9, WI-10 to WI-15)** — ask the same questions of the code that was
  not changed: mutation coverage for the rest of the tool, docstrings, coverage
  floors, complexity gates, an architecture review, and the documentation set.
  Separable; nothing in it blocks Phase 1.

---

## 0. Findings that change the plan

Three of the issue document's premises do not match the code as it stands on
`fix/cross-repo-caller-detection`. Each changes what the fix has to be, so they
are settled here rather than discovered mid-implementation.

### F1 — the proposed `path_templates_match` silently deletes prefix matching (§1)

The replacement function in §1 keeps only *equality* and *suffix* relations. Run
against the current corpus of cases it regresses two shapes that match today and
are legitimate:

| outbound | inbound | 1.1.0 | §1 proposal | verdict |
|---|---|---|---|---|
| `/queries/{handle}` | `/queries` | medium | **None** | regression — asserted by `tests/test_phase2_graphs.py:15` |
| `/stock/dispatch` | `/stock` | medium | **None** | regression — a child route of a declared endpoint |

A caller hitting `/queries/{handle}` against a service that declares `/queries`
is the single most common real match; dropping it would trade §1's precision win
for a large recall loss. The plan therefore uses an **amended** algorithm that
keeps prefix matching but evaluates it over *significant* segments only. As a
bonus it closes the residual §1 declares out of scope:

| outbound | inbound | 1.1.0 | amended |
|---|---|---|---|
| `/v1/stock` | `/stock` | low | **medium** |
| `/v1/stock` | `/v1` | medium | **None** |
| `/api/v2/pallets` | `/pallets` | low | **medium** |
| `/api/queries` | `/api` | medium | **None** |
| `/queries/{handle}` | `/queries` | medium | medium |
| `/stock/dispatch` | `/stock` | medium | medium |
| `/v1/orders/{id}` | `/orders` | None | **medium** |
| `/v1/reservations` | `/reservations/{ref}` | None | **medium** ← §1's stated residual |
| `/orders/{id}/lines` | `/lines` | low | low |
| `/api` | `/api/v1/queries` | medium | **None** |
| `/stock` | `/stock` | high | high |
| `/v1/stock` | `/v1/reservations` | None | None |

### F2 — §1 has a blast radius outside the graphs: `trace --path`

`path_templates_match` is not only the graph matcher. `trace.py` uses it as the
predicate for the **user-supplied `--path` filter** at `trace.py:75`, `:157`,
`:174` and `:368`. Under the amended rule `path_templates_match("/v1/stock",
"/v1")` is `None`, so `src2sink-trace --path /v1` would silently filter away
every result it matches today. Filter semantics ("show me everything under this
prefix") and routing semantics ("do these two routes denote the same endpoint")
are different questions and must stop sharing one function.

**Decision:** add `path_filter_matches(candidate, filter)` to `graph_common`,
retaining today's prefix/generic-segment-tolerant behaviour, and repoint the four
`trace.py` call sites at it. `path_templates_match` becomes routing-only.

### F3 — §2b's ordering premise is inverted

§2b states "`extract_path_constants` already runs before `extract_http_outbound`
in `extractors/unified.py`, so `ctx.nodes` is populated by the time the guard is
evaluated." The actual order (`extractors/unified.py:52-53`) is the reverse:

```python
extract_http_outbound(ctx)     # line 52
extract_path_constants(ctx)    # line 53
```

So `any(n.family == "path-constant" for n in ctx.nodes)` is always `False` at
guard time and option 2b as written is a no-op. Two ways out:

* **swap the two passes** — cheap, but `unified.py:47` warns that "order only
  matters for raw-code-payload prerequisites", which must be verified rather
  than assumed; or
* **derive the evidence directly** from `ctx.source` with the same route-like
  constant predicate `extract_path_constants` uses, leaving pass order alone.

**Decision:** the second. The guard becomes a pure function of the source text,
which makes it independently testable, order-independent, and immune to a future
pass reshuffle. `_is_route_like_constant` and `PATH_CONST_RX` /`PATH_ENUM_RX` are
already in `regex_extractors.py` and get reused, so no new vocabulary is
introduced — the evidence-based property §2b asks for is preserved.

---

## 0.1 Review findings OI-7 to OI-9 (measured)

Three further defects, outside the issue document. Every output below is real
`extract_from_file` output on this branch, not a reconstruction.

### OI-7 — the `sql` family matches on method name alone

`extractors/patterns.py:9-10` puts `execute`, `query` and `update` in
`SQL_SINK_NAMES`, and `ts_extractors.py:17` tests `name in SQL_SINK_NAMES` with
no reference to the receiver. `call_name_java_kotlin` (`ast_walk.py:32`) returns
only the method name — the receiver is available on the AST node's `object` field
but is discarded before the decision is made:

```
httpClient.execute(request)     -> sql sink, confidence=high, execution=True, parameterised=False
messageDigest.update(data)      -> sql sink, confidence=high, execution=True, parameterised=False
call.execute()                  -> sql sink, confidence=high, execution=True, parameterised=False
```

`parameterised` is `"?" in call_text or ":" in call_text` (`ts_extractors.py:35`),
so a call with no SQL in it at all is reported as an **unparameterised** SQL sink.

**This is worse than a noisy family.** `execution=True` puts the node into
`ctx.sql_execution_sinks`, which is one of the three inputs to
`link_raw_code_payload_endpoints`. A plain HTTP proxy that happens to have a field
named `sql` therefore manufactures a **`raw-code-payload` node at `high`
confidence**:

```java
@RestController
public class Proxy {
    private String sql;
    @PostMapping("/forward")
    public Response forward(@RequestBody Req req) throws Exception {
        return httpClient.execute(req.toHttpRequest());   // not SQL
    }
}
```
```
http-in          source high
sql              sink   high   {'symbol': 'execute', 'execution': True, 'parameterised': False}
raw-code-payload source high   {'endpoint_path': '/forward', 'sink_symbol': 'execute'}
EDGE intra-file: sql payload field (line 4) on /forward → execute (line 7)
```

That fabricated finding propagates into `taint/raw-code-payload-endpoints.jsonl`,
the `trace_batch` reports, and the index counts — the tool's highest-value output.

### OI-8 — SQL built by formatting produces no node at all

`SQL_SOURCE_RX` (`patterns.py:36-41`) has four patterns: concatenation in either
direction, Python f-strings, and `${...}` templates. Measured:

| Construct | Result |
|---|---|
| `String.format("SELECT * FROM users WHERE name = '%s'", user)` | **no sql node** |
| Kotlin `"SELECT * FROM users WHERE id = $id"` | **no sql node** |
| Python `"SELECT … '%s'" % user` | **no sql node** |
| Python `"SELECT … '{}'".format(user)` | **no sql node** |
| `"SELECT * FROM t WHERE n = " + u` | sql source, `concatenated` ✓ |
| `"SELECT * FROM t WHERE n = '" + u + "'"` | **no sql node** |

Two distinct bugs, and the last row is the one to notice:

1. **No format-function coverage.** `String.format`, `.formatted`,
   `MessageFormat.format`, Python `%` and `.format` are all absent.
2. **The existing concatenation patterns break on an embedded quote.** Their
   literal body is `[^"\']*` — excluding *both* quote characters — so a
   double-quoted literal containing `'` cannot be spanned. Since
   `WHERE name = '" + user + "'` is the canonical injection shape, the pattern
   misses precisely the cases it exists for.
3. The `${...}` template pattern is `\$\{[^}]+\}.*(?:SELECT|…)` — it requires the
   interpolation to appear **before** the SQL keyword. Real templates interpolate
   after it (`"SELECT … WHERE id = ${id}"`), so it rarely fires.

A confirmed injection from a report producing no SQL node is consistent with all
three.

### OI-9 — an outbound request carrying a SQL payload has no home family

`raw-code-payload` is the *inbound* concept: `link_raw_code_payload_endpoints`
requires `ctx.http_sources` (an `http-in`), so it only ever fires on the service
that *receives* SQL. The dual — this repo *sending* SQL to another service — has
no representation. Measured on a realistic forwarder:

```java
public class QueryForwarder {
    private static final String SUBMIT_URL = "/v1/query";
    public Result submit(String sqlText) {
        QueryRequest body = new QueryRequest();
        body.setSql(sqlText);
        return restTemplate.postForObject(SUBMIT_URL, body, Result.class);
    }
}
```
```
http-out       sink      high   {'path': '/v1/query', …}
path-constant  reference medium
data-class-field source  medium {'field_name': 'query'}
```

An ordinary HTTP call and nothing more. Note also that `body.setSql(sqlText)`
contributed no `sql` field marker — the field-name passes did not recognise the
setter form, which the fix has to handle.

The vocabulary this needs already exists: `RAW_SQL_PAYLOAD_FIELD_NAMES`
(`vocabulary.py:130`, the strict set) and the per-binding `payload_fields` in
`api-clients.json` (default `("sql",)`, `known_api_clients.py:32`).

---

## 1. Sequencing

Priority follows the issue doc's §5, with the P0 first because it is the only one
producing *wrong* output.

| Step | Work item | Issue | Branch |
|---|---|---|---|
| WI-1 | Significant-segment path matching + filter split | §1 | `fix/path-match-significance` |
| WI-2 | Specificity tie-break in `match_path_in_inbound_index` | §1 companion | same branch as WI-1 |
| WI-3 | Gradle version catalogs | §3 | `fix/gradle-version-catalogs` |
| WI-4 | Route-constant evidence for the HTTP file guard | §2 | `fix/http-guard-evidence` |
| WI-5 | Demand-side api-client discovery | §4 | `feat/demand-side-discovery` |
| WI-6 | Empty-input notes (cross-cutting §6) | §3/§6 | folded into WI-3 and WI-4 |
| WI-10–15 | Phase 2 — hardening the existing surface (§9) | — | separable, ships as 1.3.0 |
| **WI-7** | **Gate the sql family on receiver / SQL evidence** | **OI-7** | `fix/sql-sink-receiver-gate` |
| **WI-8** | **SQL sources via format / template / concatenation** | **OI-8** | `fix/sql-source-formatting` |
| **WI-9** | **`sql-payload-out` family** | **OI-9** | `feat/sql-payload-out` |

WI-1 and WI-2 ship together: the tie-break is only meaningful once the confidence
ladder is corrected. WI-5 is the only one of the issue-doc items that is a feature
rather than a defect fix and is the natural cut line if the release has to be
trimmed.

**WI-7 to WI-9 were added to the issue document after this plan was drafted**, as
§7–§9 / `OI-7`–`OI-9`. §0.1 gives the measured evidence for each. **WI-7 outranks everything else in this plan, including §1**: it is the only
item that fabricates *high-confidence security findings*, and §5's own argument —
"a confidently wrong edge is worse than a missing one" — applies with more force
to a fabricated injection endpoint than to a wrong service edge. Recommended
order: WI-7, WI-1+2, WI-8, WI-3, WI-4, WI-9, WI-5.

Cite issues by their stable `OI-n` id everywhere — test docstrings, commits, code
comments. Section numbers do not survive an issue's move to
`docs/issues/src2sink-closed-issues.md` when it is fixed.

Each work item is one commit (or a small stack): failing tests first, then the
implementation that turns them green, then the mutants added to the catalogue.

---

## 2. TDD protocol

For every work item, in order, no step skipped:

1. **Red.** Write the tests from the "Red tests" list below. Run
   `uv run pytest <file> -q --no-cov` and paste the failure into the commit body
   of the test-only commit. A test that passes before the implementation is not a
   test of the change and must be rewritten or deleted.
2. **Characterise.** Run the full suite and record which *existing* tests the
   intended behaviour change breaks. Every such break is either (a) an
   intentional behaviour change — update the assertion in the same commit with a
   comment naming the issue section, or (b) an unintended regression — the
   implementation is wrong. There is no third option and no bulk baseline
   regeneration.
3. **Green.** Implement the smallest change that passes. No opportunistic
   refactoring in the same commit.
4. **Regress.** Add the regression tests (§4) that pin the *reported symptom*,
   not just the unit behaviour.
5. **Mutate.** Two steps, in order (§5). First *discover*: run the scoped
   `uvx mutmut@3.7.0` sweep over the module this work item changed and read the
   survivors — each one is a missing assertion. Then *fix in stone*: write the
   test that kills it, and transcribe the mutant into the catalogue. Run
   `make mutation`; the catalogue must be 100% killed before moving on.
6. **Gates.** `make ci` must be green (`lint typecheck test srtm bandit audit`).

### Constraints the tests must respect

* `mypy --strict` covers `src2sink/` and `scripts/` — every new helper needs full
  annotations, including the new mutation script.
* Coverage floors: project ≥80% (`pyproject` addopts), and ≥90% for
  `limits`/`safe_paths`/`sanitize`/`prescreen` (`tests/test_zz_security_coverage_gate.py`).
  None of this work touches those four, but the project floor must not drop.
* **Do not invent `TA-xxx` labels.** `scripts/srtm_check.py` fails if a test
  mentions a `TA-xxx` that is not in the SRTM in
  `docs/security-privacy-gap-analysis.md` §8. These are correctness fixes, not
  security requirements, so new tests carry no TA label — except the ReDoS
  bounds additions below, which extend the existing TA-005 test module and need
  no new id.
* Every new regex added to a module-level pattern table must be **length-bounded**
  and reachable from `tests/test_redos_bounds.py`. That test collects patterns
  from module tables; the new catalog regexes live in `build_metabase_v2` and
  must be added to its import list alongside `_GRADLE_DEP_RX`.
* Fixtures stay sanitised — invented repo/package/class names only, per the
  header of `tests/test_cross_repo_caller_coverage.py`. The warehouse example
  from the issue doc is already fictitious and can be reused verbatim.
* Tests stay single-process, ≤4 fixture repos, under the 15s autouse watchdog.

---

## 3. Work items

### WI-1 — significant-segment path matching (§1, P0)

**Files:** `src2sink/graph_common.py`, `src2sink/trace.py`

**Implementation**

```python
_VERSION_SEGMENT_RX = re.compile(r"^v\d+$", re.I)
_GENERIC_SEGMENTS = frozenset({"api", "rest", "internal", "public", "service", "services"})

def _significant(parts: list[str]) -> list[str]: ...

def path_templates_match(outbound: str, inbound: str) -> str | None:
    #  exact normalised equality                       -> "high"
    #  significant-segment equality                    -> "medium"
    #  either side a significant *prefix* of the other -> "medium"   (child/parent route)
    #  either side a significant *suffix* of the other -> "low"      (tail overlap only)
    #  a side that reduces to no significant segments  -> None
```

The `None` for an empty significant side is the actual defect fix: a bare `/v1`
or `/api` names a version, not a destination, so it must match nothing rather
than beat everything.

Split out `path_filter_matches(candidate, path_filter)` (F2) preserving the 1.1.0
prefix behaviour, and repoint `trace.py:75/157/174/368`.

**Red tests** — `tests/test_graph_common.py`

* `test_path_templates_match_ignores_version_prefix` — the full 12-row table in
  F1, parameterised, asserting the exact confidence label (not just truthiness).
* `test_path_templates_match_rejects_bare_version_or_generic_segment` —
  `/v1/stock` vs `/v1`, `/api/queries` vs `/api`, `/api` vs `/api/v1/queries`,
  `/service` vs `/service/orders` all `None`.
* `test_path_templates_match_is_symmetric_in_relation` — for every pair, swapping
  the arguments yields a label of the same rank (the relation is direction-free;
  today's implementation is asymmetric by accident).
* `test_path_filter_matches_keeps_prefix_semantics` — `path_filter_matches("/v1/stock", "/v1")`
  is `True` while `path_templates_match` of the same pair is `None`.
* `tests/test_trace_render.py::test_trace_path_filter_matches_version_prefix` —
  a trace with `--path /v1` still returns the `/v1/stock` hit. This is the F2
  guard and it must fail before the split exists.

**Existing tests that change (step 2 of the protocol)**

* `tests/test_graph_common.py:27` — `/api/queries` vs `/api`: `medium` → `None`.
* `tests/test_graph_common.py:28` — `/api/v1/queries` vs `/queries`: `low` → `medium`.
* `tests/test_phase2_graphs.py:15` — `/queries/{handle}` vs `/queries`: stays
  `medium` under the amended algorithm. If this one breaks, the implementation
  drifted toward the §1 proposal as written; fix the implementation, not the test.
* `tests/test_cross_repo_caller_coverage.py:443` — asserts a `/api/v1/queries`
  match; the rows should be unchanged but the confidence rises to `medium`.

### WI-2 — specificity tie-break (§1 companion)

**File:** `src2sink/graph_common.py` (`match_path_in_inbound_index`)

Within the winning confidence group, prefer the candidate matching the most
significant segments; only genuinely equal-specificity candidates are returned
together. Ordering must be deterministic — break remaining ties on
`(target_repo, inbound_path)` so output is stable across dict iteration.

**Red tests** — `tests/test_graph_common.py`

* `test_match_path_prefers_more_specific_route` — index holds `/stock` and
  `/stock/dispatch`; outbound `/v1/stock/dispatch` returns only the
  `/stock/dispatch` row.
* `test_match_path_returns_equally_specific_candidates_together` — two repos both
  declaring `/stock` both come back.
* `test_match_path_is_order_independent` — build the same index from two
  different insertion orders; assert identical results.
* `test_match_path_memo_agrees_with_uncached` — extend the existing memo test at
  `tests/test_cross_repo_caller_coverage.py:452` to the tie-break cases.

**Regression test** — the reported symptom, end to end:
`tests/test_cross_repo_caller_coverage.py::test_version_prefix_does_not_outrank_route_name`.
Build three fictitious repos exposing bare `/v1` plus one exposing `/stock`, a
consumer with `STOCK_SUBMIT_URL = "/v1/stock"`, run `collect_service_edges`, and
assert exactly one edge, to the `/stock` service, at `medium`. This is the §1
symptom verbatim and it is the test that would have caught the defect.

### WI-3 — Gradle version catalogs (§3)

**File:** `src2sink/build_metabase_v2.py`

Add `_parse_version_catalog(repo_root)` (TOML `[libraries]` + `settings.gradle.kts`
`library(...)` DSL), `_normalise_alias`, and `_CATALOG_REF_RX`; resolve
`libs.<alias>` references found in `build.gradle*` and feed the resolved
`(groupId, artifactId)` through the existing `is_internal_coordinate`
classification so internal/external tagging is unchanged.

Deviations from the §3 sketch, all mandatory:

* Bound every quantifier — the sketch's `\{[^}]*module` is unbounded. Use
  `[^}\n]{0,200}`, and register the new patterns in `tests/test_redos_bounds.py`.
* Read through `safe_read_text` (size cap) and skip via `is_skipped_path`, as
  `_collect_dependencies` already does for `pom.xml`/`build.gradle`.
* Restrict the TOML glob to `**/gradle/libs.versions.toml` plus `**/*.versions.toml`,
  and cap the number of catalog files parsed per repo, so a monorepo cannot turn
  this into an unbounded scan.
* **WI-6 note (§6):** when a `build.gradle*` contains `libs.` references and the
  resolved catalog covers none of them, append a repo note —
  `"gradle version catalog unresolved: N libs.* references, no catalog found"`.
  A count of zero is a finding.

**Red tests** — `tests/test_build_internals.py`

* `test_parse_version_catalog_toml` / `test_parse_version_catalog_settings_dsl`.
* `test_normalise_alias_is_case_and_separator_insensitive` —
  `warehouse-service-client`, `warehouse.service.client`, `warehouseServiceClient`
  all collapse to one key.
* `test_collect_dependencies_resolves_libs_reference` — a tmp repo with
  `gradle/libs.versions.toml` + `build.gradle.kts` using `implementation(libs.warehouseServiceClient)`
  yields one internal dependency with the right coordinate.
* `test_collect_dependencies_notes_unresolved_catalog_reference` — `libs.` used,
  no catalog: dependency list empty **and** the note present.
* `test_version_catalog_does_not_change_inline_coordinate_parsing` — a
  build file with both forms yields both deps, de-duplicated.

**Regression test** — `tests/test_phase4_regression.py`: add a fixture repo
`tests/fixtures/synthetic-repos/fulfilment/catalog-consumer/` with the catalog +
build script and assert `dependencies_internal` is non-empty after a full
`analyse_repo_v2`. Being non-empty is the whole point: §3's symptom is a silent
zero.

### WI-4 — route-constant evidence for the HTTP file guard (§2)

**File:** `src2sink/extractors/http_out.py`, `src2sink/extractors/regex_extractors.py`

Per F3, implement 2a **and** an evidence-based 2b:

* **2a** — extend `_JAVA_HTTP_FILE_RX` with `MediaType|HttpStatus|Authorization|Bearer`
  and a bounded quoted-route-literal alternative; extend `_PY_HTTP_FILE_RX`
  equivalently.
* **2b** — `file_has_route_constant(source) -> bool`, built on the existing
  `PATH_CONST_RX`/`PATH_ENUM_RX` + `_is_route_like_constant` predicate. In
  `extract_http_outbound`, the context tier fires when
  `file_guard.search(ctx.source) or file_has_route_constant(ctx.source)`.
  Compute it **once per file**, lazily, not once per pattern.
* **2c** — documentation only: `docs/api-clients-json.md` gains a short section
  stating that a known in-house wrapper should be declared as a binding
  `class_patterns` entry, which runs in the unguarded tier.

Widening a guard raises false positives by construction, so the negative tests
below are as important as the positive one.

**Red tests** — `tests/test_cross_repo_caller_coverage.py`

* `test_custom_wrapper_with_route_constant_yields_http_out` — §2's suggested
  test, verbatim: a Java file containing only `STOCK_SUBMIT_URL = "/v1/stock"`,
  `client.post(STOCK_SUBMIT_URL, request, StockSubmitResponse.class)`, and no
  HTTP-library identifier yields **exactly one** `http-out` node, with
  `detail["path"] == "/v1/stock"` resolved through the symbol table.
* `test_guard_still_rejects_non_http_client_calls` — a file with
  `cacheClient.get(key)` and no route constant yields zero `http-out` nodes.
* `test_route_constant_alone_does_not_emit_http_out` — a constants-only file
  (route constant, no call site) yields zero `http-out` nodes. Guards gate; they
  do not emit.
* `test_file_path_constant_is_not_route_evidence` — `TEMPLATE = "/config/app.yml"`
  does not satisfy the guard (`_FILE_PATH_RX` already excludes it).
* `test_guard_evidence_is_order_independent` — call `extract_http_outbound`
  standalone on a fresh context, with `ctx.nodes` empty, and assert the node is
  still produced. This is the F3 guard: it fails against a `ctx.nodes`-based
  implementation and passes against a source-based one.
* `tests/test_phase1_extractors.py` — one Python-side equivalent for the widened
  `_PY_HTTP_FILE_RX`.

**Regression test** — extend `tests/test_phase4_extractor_snapshots.py` with a
snapshot for a `fulfilment/wrapper-caller` fixture, so the exact node/detail
shape for the hand-rolled wrapper is pinned rather than merely counted. Also
re-run the **negative** fixture (`synthetic-repos/negative/safe-crud`) and assert
its `http-out` count is unchanged — the guard widening must not leak into it.

### WI-5 — demand-side api-client discovery (§4)

**Files:** `src2sink/aggregators/api_client_discovery.py` (+ a new
`_demand_side.py` if the module exceeds ~350 lines)

Sequential, after the supply-side pass, exactly as §4 specifies: for each entry
in the unmatched/weakly-matched call-site set, resolve `target_repo`, then enrich
the existing `(target_repo, artifact)` candidate or create a
`(target_repo, "<hand-rolled>")` one with empty `maven_artifact`/`import_prefix`
and `status: pending`.

Both safeguards from §4 are in scope and non-optional:

* **Distinctiveness.** A proposed `class_patterns` entry is checked for
  corpus-wide occurrence; more than `MAX_PATTERN_REPOS = 3` repos and the
  proposal is flagged in `warnings` (not silently kept). Class patterns run in
  the unguarded, language-agnostic tier at `extractors/regex_extractors.py:257-259`,
  so a generic proposal manufactures fleet-wide phantom edges.
* **Self-confirmation.** Nodes whose `detail.target_repo_evidence` shows the
  target was stamped by a binding are excluded from the demand-side input set.
  Confidence must not compound across runs.

`discovery_method` is recorded as `"dependency" | "call-site" | "both"`, and
`_confidence` sorts `both` above `dependency` above `call-site`.

**Red tests** — `tests/test_api_client_discovery.py`

* `test_demand_side_proposes_class_pattern_from_enclosing_class`.
* `test_demand_side_enriches_existing_supply_side_candidate` — unions paths and
  aliases, sets `discovery_method == "both"`, does not duplicate the candidate.
* `test_demand_side_creates_hand_rolled_candidate_when_no_dependency`.
* `test_generic_class_pattern_is_flagged_not_accepted` — a `Client` proposal
  appearing in 4 fixture repos produces a `warnings` entry naming the count.
* `test_binding_stamped_nodes_are_excluded_from_demand_side_input` — a node with
  `target_repo_evidence: "api-client class …"` contributes nothing; run discovery
  twice and assert the candidate's confidence and evidence are byte-identical
  (the anti-self-confirmation property, stated as idempotence).
* `test_reviewer_edits_survive_demand_side_regeneration` — an `accepted`
  candidate with a hand-tuned `class_patterns` keeps it (extends the existing
  `_TUNABLE_FIELDS` preservation behaviour to the new pass).

**Regression metric** (§4's "why this closes a loop"): a fleet-shaped fixture
test asserting that accepting a demand-side candidate strictly reduces the row
count in `graphs/service-call-unmatched.jsonl`. Monotone-down is the assertion;
zero is not required.

### WI-7 — gate the sql family on receiver or SQL evidence (OI-7, P0)

**Files:** `src2sink/extractors/ast_walk.py`, `ts_extractors.py`, `patterns.py`

The name-only test must become an evidence test. Surface the receiver first —
`method_invocation` carries an `object` field in the Java/Kotlin grammar and
`attribute` carries one in Python, so this is a discard, not a limitation:

```python
def call_receiver(source: bytes, node: Node, language: str) -> str | None: ...
```

A bare `SQL_SINK_NAMES` hit then becomes a `sql` node only when at least one
positive signal holds:

* **(a) receiver vocabulary** — `jdbcTemplate`, `entityManager`, `em`, `session`,
  `sqlSession`, `cursor`, `conn`/`connection`, `stmt`/`statement`/`preparedStatement`,
  `db`, `dao`, `repository`, `tx`. Case-insensitive, matched on the trailing
  identifier so `this.userDao` and `readOnlyJdbcTemplate` both hit;
* **(b) call-text hint** — the existing `SQL_EXECUTION_CALL_HINTS`, unchanged;
* **(c) file-level SQL evidence** — a **SQL keyword inside a string literal**
  (`SELECT|INSERT|UPDATE|DELETE|MERGE|UPSERT|CREATE TABLE`) **or** a DB
  import/include (`java.sql`, `javax.sql`, `jakarta.persistence`,
  `org.springframework.jdbc`, `org.hibernate`, `mybatis`, `sqlalchemy`,
  `psycopg`, `pymysql`, `sqlite3`, `database/sql`, `gorm`).

**(c) must be SQL text or a DB import — never the mere token `sql`.** The OI-7
proxy fixture has a field named `sql` and no SQL anywhere; a looser (c) would
re-admit exactly the case this work item exists to kill. This is the single most
important constraint in WI-7 and it gets its own test.

Also fix `parameterised`. `"?" in call_text or ":" in call_text` is not a
placeholder test; it is a substring test that reports "unparameterised" for calls
containing no SQL. Make it tri-state: `True`/`False` only when a SQL literal is
actually in scope, otherwise `"unknown"`. Half the reported symptom is the word
*unparameterised* appearing next to a hash update.

**Red tests** — `tests/test_phase1_extractors.py`

* `test_http_client_execute_is_not_a_sql_sink` — the three OI-7 call sites, in a
  file with no SQL evidence, yield zero `sql` nodes.
* `test_jdbc_template_query_is_still_a_sql_sink` — `jdbcTemplate.query(SQL, …)`
  still yields one, `execution=True`, `confidence=high`. The recall guard.
* `test_bare_execute_with_sql_literal_in_file_is_a_sql_sink` — signal (c) via a
  `SELECT` literal.
* `test_bare_execute_with_jdbc_import_is_a_sql_sink` — signal (c) via import.
* `test_sql_field_name_alone_is_not_sql_evidence` — the OI-7 proxy verbatim:
  zero `sql` nodes **and** zero `raw-code-payload` nodes. The anti-regression for
  the whole defect.
* `test_parameterised_is_unknown_without_a_sql_literal`.
* `tests/test_characterization.py` — one Python (`cursor.execute`) and one Go
  (`db.Query`) case, so the gate is not silently Java-only.

**Regression test** — `tests/test_phase4_extractor_snapshots.py`: a
`proxy/http-forwarder` fixture whose snapshot asserts the *absence* of `sql` and
`raw-code-payload`. Absence is the assertion, so it must be a snapshot of the
whole node list, not a lookup.

**Blast radius** — the largest in this plan. `sql` counts drop fleet-wide;
`raw-code-payload` counts drop; `taint_buckets`, `index_v2`, `renderers/markdown`
and the `fleet-family-baseline.json` all move. Expect the baseline diff to be
large and **review it as findings-removed, sampling ~20 dropped nodes to confirm
each was genuinely not SQL**. A large green diff here is the deliverable, but only
if someone has looked at it. If the `detail` shape changes (new `receiver`,
tri-state `parameterised`), `SCHEMA.md` changes with it.

### WI-8 — SQL sources via format, template and concatenation (OI-8, P1)

**File:** `src2sink/extractors/patterns.py` (`SQL_SOURCE_RX`)

Three fixes, of which the second is the one that matters most:

1. **Add format-function patterns** — `String.format`, `.formatted(`,
   `MessageFormat.format`, Python `%` and `.format(`, all requiring a SQL keyword
   inside the format string.
2. **Repair the concatenation patterns' literal body.** Replace `[^"\']*` with
   per-delimiter alternatives — `"(?:[^"\\\\\\n]{0,400})"` and
   `'(?:[^'\\\\\\n]{0,400})'` — so a double-quoted literal may contain `'`. Add
   `"SELECT … WHERE n = '" + u + "'"` as an explicit test case; it is the
   canonical injection shape and it is missed today.
3. **Fix the template pattern's ordering.** Match a SQL keyword and an
   interpolation (`${…}`, `$ident`, `{}`, `%s`) **in either order** within one
   literal, instead of requiring the interpolation first.

Confidence: `high` when a SQL keyword co-occurs with an interpolation *of a
variable*; `medium` for the existing shapes. Every pattern stays length-bounded
and joins `tests/test_redos_bounds.py`.

**Red tests** — `tests/test_phase1_extractors.py`, parameterised over the OI-8
table so each row is a named case:

* `test_string_format_sql_is_a_source` (Java), `test_formatted_sql_is_a_source`.
* `test_python_percent_and_format_sql_are_sources`.
* `test_kotlin_template_sql_is_a_source` — interpolation *after* the keyword.
* `test_concatenation_with_embedded_quote_is_a_source` — the OI-8 last row.
* `test_non_sql_format_is_not_a_source` — `String.format("Hello %s", name)`
  yields nothing. Widening a source pattern needs its negative.
* `test_sql_keyword_in_a_comment_is_not_a_source` — `// SELECT is faster than …`.

**Regression test** — a `dao/format-injection` fixture reproducing the confirmed
injection from the report (sanitised), asserting one `sql` source node with the
right `pattern` label, plus its snapshot.

### WI-9 — `sql-payload-out` family (OI-9, P2)

**Files:** `extractors/regex_extractors.py` + a new `link_sql_payload_out(ctx)` in
`ts_extractors.py`, then the aggregation chain.

A new cross-pass linker mirroring `link_raw_code_payload_endpoints`, but on the
outbound side: an `http-out` node in the file **and** a SQL-payload field bound
into that request. Field detection must cover the forms OI-9 showed are missed —
setters (`setSql(`), builder calls (`.sql(`), assignment (`.sql =`, `sql:`), and
JSON keys (`"sql":`) — not just declarations.

Vocabulary is reused, not invented: `RAW_SQL_PAYLOAD_FIELD_NAMES` (strict set)
∪ the `payload_fields` of the binding that stamped the `http-out` node, when one
did. Confidence `high` when a binding's own `payload_fields` matched (the binding
declares that this service takes SQL over the wire); `medium` on vocabulary alone.

```
family:     sql-payload-out
kind:       sink
data_class: raw-sql-payload
detail:     {field_name, http_out_line, path, target_repo, client, evidence}
```

**A new family is not just an extractor change.** It must be threaded through
`taint_buckets.py` (`FAMILY_TO_BUCKET`), `taint_writers.py`, `index_v2.py`,
`renderers/markdown.py`, `build_metabase_v2.py` family counts, `trace.py`, and
`SCHEMA.md`. That plumbing, not the detection, is the bulk of the work — and is
why this is P2 despite being high value.

**Red tests** — `tests/test_phase1_extractors.py` + `tests/test_phase4_aggregators.py`

* `test_sql_payload_out_from_setter` — the OI-9 forwarder verbatim yields one
  `sql-payload-out` node referencing the `http-out` line and `/v1/query`.
* `test_sql_payload_out_from_binding_payload_fields` — a binding declaring
  `payload_fields: ["dql"]` promotes a `dql` field to `high`.
* `test_http_out_without_sql_payload_yields_no_node` — an ordinary POST is
  untouched. The precision guard.
* `test_sql_payload_out_requires_an_http_out_in_the_same_file` — a data class with
  a `sql` field and no outbound call yields nothing.
* `test_sql_payload_out_appears_in_taint_catalogue` — the family reaches the
  aggregated output, not just `ctx.nodes`. This is the test that catches
  half-finished plumbing.

**Relationship to the existing families** — state it in `SCHEMA.md`:
`raw-code-payload` is *"this service accepts SQL"*; `sql-payload-out` is *"this
service sends SQL"*. Together they are the two ends of one cross-repo hop, and
joining them across repos in the aggregation phase is the obvious follow-on — out
of scope here, and worth an explicit note so it is not re-derived later.

---

## 4. Regression test strategy

Three layers, deliberately distinct:

1. **Unit** — the confidence tables, alias normalisation, and guard predicates.
   Parameterised, exact-value assertions. These are what the mutation gate scores
   against.
2. **Symptom** — one test per issue section, reproducing the *reported* failure
   using the issue doc's own fictitious warehouse example, asserting the final
   observable (an edge, a dependency, a node) rather than an intermediate. Named
   `test_<symptom>_regression` and carrying a docstring line referencing
   the issue's stable `OI-n` id so the link survives both refactoring and the
   issue's eventual move to `docs/issues/src2sink-closed-issues.md`. Never cite a
   section number — those do not survive the move.
3. **Snapshot** — `tests/fixtures/extractor-snapshots/` for WI-4 and the
   `fleet-family-baseline.json` / `regression-baseline.json` for WI-1's fleet
   effect. Baselines are regenerated **once**, in the same commit as the change,
   with the diff reviewed line by line and summarised in the commit body. A
   baseline regenerated without a reviewed diff is a deleted test.

New fixture repos (all fictitious, following the existing sanitised set):

```
tests/fixtures/synthetic-repos/
  commerce/warehouse-service/      # exposes POST /stock            (WI-1)
  pricing/price-index/             # exposes bare /v1               (WI-1, the decoy)
  fulfilment/catalog-consumer/     # gradle version catalog         (WI-3)
  fulfilment/wrapper-caller/       # hand-rolled ApiClient wrapper  (WI-4)
```

---

## 5. Mutation testing

Tests that pass are not evidence that they *constrain* anything. Every fix here
is a boundary/branch change — exactly the class a passing-but-vacuous test hides.
So each work item lands with mutants that its tests must kill.

### Two tools, two jobs

Discovery and regression are different questions and the same tool is bad at
both. The split:

| | Question | Tool | When | Gated |
|---|---|---|---|---|
| **Discovery** | "what gaps did I not think of?" | `mutmut`, scoped | dev time, per work item | no |
| **Regression** | "do the gaps I closed stay closed?" | curated catalogue, `scripts/mutation_check.py` | `make ci` + CI | **yes** |

The ratchet runs one way: mutmut surfaces a survivor → a test is written that
kills it → the mutant is transcribed into the catalogue as a permanent
deterministic entry → mutmut is never needed for that defect again. The durable
artifact of a mutation run is **the assertion it caused**, not the run.

A generated sweep cannot do the regression job: mutant ids (`x_significant__mutmut_4`)
regenerate on every edit, so a rename yields "survivors" unrelated to the change,
CI reddens for a non-defect, the allowlist churns, and the gate degrades into
rubber-stamping. A curated catalogue cannot do the discovery job: it contains
only mutants someone imagined — and the `p.lower()` → `p.upper()` survivor found
in the 3.14 trial was not one of them.

### Discovery — mutmut, dev time only

Verified working on Python 3.14.2 (this corrects an earlier draft of this plan
which assumed it would not be):

| Claim | Verified |
|---|---|
| mutmut rewrites source with `parso` | **False** — mutmut 3.x uses `libcst` (1.9.0, cp314 wheel present) |
| 3.14 support is unproven | **Installs and runs.** 21 mutants on a toy of the WI-1 fix: 19 killed, 2 survived, ~478 mutations/s |
| `libcst` may not parse this codebase | Parses `src2sink/graph_common.py` clean |

Run ad hoc as **`uvx mutmut@3.7.0 run`**, scoped via `source_paths` to the module
the work item touches. Deliberately **not** added to the dev dependency group: the
project audits its lockfile (`pip-audit --frozen`, SC-1, `docs/slsa.md`), and
adding `libcst` + `textual` + `rich` + `click` to the audited surface for a tool
run a handful of times per release is real cost for no gated benefit. Keeping it
out also neutralises every operational problem below, since none of them then sit
on a gated path.

Operational notes for whoever runs it (each reproduced, not theoretical):

* **It does not fail on survivors.** `mutmut run` exits 0; so does
  `mutmut results`, while listing survived mutants. Read the output; do not wrap
  it in `&&`.
* **pytest `addopts` collide.** `--cov=src2sink --cov-fail-under=80` is inherited
  by mutmut's pytest invocations and the floor is not met under a scoped
  selection, so mutmut aborts at the stats stage (`failed to collect stats.
  runner returned 1`). Run it with coverage disabled.
* **Config API is in flux** (Beta; 3.7.0 published 2026-07-31).
  `paths_to_mutate`/`tests_dir` are already deprecated for
  `source_paths`/`pytest_add_cli_args_test_selection`. Pin the exact version in
  the command.
* **It forks its runners** (`DeprecationWarning: multi-threaded … fork()`), while
  `tests/conftest.py` installs an autouse SIGALRM watchdog that no-ops off the
  main thread. Confirm the watchdog still fires before trusting a "killed"
  verdict on a hanging mutant.

*Optional:* a **non-blocking** weekly mutmut sweep on the existing `ci.yml`
schedule (Mondays 06:17 UTC, already there for `pip-audit`) catches catalogue rot
at zero per-PR cost. It reports; it never fails the build.

### Regression — the curated catalogue, `scripts/mutation_check.py`

* A catalogue of explicit mutants: `(id, file, old_snippet, new_snippet,
  test_selector, note)`. Exact, unique snippets.
* Per mutant: copy `src2sink/` + `tests/` + `pyproject.toml` to a tmpdir, apply
  the one substitution, run
  `uv run pytest <test_selector> -q --no-cov -p no:cacheprovider -x` there.
* **Killed** = non-zero exit. **Survived** = zero exit → gate fails, naming the
  mutant id, its note, and the selector that failed to notice.
* A snippet that no longer matches is a **hard error**, not a skip — a refactor of
  the target line should force a human to restate which defect the mutant
  represented and confirm a test still catches it. The error must print the
  mutant id, its note, and the snippet it could not find; a bare "snippet not
  found" just gets the entry deleted.
* Per-mutant timeout; `--only <id>` for iteration; `--summary $GITHUB_STEP_SUMMARY`
  matching `srtm_check.py`.

**Cost — measured on this repo, not estimated:** one targeted test file runs in
**0.8s** wall (mostly interpreter startup); the full suite is 16s. 22 mutants ×
one test file ≈ **~20s**. That is affordable in the local loop, so `make mutation`
goes **into the `make ci` chain**, with a `mutation` CI job alongside `srtm`. (An
earlier draft of this plan put it outside `make ci` on a "minutes, not seconds"
assumption; that figure belongs to the generated sweep — ~1,670 lines across the
four scoped modules is order 800–1,200 mutants, 10–16 min single-threaded — which
is exactly why the sweep is dev-time and the catalogue is the gate.)

**Gate: 100% of the catalogue killed.** No percentage threshold — the catalogue is
curated, so every entry is a defect someone chose to care about; killing 20 of 22
means two defects are undetectable and the number tells you nothing about which
two. Percentages are for generated sets, where equivalent mutants make 100%
unreachable by construction. Correspondingly, there is **no equivalent-mutant
allowlist here** — an entry that turns out to be equivalent was miscurated and is
deleted with a note, not suppressed.

### Catalogue (initial)

Populated from the work items below, then extended with whatever the mutmut
discovery runs surface. Entries are appended, never trimmed.

| id | Target | Mutation | Must be killed by |
|---|---|---|---|
| M-01 | `_significant` | drop the version-segment filter | `test_path_templates_match_rejects_bare_version_or_generic_segment` |
| M-02 | `_significant` | drop the generic-segment filter | same |
| M-03 | `_VERSION_SEGMENT_RX` | `^v\d+$` → `^v\d*$` (now matches a literal `v`) | version-table test |
| M-04 | `path_templates_match` | empty-significant-side `return None` → `return "low"` | `test_..._rejects_bare_version_or_generic_segment` |
| M-05 | `path_templates_match` | significant-equality returns `"high"` instead of `"medium"` | the confidence table (§1 is explicit that this must not be `high`) |
| M-06 | `path_templates_match` | prefix branch returns `None` (the §1-as-written regression) | `test_path_templates_match_ignores_version_prefix` rows for `/queries/{handle}`, `/stock/dispatch` |
| M-07 | `path_templates_match` | suffix branch returns `"medium"` not `"low"` | confidence table |
| M-08 | `match_path_in_inbound_index` | tie-break comparison `>` → `>=` | `test_match_path_prefers_more_specific_route` |
| M-09 | `match_path_in_inbound_index` | specificity key → constant (tie-break disabled) | same |
| M-10 | `match_path_in_inbound_index` | drop the deterministic secondary sort | `test_match_path_is_order_independent` |
| M-11 | `path_filter_matches` | delegate back to `path_templates_match` | `test_trace_path_filter_matches_version_prefix` (F2) |
| M-12 | `_normalise_alias` | drop `.lower()` | `test_normalise_alias_is_case_and_separator_insensitive` |
| M-13 | `_normalise_alias` | drop the `-` replacement | same |
| M-14 | `_CATALOG_TOML_RX` | require `version` in the match | `test_parse_version_catalog_toml` |
| M-15 | `_collect_dependencies` | skip the catalog resolution step | `test_collect_dependencies_resolves_libs_reference` |
| M-16 | catalog note emission | note append removed | `test_collect_dependencies_notes_unresolved_catalog_reference` |
| M-17 | guard predicate | `guard.search(...) or has_route_constant(...)` → `and` | `test_custom_wrapper_with_route_constant_yields_http_out` |
| M-18 | guard predicate | → unconditional `True` | `test_guard_still_rejects_non_http_client_calls` |
| M-19 | `file_has_route_constant` | drop the `_is_route_like_constant` filter | `test_file_path_constant_is_not_route_evidence` |
| M-20 | `MAX_PATTERN_REPOS` | `3` → `1000` | `test_generic_class_pattern_is_flagged_not_accepted` |
| M-21 | demand-side input filter | binding-stamped exclusion removed | `test_binding_stamped_nodes_are_excluded_from_demand_side_input` |
| M-22 | `discovery_method` | always `"both"` | the `discovery_method` assertions |
| M-23 | `_maybe_add_sql_sink` | evidence check `and` → `or` (any one signal admits) | `test_http_client_execute_is_not_a_sql_sink` |
| M-24 | `_maybe_add_sql_sink` | evidence check removed entirely (1.1.0 behaviour) | `test_sql_field_name_alone_is_not_sql_evidence` |
| M-25 | file-evidence signal (c) | SQL-literal test → bare `"sql" in source` | `test_sql_field_name_alone_is_not_sql_evidence` |
| M-26 | receiver vocabulary | emptied to `frozenset()` | `test_jdbc_template_query_is_still_a_sql_sink` |
| M-27 | `is_execution` | forced `True` | `test_http_client_execute_is_not_a_sql_sink` (via `raw-code-payload` absence) |
| M-28 | `parameterised` | tri-state collapsed back to `False` | `test_parameterised_is_unknown_without_a_sql_literal` |
| M-29 | concat literal body | reverted to `[^"\']*` | `test_concatenation_with_embedded_quote_is_a_source` |
| M-30 | template pattern | ordering constraint reintroduced | `test_kotlin_template_sql_is_a_source` |
| M-31 | format patterns | SQL-keyword requirement dropped | `test_non_sql_format_is_not_a_source` |
| M-32 | `link_sql_payload_out` | `http-out`-in-file precondition removed | `test_sql_payload_out_requires_an_http_out_in_the_same_file` |
| M-33 | `link_sql_payload_out` | field vocabulary → any field name | `test_http_out_without_sql_payload_yields_no_node` |
| M-34 | `sql-payload-out` confidence | always `high` | `test_sql_payload_out_from_binding_payload_fields` |
| M-35 | `FAMILY_TO_BUCKET` | `sql-payload-out` entry dropped | `test_sql_payload_out_appears_in_taint_catalogue` |

M-24, M-25 and M-32 are the ones to get right: each restores a specific
false-positive path that a fixture proved live.

M-06 and M-18 are the two that matter most: M-06 is the F1 regression, and M-18
proves the widened guard is still a guard.

Each work item adds its own rows before it is considered done; the catalogue is
appended to, never trimmed.

---

## 6. Definition of done

Per work item:

- [ ] Failing tests committed before the implementation, failure output in the commit body.
- [ ] Every existing test the change breaks is individually reviewed and updated with a comment naming the issue section.
- [ ] Symptom-level regression test present, citing the issue's stable `OI-n` id.
- [ ] Mutants added; `make mutation` kills 100% of the catalogue.
- [ ] `make ci` green; coverage floors held.
- [ ] Baselines/snapshots regenerated only with a reviewed, summarised diff.
- [ ] **WI-7 only:** ~20 dropped `sql`/`raw-code-payload` nodes sampled from the
      baseline diff and individually confirmed not to be SQL; the sample recorded
      in the commit body. A large green diff is the deliverable only once someone
      has actually looked at it.

Per release:

- [ ] **Each fixed issue moved** out of `docs/issues/src2sink-open-issues.md` and
      into `docs/issues/src2sink-closed-issues.md`, following the procedure in
      that file's header: section moved verbatim, `### Resolution` block prepended
      (fixed-in version, commit sha, guarding test ids, what changed, deviation
      from the proposed fix, behaviour change), index row added, open-issues §5
      priority row removed. This is a **follow-up commit** — the fix's own sha is
      not knowable inside the fix commit — normally the release-prep commit,
      closing several issues at once.
- [ ] The deviations recorded for OI-1 in particular: the amended matching
      algorithm (F1) and the `path_filter_matches` split (F2) both differ from
      what the issue proposed, and OI-2's fix differs from §2b's premise (F3).
      The `Deviation from the proposed fix` field exists for exactly these.
- [ ] `CHANGELOG.md` 1.2.0 entry, calling out both **behaviour changes**: edges
      that used to resolve via a bare `/v1`/`/api` prefix disappear (`trace --path`
      filtering is unchanged, F2), and `sql` / `raw-code-payload` counts drop
      fleet-wide as false positives are withdrawn (WI-7). The second will look
      like lost coverage on a dashboard unless the entry says otherwise.
- [ ] Version bumped in `pyproject.toml`; `docs/releasing.md` steps followed.
- [ ] `README.md` / `docs/api-clients-json.md` updated for 2c and for the
      demand-side discovery output.
- [ ] `SCHEMA.md` updated for the `sql` detail changes (WI-7: `receiver`,
      tri-state `parameterised`) and the `sql-payload-out` family (WI-9),
      including how it relates to `raw-code-payload`.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| WI-1 removes real edges that happened to match via a `/v1` prefix | The fleet baseline diff is reviewed edge by edge before regeneration; the amended algorithm adds two new match shapes (F1 table) that should offset most of the loss. |
| `trace --path` behaviour silently changes | F2's dedicated filter function plus `test_trace_path_filter_matches_version_prefix`. |
| WI-4's widened guard raises false positives fleet-wide | Three negative tests plus an unchanged-count assertion on the existing negative fixture; 2b is evidence-based (a route constant in the same file), not vocabulary-based. |
| WI-3's catalog globbing turns into an unbounded scan on a monorepo | `is_skipped_path` + `safe_read_text` size cap + a per-repo catalog file cap. |
| New regexes introduce catastrophic backtracking | Bounded quantifiers, registered in `tests/test_redos_bounds.py` (TA-005). |
| WI-5 inflates its own confidence over successive runs | Provenance exclusion (§4) asserted as run-to-run idempotence. |
| The mutation gate becomes slow enough to be disabled | Selector-scoped runs — one mutant runs one test file (0.8s), not the suite; measured ~20s for the whole catalogue. The expensive generated sweep is dev-time only and never gates. |
| The catalogue ossifies — it only ever contains defects already thought of | mutmut discovery runs per work item feed it, plus the optional non-blocking weekly sweep. |
| WI-7 removes real SQL sinks along with the false ones | A recall guard per language (`jdbcTemplate`, `cursor.execute`, `db.Query`) plus a sampled manual review of ~20 dropped nodes in the baseline diff. Signal (c) is deliberately generous — any SQL literal or DB import in the file admits the call. |
| WI-8's widened source patterns raise false positives | Each new pattern ships with its negative test (`String.format("Hello %s", …)`, SQL keyword in a comment); all patterns bounded and in TA-005. |
| WI-9's new family is added to the extractor but not the aggregation chain | `test_sql_payload_out_appears_in_taint_catalogue` asserts end-to-end reach, not `ctx.nodes`. |
| WI-7's baseline diff is large enough that it gets rubber-stamped | Diff reviewed as *findings removed*, with the sample recorded in the commit body; this is called out in the definition of done. |

---

## 8. Effort

| Work item | Tests | Implementation | Total |
|---|---|---|---|
| WI-7 | ~0.5 d | ~0.5 d + ~0.5 d baseline review | 1.5 d |
| WI-1 + WI-2 | ~0.5 d | ~0.5 d | 1 d |
| WI-8 | ~0.5 d | ~0.5 d | 1 d |
| WI-3 | ~0.5 d | ~0.5 d | 1 d |
| WI-4 | ~0.5 d | ~0.5 d | 1 d |
| Mutation harness | — | ~0.5 d | 0.5 d |
| WI-9 | ~0.5 d | ~1.5 d (mostly family plumbing) | 2 d |
| WI-5 | ~1 d | ~1.5 d | 2.5 d |

WI-7, WI-1+2, WI-8, WI-3, WI-4 plus the harness is ~6 days and closes every
defect — both the wrong-output ones and the missed-detection ones. WI-9 and WI-5
are capability additions and can ship separately as 1.3.0 without weakening the
rest.

If the release has to be cut hard, **WI-7 alone is worth shipping**: it is the
only item that removes fabricated high-confidence security findings, and it is a
day and a half.

---

## 9. Phase 2 — hardening the existing surface (WI-10 to WI-15)

Phase 1 (WI-1 to WI-9) fixes known defects. Phase 2 asks the same questions of the
code that was *not* changed. It is separable and ships as 1.3.0; nothing in it
blocks Phase 1.

Every figure below was measured on this branch, not estimated. Re-measure before
starting — these are a baseline, not a spec.

### WI-10 — extend mutation coverage to the rest of the tool

Phase 1's catalogue covers four modules. The rest of the tool has never had its
tests questioned.

**Where mutation testing pays most is not where coverage is worst — it is where
coverage is already high.** `limits`, `safe_paths`, `sanitize` and `prescreen` sit
at 100%/100%/100%/100% behind a 90% gate, which means line coverage can no longer
distinguish a strong test from a vacuous one there. Those four are Tier A.

| Tier | Modules | Why | Budget |
|---|---|---|---|
| A | `limits`, `safe_paths`, `sanitize`, `prescreen` | security-critical, coverage exhausted as a signal | ~30 mutants |
| B | `graph_common`, `extractors/*`, `known_api_clients`, `internal_groups`, `repo_utils` identity index | core detection — a weak test here means silent wrong output | ~50 mutants |
| C | `aggregators/*`, `renderers/*`, `trace*` | reporting; a defect is visible, not silent | ~20 mutants |

Method per module: run the scoped `uvx mutmut@3.7.0` sweep, triage survivors, write
the killing test, transcribe the mutant. Same one-way ratchet as Phase 1.

**Budget the catalogue explicitly.** At the measured 0.8s per mutant, the ~22
Phase 1 entries cost ~20s. The tiers above take the catalogue to ~120 entries
≈ **96s**, which is the outer limit of what belongs in `make ci`. Beyond that,
add `--changed-only` to `scripts/mutation_check.py` (mutants whose target file is
in the working diff) for the local loop and keep the full catalogue in CI. State
the cap in the script; a catalogue that silently grows past the budget is how the
gate gets disabled.

### WI-11 — docstrings

Measured: **5** missing-docstring violations under ruff's `D100`–`D105`, **7** by
an AST sweep (ruff does not flag two `@dataclass` classes the sweep does), and
**35** total violations under the full `D` ruleset — 12 auto-fixable.

| Location | Missing |
|---|---|
| `schema.py:12`, `schema.py:28` | `FlowNode`, `FlowEdge` — the core data model |
| `trace.py:41`, `trace.py:50` | `UpstreamHit`, `TraceReport` |
| `repo_utils.py:38` | `__call__` on a public class |
| `aggregators/api_client_discovery.py:194` | nested `resolve` |
| `aggregators/service_call_report.py:64` | `_status` |

The remaining 30 are style, not absence: 16 × `D401` (imperative mood), 10 ×
`D413`, 2 × `D403`, 2 × `D301`.

Add `"D"` to `[tool.ruff.lint] select` with an explicit
`[tool.ruff.lint.pydocstyle] convention` — `D211`/`D213` are mutually exclusive
with `D203`/`D212`, so a convention must be chosen or ruff will contradict itself.
Fix all 35, then the rule gates.

The four undocumented dataclasses matter more than the count suggests: they *are*
the schema, and `SCHEMA.md` documents them separately, which is how the two drift.

### WI-12 — coverage

Measured: **84.97%** overall against an 80% floor, 352 passed / 2 skipped. The
floor has ~5 points of slack, which means it is currently gating nothing.

| Module | Coverage | Note |
|---|---|---|
| `trace_batch.py` | 67% | lowest in the tree |
| `extractors/ast_walk.py` | 68% | **where WI-7's receiver extraction lands** |
| `build_metabase_v2.py` | 77% | WI-3 lands here |
| `repo_utils.py` | 79% | 107 uncovered statements |
| `trace.py` | 79% | WI-1's F2 filter split lands here |
| `extractors/config.py` | 81% | |
| `extractors/regex_extractors.py` | 83% | WI-4, WI-8, WI-9 land here |

Two changes:

1. **Raise the global floor to 85** — already met, so it ratchets rather than
   demands work, and it stops the next five points of regression being free.
2. **Add per-module floors for the detection-critical modules** — `graph_common`,
   `extractors/*` at 90%, mirroring the existing security-module gate in
   `tests/test_zz_security_coverage_gate.py` (which already proves the pattern:
   coverage.py has one global `fail_under`, so per-module floors live in a test).

`ast_walk.py` at 68% is the one to fix first regardless of the floor: WI-7 adds
receiver extraction there, and adding logic to the least-covered extractor module
is how a fix ships its own defect.

**Coverage floors and the mutation catalogue must move together.** A floor raised
alone is an invitation to pad tests until the number passes; WI-10 is what makes
the number mean something.

### WI-13 — cognitive complexity

No complexity gate exists today (`[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]`).
Measured:

* **8 functions** exceed ruff `C901` at `max-complexity=10`;
* **69 functions** exceed cognitive ≥12 or cyclomatic ≥11 on an AST sweep.

Worst offenders, cognitive first:

| Function | Cognitive | Cyclomatic |
|---|---|---|
| `service_call_collect.py:116 _collect_http_out_edges` | **48** | 19 |
| `openapi_match.py:51 match_http_out_to_openapi` | 31 | 12 |
| `pii_flow_v2.py:30 _collect_pii_flow` | 31 | 11 |
| `repo_utils.py:361 _read_pom_identity` | 30 | 17 |
| `openapi_discovery.py:81 discover_helm_hosts` | 28 | 12 |
| `graph_common.py:185 match_path_in_inbound_index` | 25 | 14 |
| `extractors/ast_walk.py:61 call_name_js_go` | 24 | 13 |

Two gates, because one metric is not enough — the table shows they disagree:
`write_ropa_view` is cognitive 11 / cyclomatic 15, `collect_pii_touchpoints` is
cognitive 26 / cyclomatic 8. Cyclomatic counts branches; cognitive counts how hard
the nesting is to hold in your head.

1. **Add `C90` to ruff** at `max-complexity=10` and fix the 8 violations. Cheap,
   standard, no new tooling.
2. **Add `scripts/complexity_check.py`** for cognitive complexity — ruff has no
   cognitive metric — with a threshold of 15 and a **frozen allowlist that may
   only shrink**. A ratchet, not a cliff: 69 functions cannot be refactored in one
   change, and a gate that fails on day one gets switched off.

**Three of these are already Phase 1 work.** `match_path_in_inbound_index`
(cog 25) is WI-2's target, `path_templates_match` (cyc 13) is WI-1's, and
`_collect_http_out_edges` (cog **48**, the worst function in the codebase) is the
function every WI-1/WI-2 edge flows through. Refactor them *inside* those work
items, under their tests, rather than as a separate risky pass.

### WI-14 — architecture and maintainability review

Two modules carry disproportionate weight and are where Phase 1 keeps landing:

* **`repo_utils.py`** — 510 statements, 79% covered, 107 uncovered. It holds the
  component identity index, pom/gradle/package.json parsing, build-system
  detection, and path helpers. Four responsibilities, one module. WI-3 adds a
  fifth (version catalogs).
* **`build_metabase_v2.py`** — 388 statements, 77% covered. CLI parsing,
  orchestration, dependency collection, manifest writing.

Candidate decomposition, each needing an ADR in `docs/architecture.md` §8:

| Proposed | Contents | Motivated by |
|---|---|---|
| `src2sink/dependencies/` | pom, gradle, package.json, **version catalog** parsers | WI-3 |
| `src2sink/identity/` | component identity index + coordinate resolution | `repo_utils` size |

**This is a proposal to evaluate, not a decision.** A refactor of the two modules
Phase 1 edits, done *before* Phase 1 lands, would invalidate every line reference
in this plan. Sequence it after, and only if WI-3 and WI-7 confirm the seams are
where they look. Judge against `docs/architecture.md` §6 "Patterns in use" and
record the outcome — including "we looked and left it alone" — as an ADR.

New ADRs Phase 1 needs regardless of any refactor:

* **ADR-013** — `sql-payload-out` as a family, and why it is not an extension of
  `raw-code-payload` (WI-9);
* **ADR-014** — routing predicates vs filter predicates as separate functions
  (WI-1, finding F2);
* **ADR-015** — evidence-gated extraction tiers, and the rule that evidence must
  be a property of the source text rather than of pass ordering (WI-4/F3) or of a
  field name (WI-7).

### WI-15 — documentation

| Document | Update |
|---|---|
| `docs/architecture.md` | §3 component decomposition (catalog parser, `sql-payload-out` linker), §5 core data model (new family), §7 security-relevant findings (evidence gating), §8 ADR-013/014/015 |
| `docs/threat-model.md` | See below — this one needs a new entry, not just edits |
| `docs/security-privacy-gap-analysis.md` | §8 SRTM rows for any new control; `scripts/srtm_check.py` gates both directions, so a new `TA-xxx` in a test without a matrix row fails CI |
| `docs/sast-report.md` | Re-run bandit + opengrep after Phase 1; record deltas |
| `SCHEMA.md` | `sql` detail changes (WI-7), `sql-payload-out` family (WI-9), and the four dataclass docstrings (WI-11) — `SCHEMA.md` and the dataclasses document the same thing and drift apart |
| `README.md`, `metabase-usage.md` | new family in the output description |
| `docs/api-clients-json.md` | §2c wrapper escape hatch; demand-side discovery output |
| `CHANGELOG.md` | both behaviour changes (path matching, withdrawn `sql` findings) |

**The threat model needs a threat it does not currently carry.** Its STRIDE
analysis covers attacks on *src2sink* — a hostile repo hanging a worker (D-1),
escaping path containment (T-1/T-2), injecting into Markdown output (I-4). OI-7
is a different shape: the tool's **own output is wrong in a way that misdirects
security work**. A fabricated `raw-code-payload` finding sends an analyst to audit
code that was never vulnerable, and — more damaging — erodes trust in the true
findings beside it. That is an integrity property of the output, and neither the
STRIDE section nor the risk register has a row for it.

Proposed additions:

* **Tampering / Integrity** — "Detection produces a confidently wrong finding."
  Controls: evidence-based gating (WI-7), the mutation catalogue (WI-10),
  precision-negative tests as a standing requirement for any widened pattern.
* **Risk register** — one row for false-positive rate as a first-class risk,
  alongside the existing false-negative framing in ADR-012 ("negative coverage is
  a first-class output"). The document currently treats missing findings as the
  risk and wrong findings as noise; OI-7 shows the ordering is the other way
  round.
* Routine surface updates: new regexes extend the D-2 bounded-regex surface (all
  land in TA-005); new file reads (`gradle/libs.versions.toml`,
  `settings.gradle.kts`) extend T-1/T-2 path containment and D-4 size gating and
  must go through `is_skipped_path` + `safe_read_text`.

Also check `_REDACT_DETAIL_FIELDS` in `build_metabase_v2.py:271` against the new
`sql-payload-out` detail: it redacts `snippet`, `raw`, `url`, `bucket`,
`endpoint_path`. The new family's `evidence` field may carry source text and would
not currently be redacted.

### Phase 2 effort

| Work item | Effort |
|---|---|
| WI-11 docstrings | 0.5 d |
| WI-13 complexity gates (gates + the 8 `C901` fixes; not the 69) | 1 d |
| WI-12 coverage floors + `ast_walk` tests | 1 d |
| WI-15 documentation | 1.5 d |
| WI-10 mutation coverage, Tier A + B | 2.5 d |
| WI-14 architecture review (assessment + ADRs, excluding any refactor) | 1 d |
| WI-10 Tier C | 1 d |

~8.5 days total. WI-11 and WI-13's gates are the cheapest and stop the problem
growing while the rest proceeds, so do those first.
