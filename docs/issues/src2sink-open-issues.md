# src2sink 1.1.0 — Open Detection Issues and Proposed Fixes

**Version reviewed:** src2sink 1.1.0
**Status:** every issue in this document is **open**. Fixed issues are removed from here and recorded in [`src2sink-closed-issues.md`](src2sink-closed-issues.md) with their fix and commit sha, so the length of this file is the backlog. Earlier defects (empty-binding silent failure, `api-client-consumer` nodes never reaching the call graph, class-name-anchored call-site regexes, constant/enum indirection, binding aliases, unmatched-ref reporting) were fixed in 1.1.0 before that convention existed and are not repeated here.

**Citing an issue:** use the stable `OI-n` id shown on each heading, not the section number — section numbers do not survive the move to the closed-issues file. See §5.

**Anonymisation notice:** every repository name, package name, artifact id, service name, class name, constant name and **URL path** in this document is fictitious. The worked example throughout is an invented warehouse system. References to `src2sink`'s own source (file:line) and to third-party library names appearing in `src2sink`'s regexes (`RestTemplate`, `requests`, …) are real, as those are needed to locate the code being fixed.

---

## 0. Context: how these were found

**§1–§4** came from a fleet scan of several hundred repositories, used to measure detection coverage for one heavily-consumed internal service. Coverage of that service's callers in the service-call graph rose from 1 to 22 after upgrading to 1.1.0. Investigating the callers that *remained* invisible surfaced those four issues. Three of them are general — not specific to the service used as the probe.

**§7–§9** came from a later review of the SQL families, unrelated to the fleet scan. Their evidence is measured `extract_from_file` output on 1.1.0 rather than fleet statistics.

The running example is a fictitious service `commerce/warehouse-service`, which publishes a client library `warehouse-service-client` (group `com.example.commerce.warehouse.client`) and exposes `POST /stock`. It is consumed by a fictitious repo `fulfilment/fulfilment-commons`.

---

## 4. Client discovery is single-direction and never proposes `class_patterns`  `OI-4`

**Severity:** Medium (capability gap). This section also answers "could discovery run from the other direction?" — yes, and the two directions are complementary rather than redundant.

### Current behaviour

`aggregators/api_client_discovery.py` mines in one direction only, which can be called **supply-side**:

> a consumer declares a dependency on an artifact whose id looks like a client library → resolve that coordinate to the publishing repo → take candidate paths from that repo's `http-in` nodes.

Two consequences:

1. **`class_patterns` is always empty.** `_collect_candidates` hardcodes `"class_patterns": []`. The field appears in `_TUNABLE_FIELDS` — it is preserved once a reviewer edits it, but never proposed. Since `class_patterns` is the mechanism that catches call sites carrying no URL, discovery cannot generate the field that most needs generating.
2. **A caller with no client library is structurally invisible.** A repo that hand-rolls HTTP (§2) has no `*-client` dependency to mine. Supply-side discovery cannot reach it by construction — no amount of dependency parsing finds a dependency that does not exist.

### Proposed: add demand-side discovery

Mine the opposite direction:

> a call site that resolves to a known service, but whose repo declares no client library for it → propose a binding, or enrich an existing one, describing how that call site is recognised.

Evidence already present in the metabase — no new extractor required:

| Signal | Source | Yields |
|---|---|---|
| Unmatched outbound call sites | `graphs/service-call-unmatched.jsonl` | the work queue |
| Route constants | `path-constant` nodes | candidate `paths` |
| Resolvable hosts | `http-out` node `detail.host` | candidate `service_aliases` |
| Deployment hostnames | `graphs/helm-service-hosts.jsonl` | candidate `service_aliases` |
| Config base-URLs | config extractor nodes | candidate `service_aliases` |
| **Service name as a literal** | any string literal equal to a known repo name or alias | high-confidence `target_repo` |
| Enclosing class of the call site | `http-out` node `file` + nearest class declaration | candidate `class_patterns` |

The last two rows are the valuable ones. A string constant whose value equals a known service name — a token audience, a config key, a queue name — is unusually strong evidence, and it is exactly the sort of marker that survives in hand-rolled clients which have no other identifying feature. The enclosing class name is precisely the `class_patterns` value a reviewer would otherwise have to derive by hand.

### Parallel or sequential?

**Sequential, with the demand-side pass enriching the supply-side output.** The two passes are not symmetric competitors; they produce *different fields for the same candidate*:

| Field | Supply-side | Demand-side |
|---|---|---|
| `target_repo` | coordinate → identity index | route / host / name-literal match |
| `maven_artifact` | authoritative | may not exist |
| `import_prefix` | from groupId | — |
| `paths` | target's `http-in` nodes | caller's route constants |
| `service_aliases` | derived from repo name | observed hosts |
| `class_patterns` | **always empty** | enclosing class |

