# Source-Code Metabase

A structured, human-readable knowledge base of the analysed source-code
ecosystem. Designed to be loaded as **context for LLM SAST** so that
cross-repository taint analysis becomes possible — sources in one repo,
sinks in another, internal libraries that act as transparent pass-
throughs to dangerous APIs.

**Canonical field definitions:** [`SCHEMA.md`](./SCHEMA.md)  
**Roadmap / gaps:** [`NEXT_STEPS.md`](./NEXT_STEPS.md)

---

## Why this exists

Conventional SAST tools work one repository at a time and cannot
follow:

1. **Cross-repo SQL injection** — service A constructs a SQL fragment
   and forwards it as a payload to service B which executes it.
2. **Internal-library black boxes** — shared wrappers hide JDBC/JPA sinks.
3. **Cross-repo PII flow** — ingress → queue → store → log → third party.
4. **Crypto agility** — algorithms and keys decided in config or shared libs.
5. **Dangerous request payloads** — e.g. raw SQL accepted on an HTTP
   endpoint (`acme/sql-runner-api` `/query`).

The metabase captures cross-repo facts so an LLM (or human) can reason
about the full path, not a single repo in isolation.

---

## Contents at a glance

### Per-repo (gitignored after generation)

| Path | Content |
|------|---------|
| `repos/<group>/<repo>.md` | Flow-node summary |
| `repos/<group>/<repo>.json` | Flow graph (`schema_version: 2`, `nodes[]`, `edges[]`) |

### Cross-repo catalogues (mostly gitignored)

| Folder / file | Purpose |
|---------------|---------|
| `taint/sql-sources.md` + `.jsonl` | SQL string construction (concat / template) |
| `taint/sql-execution-sinks.md` + `.jsonl` | JDBC/JPA/native execution sinks |
| `taint/file-sinks.md` + `.jsonl` | File-write / archive extract |
| `taint/http-sinks.md` + `.jsonl` | Outbound HTTP client calls |
| `taint/pii-sources.md` + `.jsonl` | PII field declarations (bounded MD + full jsonl) |
| `taint/pii-sinks.md` + `.jsonl` | PII in logs / storage (`field_name` null = generic `.save` etc. without nearby PII token — see `SCHEMA.md`) |
| `taint/crypto-operations.md` + `.jsonl` | Crypto algorithm use |
| `taint/raw-code-payload-endpoints.md` + `.jsonl` | HTTP handlers that accept `sql`/… and reach SQL execution in-file |
| `taint/config-data-stores.md` + `.jsonl` | JDBC/Mongo/Redis/S3 from config |
| `taint/config-security.md` + `.jsonl` | Security-sensitive config keys |
| `taint/config-crypto.md` + `.jsonl` | Cipher suites / signing algorithms from config |
| `graphs/service-call-graph.md` + `service-call-edges.jsonl` | Cross-repo HTTP edges (`http-in` ↔ enriched `http-out`) |
| `graphs/queue-graph.md` + `.jsonl` | Topic producers / consumers |
| `graphs/data-store-graph.md` + `.jsonl` | Config-discovered stores ↔ repos |
| `graphs/payload-endpoint-producers.md` + `.jsonl` | Registered API clients → target services |
| `graphs/pii-lifecycle.md` + `.jsonl` | PII lifecycle stages (Phase 3) |
| `graphs/pii-phone-cross-repo.md` + `.jsonl` | Cross-repo phone hops (queue + HTTP) |
| `ropa/categories-of-personal-data.md` | ROPA Article 30 projection (Phase 3) |
| `conventions/auth-models.md` + `.jsonl` | Per-repo auth cards (Phase 3) |
| `conventions/crypto-agility.md` + `.jsonl` | Per-repo crypto cards (Phase 3) |
| `graphs/traces/<target>.md` | Endpoint traces (`trace.py` / `trace_batch.py`) |

### Hand-authored (committed)

| Path | Purpose |
|------|---------|
| `SCHEMA.md` | Field and node-family definitions |
| `README.md` | This file |
| `NEXT_STEPS.md` | Gaps and follow-ups |
| `internal-libraries/<coord>.md` | Per-library taint tables (hand-curated) |
| `conventions/*.md` | Auth, crypto agility, PII naming (mixed) |

### Scripts (committed)

