# Observe, then classify — amendment to the 3.0 plan

**Status:** proposal for review. Amends
[`src2sink-3.0-plan.md`](src2sink-3.0-plan.md) and supersedes the
boundary-catalogue question left open as Q6 in
[`identity-versioning-boundaries.md`](identity-versioning-boundaries.md).

**Where it came from:** a review conversation that started as "where should the
boundary catalogue live" and ended somewhere else. Each step is recorded with the
measurement that forced it, because the conclusions only make sense as a chain.

**One-line summary:** the extractor should record *what it saw*, not *what it
concluded* — and the output is an indicator for an LLM to act on, not a verdict.

---

## 1. The question that started it, and why it was the wrong question

**Q6 was:** should the boundary catalogue (library → `sink`/`source` → family)
live in Python constants or in config?

The tension seemed to be cost. The catalogue is a detection input, so adding one
line changes what is detected, which under `OI-16` bumps `DETECTION_VERSION` and
forces a **full fleet rescan**. Config makes additions easy and makes that cost
invisible at the point of editing; code makes the cost visible but needs a
release for a one-line change.

**The objection that dissolved it:** the metabase maps *data flows*, not *danger
flows*. Adding a catalogue entry ought to be adding a **flag to a flow that was
already recorded** — a reclassification, not a re-extraction. Reclassification
needs no rescan.

That is not how it works today, and the gap turns out to be the design.

---

## 2. Measured — the catalogue gates existence, not classification

`_maybe_add_sql_sink` in `extractors/ts_extractors.py`:

> "Append a sql sink node **if** the call is evidenced as database work."

```python
if not (has_hint or receiver_is_database(receiver) or file_sql_evidence):
    return          # no node at all
```

So the library evidence decides whether the node **exists**. Change the
catalogue, and different nodes are produced — hence the rescan.

### And the OR has a live defect

Three evidence terms OR'd together, one of them file-scoped, so the weakest term
decides once satisfied:

| File | `file_has_sql_evidence` | `sql` sinks emitted |
|---|---|---|
| an HTTP client call alone | `False` | none — the gate works |
| **the same call, plus one real SQL query elsewhere in the file** | `True` | **`httpClient.execute`** *and* `jdbcTemplate.query` |

`receiver_is_database('httpClient')` is `False` — the receiver *knows* it is not a
database — but file evidence overrides it. **One real SQL statement anywhere in a
file admits every sink-named call in that file.**

This is `OI-7`'s residual. That fix replaced "name alone" with "name OR three
evidence terms", and file scope is too coarse for a call-level decision — the
same scope error as using a repo-level manifest to justify a file-level finding.
Since an execution sink feeds `link_raw_code_payload_endpoints`, it can still
manufacture the fabricated injection endpoint `OI-7` was raised to stop.

**Proposed as a new issue** (see §9). Worth noting *how* the architecture makes
it expensive: under today's design fixing it is a detection change and costs a
fleet rescan. Under §3 it is a classifier change costing a re-aggregation.

---

## 3. Amendment 1 — record the observation, classify downstream

The extractor records what it saw:

```
call: execute      receiver: httpClient      library hint in call text: no
                   file has SQL evidence: yes
```

A classifier then decides, from the catalogue, whether that is a `sql` sink, an
`http-out` call, or neither.

### The change is smaller than it sounds

Measured: `iter_calls` is invoked **once**, at `ts_extractors.py:115`, and
dispatches to `_maybe_add_sql_sink` and `_maybe_add_script_exec`. The structure
is *already* observe-then-dispatch. What is missing is that the observation is
**discarded** when no family claims it.

### The extra nodes are inventory, not cost

In a representative mixed file, 7 calls carry a sink-shaped name and 2 are real
SQL. The other 5 are not waste:

| Discarded today | What it actually is |
|---|---|
| `httpClient.execute` | an outbound HTTP boundary |
| `digest.update` | a crypto operation |
| `pool.execute` | a task-execution boundary |
| `s.update` | possibly an ORM write |

Each is a different family's finding. Recording the observation once serves
`sql`, `http-out`, `crypto-*`, `script-exec` and the deserialization family that
does not exist yet — from one pass. The volume is bounded: calls with a
sink-shaped name, not all calls.

### What it buys

* **Q6 becomes moot.** A catalogue entry is a classification rule; adding one is
  a re-aggregation, which happens every run anyway. Config, no rescan.