Running them in parallel yields two candidate sets that must be merged anyway, and the merge key is ambiguous for demand-side-only candidates — there is no artifact id to key on. Running demand-side second lets it do a keyed lookup:

```
supply-side pass
  → candidates keyed by (target_repo, artifact)

demand-side pass
  → for each unmatched or weakly-matched call site:
      resolve target_repo
      if a candidate exists for that target:   enrich it
          - append observed service_aliases
          - append proposed class_patterns
          - union observed paths
          - upgrade confidence: both directions agree
      else:                                    create a new candidate
          - key (target_repo, "<hand-rolled>")
          - maven_artifact: "" and import_prefix: "" (there is none)
          - status: pending, flagged as call-site-only
```

Neither pass is expensive — both run in the aggregation phase with the fleet already in memory — so parallelism buys little wall-clock and costs merge complexity. Sequence for correctness, not speed.

### Confidence from agreement

Record how each candidate was found and score agreement explicitly:

```python
entry["discovery_method"] = "dependency" | "call-site" | "both"
```

`both` is materially stronger than either alone: a declared dependency *and* an observed call site resolving to the same service are independent lines of evidence. Conversely, `call-site` alone should sort lowest, since it rests on the path matching that §1 shows can be wrong.

### Two safeguards this needs

**Proposed `class_patterns` must be checked for distinctiveness.** Binding class patterns run in an **unguarded, language-agnostic tier** (`extractors/regex_extractors.py:257-259` — `language="any"`, no file guard, plain substring match after `re.escape`). A proposal such as `Client`, `ApiClient` or `ServiceGateway` would match across the fleet and manufacture phantom edges. Discovery should compute each proposal's corpus-wide occurrence and refuse or flag broad ones:

```python
MAX_PATTERN_REPOS = 3

occurrences = _repos_containing_literal(records, proposed_class)
if len(occurrences) > MAX_PATTERN_REPOS:
    entry.setdefault("warnings", []).append(
        f"class_pattern {proposed_class!r} appears in {len(occurrences)} repos; "
        "too generic to be safe — narrow it before accepting"
    )
```

**Guard against self-confirmation.** Demand-side discovery resolves targets by matching against routes and aliases that promoted bindings already influence. Once a binding is promoted, the edges it creates must not be re-ingested as fresh evidence for itself, or confidence inflates on every run. Record evidence provenance and exclude nodes whose `target_repo` was stamped by a binding — `detail.target_repo_evidence` already distinguishes these — from the demand-side input set.

### Why this closes a loop

`service-call-unmatched.jsonl` (added in 1.1.0) is both the input to demand-side discovery and the natural measure of its success: every accepted candidate should remove entries from it. That gives the discovery pass a regression metric it currently lacks — *unmatched call sites trending to zero* — rather than only a count of candidates produced.

---

## 5. Priority

| id | # | Issue | Effort | Value | Priority |
|---|---|---|---|---|---|
| OI-4 | 4 | Demand-side discovery | medium | generates the field that cannot be inferred otherwise | P2 |

Everything above P2 has been fixed and moved to
[`src2sink-closed-issues.md`](src2sink-closed-issues.md); what remains is the one
capability addition. The ordering rationale for the fixed set is preserved there
alongside each issue.

### Issue ids and lifecycle

Each issue carries a stable `OI-n` id **in addition to** its section number,
because section numbers do not survive the move to
[`src2sink-closed-issues.md`](src2sink-closed-issues.md). Cite `OI-n` — never `§n` —
from test docstrings, commit messages, and code comments.

When an issue is fixed it is **removed from this document** and its section moved
verbatim to the closed-issues document, with a fix description and the commit sha
appended. This file is therefore always and only the open set: its length is the
backlog. See the closed-issues header for the exact move procedure.

---

## 6. Cross-cutting principle

Three of these four defects share one shape: **a detection path that fails to empty without emitting a signal.**

- An empty bindings file disabled all client detection (fixed in 1.1.0 by a hard error plus a manifest count).
- A guard that never matches produces zero nodes and no note (§2).
- An unparsed dependency format produces `dependencies_internal: []` and no note (§3).

The 1.1.0 work established the right pattern — the manifest binding count, the unconditional `service-call-unmatched.jsonl`, the recorded oversized-file skips. Extending it consistently is the durable fix: **any detection input that resolves to nothing should say so in the run manifest or the repo's notes.** A count of zero is a finding; an absent field is not.

---