| Script | Role |
|--------|------|
| `src2sink/build_metabase_v2.py` | Extractor + taint + graph aggregation |
| `src2sink/trace.py` | Bidirectional trace for a target repo/endpoint |
| `src2sink/trace_batch.py` | Batch traces from `raw-code-payload-endpoints.jsonl` |
| `src2sink/known_api_clients.py` | Registry: client library → target service |
| `src2sink/schema.py` | v2 `FlowNode` / `FlowEdge` dataclasses |
| `src2sink/extractors/` | unified, config, http_out, tree-sitter helpers |
| `src2sink/aggregators/` | taint catalogues, graphs, payload producers |

---

## Building the metabase

Prerequisites: source repos cloned to your `--repos-root` path, `uv sync`, corp network.

```sh
# Incremental update — skips repos whose git SHA hasn't changed (~fast after first run)
uv run src2sink-build \
  --repos-root repos --metabase-root metabase \
  --internal-groups-file internal-groups.json --api-clients api-clients.json

# Force full re-extract of all repos (use after downloading new repo snapshots
# that are not git checkouts, or after changing internal-groups/api-clients config)
uv run src2sink-build \
  --repos-root repos --metabase-root metabase --force \
  --internal-groups-file internal-groups.json --api-clients api-clients.json

# Single repo — re-extracts only that repo, but re-aggregates taint/graphs/phase3
# from all existing JSONs so cross-repo outputs stay consistent
uv run src2sink-build \
  --repos-root repos --metabase-root metabase \
  --repo acme/sql-runner-api \
  --internal-groups-file internal-groups.json --api-clients api-clients.json

# Re-aggregate taint + graphs from existing v2 JSONs only (fast, no re-extraction)
uv run src2sink-build \
  --repos-root repos --metabase-root metabase --graphs-only \
  --internal-groups-file internal-groups.json --api-clients api-clients.json

# Phase 3 only (PII lifecycle, ROPA, auth/crypto, cross-repo phone flows)
uv run src2sink-build \
  --repos-root repos --metabase-root metabase --phase3-only \
  --internal-groups-file internal-groups.json --api-clients api-clients.json

# Resolve flagged library-source-map entries from on-disk pom.xml only
# (fast, no re-extraction). Walks every pom.xml under --repos-root, reads each
# groupId/artifactId, and for any mapping still flagged (status pending/ambiguous
# or with an empty clone_path) whose coordinate matches a pom, fills in clone_path
# and sets status: cloned. excluded and already-cloned entries are left alone.
uv run src2sink-build \
  --repos-root repos --metabase-root metabase --fix-source-map

# Tests (Phase 4 regression suite)
uv run pytest tests/ -q
uv run pytest tests/ -q -m fleet   # needs metabase/repos v2 JSONs
```

> **Incremental behaviour:** each re-run compares the repo's current `git HEAD` SHA
> against the SHA stored in the existing per-repo JSON. Repos that haven't changed are
> skipped (`skip  group/name (unchanged)`); only changed repos are re-extracted.
> Repos without a `.git/` directory (bare downloads) are always re-extracted.
> Cross-repo graphs and taint catalogues are always re-aggregated at the end regardless
> of how many repos were skipped, so the metabase stays consistent.
> Use `--force` to bypass the SHA check and re-extract everything.

> **Traces are not run automatically.** `src2sink-build` produces a
> `graphs/traces/INDEX.md` that lists existing trace files and shows
> which endpoints still lack one, but it does not generate traces itself.
> Run `src2sink-trace-batch` (or `src2sink-trace`) as a separate step
> after the build completes.

### Generating traces (separate step)

Traces give a full bidirectional view of a dangerous-payload endpoint:
inbound routes, `raw-code-payload` nodes, SQL sinks, config stores, and
upstream callers. They are written to `graphs/traces/<name>.md`.

**Batch — trace all raw-code-payload endpoints at once (recommended):**

```sh
# First run: generates a report for every endpoint in
# taint/raw-code-payload-endpoints.jsonl
uv run src2sink-trace-batch --metabase-root metabase \
  --internal-groups-file internal-groups.json --api-clients api-clients.json

# Subsequent runs: skip endpoints that already have a report
uv run src2sink-trace-batch --metabase-root metabase \
  --internal-groups-file internal-groups.json --api-clients api-clients.json --skip-existing
```

**Single endpoint:**

```sh
uv run src2sink-trace \
  --metabase-root metabase \
  --target acme/sql-runner-api \
  --path /query \
  --scan-repos repos \
  --internal-groups-file internal-groups.json \
  --api-clients api-clients.json \
  --output metabase/graphs/traces/sql-runner-api-query.md
```