* **`DETECTION_VERSION` regains its meaning.** It bumps when what is *observed*
  changes — a genuinely rarer event — rather than every time a label moves.
* **Defects like §2's become cheap.** Fix the classifier, re-aggregate, done.

It is the same shape as Finding A in the
[architecture review](architecture-review-2.0.md) — computation fused with
rendering. Here it is **observation fused with classification**, with the same
consequence: you cannot re-derive without re-extracting.

---

## 4. Amendment 2 — collect edges unconditionally too

When the scan meets `B → C` it does not yet know that `C → D → sink` exists. So
**significance is only knowable globally, after collection**, and edges cannot be
filtered at discovery time.

Cheap: 2,963 edges for a 2,000-service fleet. The graph is trivial to hold — §8
of the design notes measured the traversable skeleton at 0.52% of the metabase.
It is the payload that is large.

Same property as §3, one level up: a sink-catalogue change reclassifies without
re-collecting.

---

## 5. Amendment 3 — `OI-17` is bipartite reachability, and depth is not optional

We do not need full dataflow analysis. We need the set of **inbound** boundaries,
the set of **outbound** boundaries, and whether any inbound reaches any outbound.
That is reachability between two small labelled sets — not tracking every value
through every assignment.

Consequences: search from whichever set is smaller and stop at first connection;
no transitive closure; no need to model field writes, collections or return
values in general — only whether an argument travels.

### Depth is where the findings are

Measured on a 2,000-service fleet, 2,963 edges, hub-heavy:

| depth | services reached | % of fleet | **entry points reaching ≥1 sink** | cost |
|---|---|---|---|---|
| 1 | 2.5 | 0.1% | **12%** | 0 µs |
| 3 | 7.5 | 0.4% | **25%** | 1 µs |
| 4 | 11.0 | 0.5% | **32%** | 1 µs |
| 8 | 36.3 | 1.8% | **64%** | 4 µs |
| 12 | 73.8 | 3.7% | **70%** | 8 µs |

**Capping at 3 hops finds 25% of what depth 8 finds.** `A → B → C → D → sink` is
the common case, not the exception.

**And it does not become a hairball.** At depth 12 an entry point still reaches
only 3.7% of the fleet, so depth stays *informative* as well as affordable. Cost
is a non-issue throughout.

---

## 6. Amendment 4 — path confidence is a minimum, not a product

Multiplying hop confidences destroys exactly the paths §5 shows are most of the
value:

```
each hop 'medium' (~0.7), multiplied:   4 hops -> 0.240    8 hops -> 0.058
```

But hops are not independent coin flips. A chain of eight *individually
resolved* calls, each with a declared receiver type, is not less trustworthy than
two fuzzy path-string matches.

**Take the minimum hop confidence, record path length separately, and name the
weakest link in the evidence.** A reader can act on "8 hops, all `high`, weakest
link is the `B→C` binding". Nobody can act on `0.058`.

---

## 7. Amendment 5 — the output is an indicator, and the two roles have opposite error costs

The output supports an LLM: first, showing where to dig; second, reducing false
positives for paths that cannot be reached.

| Role | Claim | A wrong answer costs |
|---|---|---|
| **Indicator** — where to dig | positive: "a path exists" | some wasted digging — cheap |
| **Exclusion** — cannot be reached | **negative**: "no path exists" | a suppressed real vulnerability — expensive |

They cannot share a threshold.

### Retracted: the confidence floor

An earlier draft proposed refusing to emit paths below a confidence floor. That
is backwards for an indicator: it converts cheap false positives into expensive,
invisible false negatives. **Emit broadly, rank honestly, never suppress on
confidence alone.**

This does not weaken the `OI-1`/`OI-7`/`OI-10` discipline, which was about not
*asserting* more than the evidence supports — the confidence **label**, not
whether the row exists. Emitting a `low` path is fine; calling it `high` is not.
The `OI-25` fix already has this shape: verb-only matches were capped at `low`,
not dropped.

### Never assert unreachability

With reflection, DI, lambdas, queues and dynamic dispatch, syntactic analysis
can almost never *prove* unreachability. The tool must say **"no path found by
resolvable calls"**, with the means stated and the blind spots named — never
"unreachable".

So the false-positive reduction comes mostly from **ranking** rather than from
asserting a negative. Where a genuine negative exists it should be marked as
such: "this repo has no datastore of any kind" is a positive fact about absence
and far stronger than "I traced no path".

