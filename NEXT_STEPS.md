# Next steps — metabase

Short gap list. **Full plan and progress record:**
[`docs/metabase-v2-implementation-plan.md`](../docs/metabase-v2-implementation-plan.md).

For commands and artefact locations see [`README.md`](./README.md) and
[`SCHEMA.md`](./SCHEMA.md).

## Current status (2026-05-18)

| Phase | State |
|-------|--------|
| 0 — Bootstrap / tree-sitter | **Done** |
| 1 — v2 extractors + fleet (~355 repos) | **Done** |
| 2 — Graphs + `trace.py` + `trace_batch.py` | **Done** |
| 3 — PII lifecycle, ROPA, auth/crypto, cross-repo PII | **Done** |
| 4 — Fixtures, snapshots, taint caps, fleet regression | **Done** |
| Post–4 — OpenAPI/Helm, index, pii-flow, trace index, PII noise | **Done** |

**Tests:** `uv run pytest tests/ -q` (49 tests; fleet marker optional).

## Remaining (optional)

1. **Expand `trace_batch`** beyond the 233 catalogue-backed reports (673 raw-payload
   endpoints in `taint/raw-code-payload-endpoints.jsonl` — batch targets are a subset).
2. **Hand-review** auto-curated internal-library rows (`curate_internal_libraries.py`)
   and replace heuristic `propagator/opaque` with confirmed sink roles.
3. **More `known_api_clients`** bindings for client JARs → owning services.

## Critical gap (unchanged)

Internal libraries remain **black boxes** until
`internal-libraries/<coord>.md` taint rows are filled or library source
is cloned into `repos/`. Auto-curation seeds tables from Java public methods
when source is available; human review still required.
