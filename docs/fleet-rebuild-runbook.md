# Fleet rebuild runbook — `DETECTION_VERSION` 13 → 19

**Read this before you start the run, not after.** Several of the numbers worth
having can only be captured *before* the rebuild overwrites them, and one file —
`run-manifest.json` — is overwritten in place with no backup.

## Why this rebuild matters beyond the records

The rescan is forced: `DETECTION_VERSION` moves 13 → 19 and `DERIVATION_VERSION`
5 → 6, so no existing record is reused. That is unavoidable work. What makes it
worth preparing for is that it is also the **first full-fleet run with the Phase 0
timing instrumentation in place** (`OI-32`). Every performance number in the 3.0.0
measurements was obtained by hand-instrumenting a build; this run reports its own
shape. Run it unprepared and you pay the cost without collecting the data.

Five open questions get answered by this run *for free* if you keep the output,
and cannot be answered without another full rebuild if you don't.

---

## Part 1 — Before the run

### 1.0 Do you need to run the old version first? No — and don't

The metabase **currently on disk is already the "before" state**. Rebuilding it
with 3.0.0 would overwrite it with older records, destroying the comparison you
are trying to set up.

It would not buy the timing baseline either. Phase timings did not exist before
`DETECTION_VERSION` 14, so no earlier version records them — a 3.0.0 run would
give you a wall-clock total and nothing else, and the previous run's manifest
already carries that in `started_at` / `finished_at`.

What to do instead is confirm *what* you are comparing against:

```
python3 -c "import json;d=json.load(open('$HOME/fleet-measurements/run-manifest.BEFORE.json'));print({k:d.get(k) for k in ('tool_version','detection_version','started_at','finished_at')})"
```

| what it says | what the rebuild measures |
|---|---|
| `detection_version: 13` (3.1.0) | the 13 → 19 delta — `OI-41`'s aggregation win is already in the before-state, so the record growth you see is `OI-43`'s |
| `detection_version` < 13 | an older baseline; `OI-41`'s 2.4x aggregation improvement is *also* in the delta, so do not attribute all of it to one change |
| no manifest at all | you have no before-state. Accept it, run the rebuild, and this run becomes the baseline for next time — a 3.0.0 run would not fix it |

### 1.1 Record the host

The 3.0.0 baseline (657 s end to end) was taken on 12 cores / 36 GB / local NVMe.
None of the timing comparison below means anything against an unknown machine.

```
sysctl -n hw.ncpu hw.memsize && df -h <metabase-root> && sw_vers
```

Write the three lines into your notes file. Also note whether the fleet checkout
is on local disk or a network mount — that single fact dominates the read phase.

### 1.2 Save the previous run's manifest — it is about to be overwritten

`run-manifest.json` is written to `<metabase-root>/run-manifest.json` and replaced
on every run. It is the only record of what the previous detector produced.

```
cp <metabase-root>/run-manifest.json ~/fleet-measurements/run-manifest.BEFORE.json
```

**If you skip one step in this document, do not let it be this one.** Without the
before-manifest there is nothing to compare the new timings against.

### 1.3 Measure the current metabase size

The "2.2 GB of JSON from 1.07 GiB of source" figure is **stale**. It predates
`OI-43` step 3, which added declared field types, supertypes and `self_name` to
records. Records have grown by an unknown amount, and §3.5 of the 4.0 plan — the
record-size work — needs the real before-and-after.

```
du -sh <metabase-root>/repos > ~/fleet-measurements/metabase-size.BEFORE.txt
```

```
find <metabase-root>/repos -name '*.json' | wc -l >> ~/fleet-measurements/metabase-size.BEFORE.txt
```

### 1.4 Save the current fleet baseline

Family counts shift across a detection bump. Re-freezing the regression fixture
afterwards is only safe if you can see what moved.

```
cp tests/fixtures/fleet-family-baseline.json ~/fleet-measurements/fleet-baseline.BEFORE.json
```

### 1.5 Confirm configuration is actually loaded

A run with `--api-clients` pointed at a missing file, or with no internal-group
prefixes configured, looks identical to a healthy run everywhere except one
number. Check the *before* manifest for what the last run actually loaded:

```
python3 -c "import json;print(json.load(open('$HOME/fleet-measurements/run-manifest.BEFORE.json'))['invocation'])"
```

If `api_clients_binding_count` is 0, fix that before rebuilding — otherwise you
will spend the whole rescan producing a metabase with no client bindings in it.

---

## Part 2 — The run