### The hop chain is the product

If the consumer is a model about to go and read code, the chain matters more than
the verdict: each hop's `file:line`, the resolution tier, and the argument that
carried the value. A score alone says nothing about where to start.

The untrusted-content discipline already assumes an LLM consumer —
`UNTRUSTED_CONTENT_NOTICE` exists because extracted spans reach a model. This
extends it from "do not let the data attack the model" to "shape the data so the
model can act on it".

---

## 8. Amendment 6 — interface dispatch is why the negative claim is unsound

Measured. A controller holding an **interface**-typed field:

```
stockService.process()  ->  StockService (interface)  ->  body: None
   ^ a declared-type-only resolver reports NO PATH, and is confidently wrong

expanding through the implements index -> ['StockServiceImpl', 'AuditServiceImpl']
      StockServiceImpl     sink present: True     <- the SQL injection lives here
      AuditServiceImpl     sink present: False
```

The interface method has **no body at all**, so stopping at the declared type
does not produce a low-confidence answer — it produces a dead end, indis­tinguish­able
from "nothing here". Constructor-injected interface fields are the standard
Spring shape, so this is the default case in the fleet, not a corner.

**It is resolvable.** `implements`/`extends` is a plain syntactic read —
tree-sitter exposes `super_interfaces` and `superclass` — so this is T2 in the
tier ladder, not a permanent blind spot. The cost is fan-out, which is the right
cost under §7: emit both candidates ranked, and let the reader check them.

### One fleet-wide type index, three consumers

The interface is often declared in a *different* repo — a shared API library — so
the index must be fleet-wide. That converges with the answer to Q5 (build a map
of POM locations during the fleet traversal):

| Consumer | Question it asks the index |
|---|---|
| `OI-18` parent resolution | where is `com.example:platform-parent`? |
| `OI-17` T2 resolution | who implements `StockService`? |
| Cross-repo call resolution | which repo declares this type? |

One pass, three uses.

### What cannot be known from synthetic files

The fan-out cost depends on the implementations-per-interface distribution in the
real fleet. One implementation per interface means T2 resolves uniquely and is
nearly as good as T1; five means every path branches fivefold and ranking carries
the weight. **Measure this early in `OI-17`**, because it decides whether T2 is a
mild downgrade or the dominant source of noise.

Honestly outstanding: generics, abstract classes, transitively inherited
interfaces, and Spring selecting an implementation by `@Qualifier` or profile.
The last is genuinely unknowable statically — several implementations will be
legitimate candidates with no syntactic way to choose between them.

---

## 9. Proposed issue

**A file-scoped SQL evidence term admits every sink-named call in the file.**
Measured in §2. `httpClient.execute(r)` is catalogued as a SQL execution sink
because a JDBC query exists elsewhere in the same file, despite the receiver
being known not to be a database. Feeds `link_raw_code_payload_endpoints`, so it
can still manufacture the fabricated injection endpoint `OI-7` describes.

Fix has two sides, and both are needed or recall drops:

1. File evidence must not override a receiver that is known **not** to be a
   database. It should rescue only calls with no receiver, or with a library hint
   in the call text.
2. The receiver vocabulary needs widening to compensate — `ps` and `pstmt` are
   absent today, while `stmt`, `conn` and `session` are present, so tightening
   alone would drop real `PreparedStatement` calls.

Under the current architecture this is a detection change and forces a fleet
rescan. Under §3 it is a classifier change. **That is an argument for sequencing
§3 before fixing it** — not for leaving it unfixed.

Id deliberately unassigned: `OI-18`…`OI-23` are reserved by the candidate list in
the design notes, still under review, and ids are never renumbered once cited.

---

## 10. What this changes in the 3.0 plan

| Phase | Change |
|---|---|
| **Phase 1** (separate computation from rendering) | extends to separating **observation from classification** — the same seam, one layer deeper |
| **Phase 2** (`OI-17`) | rescoped to bipartite reachability, unbounded depth, T2 interface expansion; `OI-21` already moved ahead of it by the Q7 answer |
| **Phase 3** (`OI-15`) | unchanged, but the observation layer adds nodes — the volume needs measuring against the ceiling |
| **Boundary catalogue** | moves to config; Q6's tension disappears once classification is downstream |

**The through-line:** every amendment here is the same move. Record what was
observed, decide what it means later, and never let a decision that is cheap to
revise get baked into a step that is expensive to repeat.