`--scan-repos` is optional; it adds a literal import/URL scan of the
cloned source trees to find callers that are not yet in the graph.

After adding new traces, refresh the index:

```sh
uv run src2sink-build --repos-root repos --metabase-root metabase --graphs-only \
  --internal-groups-file internal-groups.json --api-clients api-clients.json
```

### Fixing flagged internal-library mappings

After the initial scan, `library-source-map.json` may contain entries the
build couldn't resolve — coordinates with `status: pending`/`ambiguous` or an
empty `clone_path` (an internal dependency whose source repo it couldn't
locate). Every normal build already tries to repair these automatically at the
end, but if you clone the missing repos *after* a scan you can re-run just the
fix without re-extracting anything:

```sh
uv run src2sink-build --repos-root repos --metabase-root metabase --fix-source-map
```

This walks every `pom.xml` under `--repos-root`, reads each `groupId` /
`artifactId`, and for any still-flagged mapping whose coordinate matches a pom
it sets `clone_path` to that repo's path (relative to the repos root) and flips
`status` to `cloned`. Matching is by exact `groupId:artifactId`, falling back to
a unique `artifactId`-only match. Entries marked `excluded`, and those already
`cloned` with a `clone_path`, are left untouched. It prints how many entries it
resolved; anything still flagged afterwards has no matching `pom.xml` on disk
(clone the repo, or set `clone_path`/`status` by hand). Once resolved,
`curate_internal_libraries.py` can seed taint tables from that source.

### Register another API client library

Edit `src2sink/known_api_clients.py` (`ApiClientBinding`), then re-run
`src2sink-build` (or `--graphs-only`).

---

## How to use this metabase as LLM context

### Minimum pack for SAST on one repo

1. `repos/<group>/<repo>.md` and `.json` (check `schema_version`).
2. For each **internal dependency**, `internal-libraries/<coord>.md`.
3. Relevant **taint** rows (filter jsonl by `repo` column).
4. **graphs** edges involving that repo (`service-call-edges.jsonl`,
   `payload-endpoint-producers.jsonl`, `queue-graph.jsonl`).

### Dangerous-payload / raw SQL in HTTP body

1. `taint/raw-code-payload-endpoints.md` (sample) + `.jsonl` (full).
2. `graphs/payload-endpoint-producers.md` for known client libraries.
3. `graphs/traces/<service>-<path>.md` if generated (see `graphs/traces/INDEX.md`).
4. Target repo v2 JSON: `family=raw-code-payload`, `http-in`, `sql` sinks.

Use **phone numbers** (not email) as the canonical PII lifecycle example
when illustrating field flow — email is ambient in an email-security
company (`conventions/pii-classification.md`).

### PII and crypto

- PII lifecycle: `graphs/pii-lifecycle.md`, `graphs/pii-phone-cross-repo.md`.
- ROPA draft: `ropa/categories-of-personal-data.md`.
- Field catalogues: `taint/pii-sources.md`, `taint/pii-sinks.md`.
- Crypto: `taint/crypto-operations.md`, `conventions/crypto-agility.md`.

---

## Updating and hand-curation

Per-repo files are regenerated wholesale by `build_metabase_v2.py`.
Put durable notes in `internal-libraries/` or conventions files, not
inside generated output.

---

## Confidence levels

| Level | Meaning |
|-------|---------|
| **high** | Strong pattern (e.g. tree-sitter JDBC sink, explicit import of registered client) |
| **medium** | Heuristic (field name, regex HTTP client) |
| **low** | Weak signal (host-only match, path suffix overlap) |

Service-call and producer-index edges label confidence explicitly.
Treat **low** as “investigate”, not “confirmed”.

---

## Dependencies

v2 requires **tree-sitter** and per-language grammars (via
`uv sync`). See `metabase/docs/phase0-probe.md` if resolution fails.
Do not add public PyPI fallbacks.

---

## See also

- [`docs/metabase-v2-implementation-plan.md`](../docs/metabase-v2-implementation-plan.md) — **phased plan and progress record**.
- Root [`README.md`](../README.md) — clone + triage pipeline.
- [`AGENTS.md`](../AGENTS.md) — contributor / agent quick reference.
- [`SCHEMA.md`](./SCHEMA.md) — complete v2 node/edge vocabulary.
- [`NEXT_STEPS.md`](./NEXT_STEPS.md) — short gap list.
- `/ai-sast-scanner` skill — SAST prompt this metabase feeds.