Use `--force`. The version bump alone should invalidate every record, but
`--force` makes a true cold rebuild certain, which is what the timing numbers
require.

```
src2sink-build --repos-root <fleet> --metabase-root <metabase-root> --force --api-clients api-clients.json 2>&1 | tee ~/fleet-measurements/build.log
```

Two things to be aware of while it runs:

- **Only the first run's timings are meaningful.** Run it twice and the second is
  incremental; its phase breakdown compares to nothing.
- **Peak memory is not recorded by the tool.** If you want to confirm the 5.75 GiB
  aggregation figure — the number that closed off multiprocessing as an option —
  you have to watch it live. In a second terminal:

```
while true; do ps -o rss=,comm= -p $(pgrep -f src2sink-build | head -1) >> ~/fleet-measurements/rss.log; sleep 10; done
```

---

## Part 3 — After the run

### 3.1 Save the new manifest immediately

Before anything else touches the metabase:

```
cp <metabase-root>/run-manifest.json ~/fleet-measurements/run-manifest.AFTER.json
```

### 3.2 Read the five numbers this run exists to produce

**Timing — where the run actually went.**

```
python3 -c "import json;print(json.dumps(json.load(open('$HOME/fleet-measurements/run-manifest.AFTER.json'))['timing'],indent=2))"
```

What to look at: aggregation's share of the total. It was **78%** in 3.0.0, and
`OI-41` claims 2.4x on aggregation with peak memory unchanged — unconfirmed at
fleet scale until now. Also read `unattributed` at every depth: that is time no
phase claimed, and it is where the next instrumentation goes rather than noise to
be ignored.

**Counts — what could not be read.**

```
python3 -c "import json;print(json.dumps(json.load(open('$HOME/fleet-measurements/run-manifest.AFTER.json'))['counts'],indent=2))"
```

Three things in here, each meaning something different:

- `unparsed.records_unreadable` — **check this first.** If it is non-zero, every
  other unparsed number is a lower bound, because a record the counter could not
  read is a repo whose failures went uncounted.
- `unparsed.{source_files, manifests, repos_affected}` — the first fleet-scale
  figure for the `OI-36` sweep. A manifest that would not parse means
  dependencies are *incomplete*, not empty; a lockfile that would not parse means
  resolved-version counts understate what is actually pinned.
- `resolution_gaps` — repos affected per language. **This decides `OI-43` step 5.**
  The plan says to add Scala "unless the fleet is Scala-heavy"; this count is
  exactly that question, and it is currently unanswered.

**Repos with no git history.**

```
python3 -c "import json;d=json.load(open('$HOME/fleet-measurements/run-manifest.AFTER.json'));r=d['updated_repos'];print(sum(1 for x in r if not x.get('git_sha')),'of',len(r),'have no git sha')"
```

This re-measures `OI-22` (65 of 746 previously). Those repos re-extract on every
run regardless of whether they changed, and `.git` presence being unusable as a
discriminator is load-bearing for `OI-34`.

### 3.3 Re-measure the metabase size

```
du -sh <metabase-root>/repos > ~/fleet-measurements/metabase-size.AFTER.txt
```

Compare against `metabase-size.BEFORE.txt`. This is the input to §3.5 — whether
to drop derivable fields, hoist per-file constants, and omit null defaults. That
work is explicitly gated on measuring first, and this is the measurement.

### 3.4 Re-freeze the fleet baseline — deliberately

```
src2sink-baseline --metabase-root <metabase-root> --output tests/fixtures/fleet-family-baseline.json
```

Then diff it against `fleet-baseline.BEFORE.json` and **look at what moved before
committing it**. A detection bump is expected to shift family counts; a family
that vanished entirely is not, and re-freezing without looking converts a
regression into the new baseline.

### 3.5 Check that the tier claims hold at fleet scale

```
uv run python scripts/measure_resolution_tiers.py --metabase-root <metabase-root> -o ~/fleet-measurements/tiers.AFTER.json
```

`OI-43` step 3 claimed TypeScript T3 → T2, and Python and Go T3 → T1. Those were
measured on a **canonical shape**, not on real code. This is the check:

| language | expect |
|---|---|
| typescript / tsx | T2 present where it was previously all T3 |
| python | T1 present |
| go | T1 on concrete fields; **T2 stays 0 by design** — interface satisfaction is structural |
| javascript | supertypes only; T1 is intrinsically impossible |

If TypeScript or Python still shows ~100% T3 across the estate, the fixtures
moved and the fleet did not, and that is worth knowing *before* Phase 3 builds
more resolution on top of it.

Also read the `ambiguous` block: a T1 or T2 whose confidence was degraded to
`low` means the resolver found several candidates. A high ambiguous share means
the tier is technically reached but the answer is weaker than the tier implies.

### 3.6 Census where the metabase's bytes go

```
uv run python scripts/measure_record_size.py --metabase-root <metabase-root> -o ~/fleet-measurements/record-size.AFTER.json
```

This is the input to §3.5 of the 4.0 plan — the record-size work — which states
each candidate is to be **measured before building**. The script reports what
each would save, in exact bytes:

1. dropping `repo` and `id` (derivable),
2. hoisting `file`, `language` and `framework` to per-file constants,
3. omitting null `pii_classification` and `data_class`.

It also gives you `nodes_by_kind`, which is the number behind "call sites are
~75% of nodes". Run it against the *before* metabase too if you still have it —
that quantifies how much `OI-43` step 3's field and supertype facts grew the
record, which nobody currently knows.

### 3.7 Run the parse-error measurement while the fleet is on disk

Independent of the build, but it needs the checkout present, so do it in the same
sitting. This is `OI-43` step 6's outstanding fleet run.

```
uv run python scripts/measure_parse_errors.py --repos-root <fleet> --depth 2 -o ~/fleet-measurements/parse-errors.json
```

Do not stop at the headline percentage. The 21-repo sample could not settle two
things, and they are the whole point of running it at fleet scale:

- **Is there a genuine broken-source population in the Java/Kotlin bulk?** The
  sample's entire broken-source population was one file, seen twice in two
  near-duplicate checkouts.
- **Does any file have a catastrophic error** — `error_ratio` approaching 1.0,
  the grammar bailing early? Nothing in the sample exceeded 0.0053. That class is
  the only one that would actually cost extracted nodes, and its existence
  decides whether step 6 ships a report at all.

Read `error_contexts` in the output. If it is dominated by `jsx_element` and
`jsx_opening_element` as the sample was, the count is measuring valid React that
tree-sitter cannot accept, not a coverage problem.

---

## Part 4 — What this still cannot tell you

**Sizing Phase 2 (`OI-34`)** wants two counts. The self-edge half is now produced
by the run itself — see §3.8. The other half, *how many repos publish more than
one artefact at a single depth*, still has no instrument, though
`_build_component_identity_index` already computes what it would need.

---

### 3.8 Read the `OI-34` sizing number

No command — the run writes it:

```
<metabase-root>/graphs/service-call-self-edges.jsonl
```

One line per repo, with a count and the target paths involved. These are the
call edges the collector **refused because source and target were the same repo
id** — which is exactly the hop `OI-34` exists to recover: a call from a client
library to the service it fronts is a call within one repo id, so it is dropped,
and the hop the client exists to make is the hop that disappears.

They never reach a written record, so before this they could not be counted at
all. The total is the number of edges the estate would *gain* if the project
rather than the repo became the unit of analysis, and the per-repo breakdown says
which repos are worth splitting first.

```
wc -l <metabase-root>/graphs/service-call-self-edges.jsonl
```

Treat it as a lower bound on the benefit, not an upper one: it counts only hops
where both ends were already detected. A client whose calls resolve to nothing
today contributes zero here and would still gain from the split.

---

## Checklist

**Before**

- [ ] Host recorded (cores, RAM, disk, local vs network mount)
- [ ] `run-manifest.json` copied to `run-manifest.BEFORE.json`
- [ ] Metabase size and record count saved
- [ ] `fleet-family-baseline.json` copied
- [ ] `api_clients_binding_count` in the before-manifest is non-zero

**Run**

- [ ] `--force`, output tee'd to `build.log`
- [ ] RSS sampled in a second terminal (only if you want the memory figure)

**After**

- [ ] `run-manifest.json` copied to `run-manifest.AFTER.json` before anything else
- [ ] Timing read; aggregation's share compared against 78%
- [ ] `counts.unparsed.records_unreadable` checked first
- [ ] `counts.resolution_gaps` read — this decides `OI-43` step 5
- [ ] Repos with no git sha counted
- [ ] Metabase size compared before/after
- [ ] Baseline re-frozen **and the diff inspected**
- [ ] `measure_resolution_tiers.py` run — do TypeScript/Python/Go actually move tier?
- [ ] `measure_record_size.py` run — §3.5's three candidates, in bytes
- [ ] `measure_parse_errors.py` run; `error_contexts` and worst `error_ratio` read
- [ ] `graphs/service-call-self-edges.jsonl` read — the `OI-34` sizing number
