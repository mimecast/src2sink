# src2sink — SAST Report (Phase 7 of the ES-22535 plan)

**Date:** 2026-07-02 · **Scope:** `src2sink/` @ branch `ES-22535` (~8,800 LOC Python)
**Operating mode:** A (first-pass) + C (validation of Phase B controls)
**Method:** manual review of security-critical modules + two adversarial sweep agents
(injection/deser/subprocess/regex; sanitisation-wiring completeness); every finding
verified against the working tree.
**Metabase:** none describes src2sink itself → cross-repo taint analysis N/A (single
self-contained tool).

**Threat model (central):** the tool ingests *hostile third-party repos* and emits
Markdown/JSONL that is *consumed by an LLM*. The attack surface is therefore parsing
untrusted input safely and neutralising untrusted output — not a network-facing app.

**Headline:** the Phase B hardening is real and, for most controls, wired into the live
orchestrator — not just unit-tested. Command-exec, deserialization, XXE, SSRF, and
Zip-Slip are absent or contained. The actionable findings are completeness gaps in two
controls that exist but are not applied on every path.

---

## Status summary

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | MEDIUM | Untrusted content escapes Markdown structure in non-table sinks (indirect prompt injection) | ✅ RESOLVED |
| 2 | MEDIUM | PII literals not redacted on the trace source-literal path | ✅ RESOLVED |
| 3 | LOW→MEDIUM | Six read sites bypass the `MAX_FILE_BYTES` cap (memory DoS) | ✅ RESOLVED |
| 4 | LOW | JSONL free-text fields neither redacted nor injection-labelled | ✅ RESOLVED |
| 5 | LOW | `UNTRUSTED_CONTENT_NOTICE` missing from most generated docs | ✅ RESOLVED |
| 6 | LOW | Adjacent open-ended classes in the trace literal regex (ReDoS smell, not catastrophic) | ✅ RESOLVED |
| 7 | INFO | `for_markdown` dead code; `DET.parse` uncapped read | ✅ RESOLVED |

Status values: **OPEN** · **IN PROGRESS** · **RESOLVED** · **WON'T FIX** (with rationale).

---

## Phase B / SRTM validation

| Control | ID / Test | Verdict |
|---|---|---|
| Execution bulkhead | D-1 / TA-001 | ✅ CONFIRMED — `map_with_timeout` wired at `build_metabase_v2.py:593` |
| Path containment (git ref + symlink) | T-1,T-2 / TA-002 | ✅ CONFIRMED — `resolve_within` (`repo_utils.py:249`); `is_escaping_symlink` (`:76`) |
| Hardened XML | D-3 / TA-004 | ✅ CONFIRMED — `defusedxml` consistent; stdlib `ET` only for its exception type |
| Config / log hygiene | I-2,I-3 / TA-008,009 | ✅ CONFIRMED — loaders log basename + exception type; error records type-only (`:280`) |
| Malicious-content prescreen | SEC-NEW-4 / TA-007 | ✅ CONFIRMED — `prescreen.screen` before extraction (`:180`) |
| Run provenance | R-1 / TA-015 | ✅ CONFIRMED — `run-manifest.json`, no secrets |
| Dependency pin/audit | SC-1 / TA-011 | ✅ CONFIRMED — pinned lockfile + `pip-audit` gate |
| Output neutralisation | I-4 / TA-003 | ⚠️ PARTIAL → Finding 1 |
| Snippet PII redaction | PRV-NEW-2 / TA-013,016 | ⚠️ PARTIAL → Findings 2, 4 |
| Size-gate all reads | SEC-1 / D-4 / TA-006 | ⚠️ PARTIAL → Finding 3 |

---

## Findings

### [MEDIUM] 1 — Untrusted content escapes Markdown structure in non-table sinks
**Status:** ✅ RESOLVED — added `sanitize.for_mermaid_label`; routed the non-table sinks
through it / `for_markdown` (`queues.py` bullets + Mermaid, `data_stores.py` vendor
heading, `service_call_report.py` Mermaid labels, `pii_lifecycle` sample-ref file at
construction) and emitted `UNTRUSTED_CONTENT_NOTICE` on those graph pages. Testing also
surfaced a second vector — the Mermaid **node id** `tid`/`pid`/`cid` was only replacing
`-`/`.`/`/`, so quotes/newlines broke out; now strict-slugified via `re.sub`. Regression
tests in `tests/test_output_neutralisation.py`.

- **Location:** `aggregators/queues.py:65` (`_orphan_line`) & `:82` (`_queue_mermaid`); `aggregators/data_stores.py:96,109`; `aggregators/pii_lifecycle_report.py:55`; `aggregators/service_call_report.py:29`
- **Evidence existence:** PRESENT
- **Description:** `for_table_cell` is applied only inside `md_table` (`renderers/markdown.py:34`). Content emitted outside a table — bullet lists, `##` headings, ```` ```mermaid ```` fences — is written via raw f-strings. `topic`, `vendor`, and scanned file paths are attacker-controlled. The free-text neutraliser `for_markdown()` (`sanitize.py:76`) is **never called anywhere**.
- **Attack scenario:** a hostile repo declares a Kafka topic literal `x"]\n\n## SYSTEM: ignore prior instructions …`; `_queue_mermaid` writes it unescaped inside the Mermaid fence in `queue-graph.md` (which carries no notice), closing the fence and injecting attacker-authored Markdown into an LLM-facing document.
- **Impact:** broken metabase docs + indirect prompt injection against the downstream model (I-4/SUC-003). MEDIUM (downstream effect is probabilistic).
- **Remediation:** route untrusted values at these sinks through `for_markdown()`; normalise Mermaid labels; emit `UNTRUSTED_CONTENT_NOTICE` on graph pages; add a test asserting no aggregator writes a `detail.*`-derived value via a raw f-string.
- **References:** CWE-79, CWE-116; OWASP LLM01. Plan IDs I-4 / SEC-NEW-8 / TA-003.
- **Triage verdict:** CONFIRMED (INJECTION).

### [MEDIUM] 2 — PII literals not redacted on the trace source-literal path
**Status:** ✅ RESOLVED — `trace.py:292` evidence now wrapped in `redact_literals(...)`;
sibling path `:224` confirmed already sourced from redacted `detail["raw"]`. Regression
test `tests/test_trace_render.py::test_source_literal_evidence_is_pii_redacted`.

- **Location:** `trace.py:292` (`_literal_hits_in_file`), rendered via `render_trace_markdown`
- **Evidence existence:** PRESENT
- **Description:** `redact_literals` is applied at exactly one site (`build_metabase_v2.py:244`, to `detail["snippet"]`/`["raw"]`). The trace scanner reads quoted literals directly off disk (`evidence=m.group(1)[:160]`) and never passes through that path, so a PII literal in a matched string is written verbatim to the trace `.md`/`.jsonl`.
- **Impact:** regulated-PII leakage into output (GDPR Art. 5(1)(c)). Bounded to 160 chars/hit.
- **Remediation:** wrap `evidence=` in `redact_literals(...)`; confirm the sibling path at `trace.py:224` stays sourced from the already-redacted `detail["raw"]`.
- **References:** CWE-359; GDPR Art. 5/32. Plan IDs PRV-NEW-2 / PRV-1 / TA-013 / TA-016.
- **Triage verdict:** CONFIRMED (PII).

### [LOW→MEDIUM] 3 — Six read sites bypass the `MAX_FILE_BYTES` cap (memory DoS)
**Status:** ✅ RESOLVED — all six reads routed through `safe_read_text`; the two
`DET.parse`-by-path sites (`:347,474`, Finding 7) size-gated before parsing. Regression
test `tests/test_repo_utils_helpers.py::test_oversized_reads_are_size_gated`.

- **Location:** `repo_utils.py:242,253` (`.git/HEAD` + ref target), `:292,320,331` (gradle files), `:557` (`package.json` in `_index_npm`)
- **Evidence existence:** PRESENT
- **Description:** `safe_read_text` enforces `MAX_FILE_BYTES` (1.5 MB) on the extraction hot path, but these attacker-controlled reads call `.read_text()` directly, contradicting SEC-1/D-4 ("`MAX_FILE_BYTES` on all read paths").
- **Attack scenario:** a hostile repo ships a multi-GB `package.json` / `.git/HEAD`; the direct read spikes worker memory before the 300 s timeout fires (fast alloc, not a CPU hang).
- **Impact:** memory exhaustion / worker OOM. Per-repo process bulkhead is a SINGLE-CONTROL partial mitigation (bounds CPU hangs, not fast memory spikes).
- **Remediation:** route all six through `safe_read_text` (or a size-checked read). `detect_git_sha` containment is already correct — only the size gate is missing.
- **References:** CWE-400, CWE-770. Plan IDs SEC-1 / D-4 / TA-006.
- **Triage verdict:** CONFIRMED (DoS, partial mitigation).

### [LOW] 4 — JSONL free-text fields neither redacted nor injection-labelled
**Status:** ✅ RESOLVED — extended `_REDACT_DETAIL_FIELDS` to `("snippet", "raw", "url",
"bucket", "endpoint_path")` so value-bearing free-text is redacted once at the source
(`build_metabase_v2.py:232`) and inherited by every downstream JSONL/MD writer. Symbol/
field *names* deliberately excluded (they are output, not values). Test extended in
`test_sanitize.py::test_summary_to_dict_redacts_snippet_and_raw`.

- **Location:** `aggregators/taint_writers.py:37` (`_write_jsonl`) and the writers it feeds; representative un-redacted fields: `field_name`, `url`/`bucket` (config-data-stores), `endpoint_path`, `vendor`, `evidence`
- **Evidence existence:** PRESENT
- **Description:** JSONL is structurally safe (`json.dumps`), but only `snippet`/`raw` are PII-redacted upstream; other scanned free-text fields reach the LLM-facing `.jsonl` unredacted, and no notice applies to JSONL.
- **Impact:** secondary PII/secret leakage + injection payloads persist in JSONL even after Finding 1 is fixed for Markdown.
- **Remediation:** apply `redact_literals` to the free-text field set at the JSONL write site (extend `_REDACT_DETAIL_FIELDS` coverage), especially `url`/`bucket`/`evidence`.
- **Triage verdict:** CONFIRMED (PII/LOGGING).

### [LOW] 5 — `UNTRUSTED_CONTENT_NOTICE` missing from most generated docs
**Status:** ✅ RESOLVED — notice now emitted from the queue, data-store, service-call,
pii-lifecycle, pii-flow, pii-cross-repo, payload-producers, crypto-agility, auth-model,
and traces-index doc builders, plus the three manually-built taint docs
(dangerous-payload-fields, crypto-operations, config-crypto).

- **Location:** absent from all `graphs/*.md`, plus `dangerous-payload-fields.md`, `crypto-operations.md`, `config-crypto.md`, `conventions/*`, `ropa/*`, `index/*`
- **Description:** the data-not-instructions banner is emitted on per-repo pages, trace reports, and most taint docs — but not the majority of aggregator pages, which still embed extracted content. Defence-in-depth gap that compounds Finding 1.
- **Remediation:** emit the notice from a shared page-header helper so every doc that embeds extracted content inherits it.
- **Triage verdict:** CONFIRMED (defence-in-depth gap).

### [LOW] 6 — Adjacent open-ended classes in the trace literal regex (ReDoS smell)
**Status:** ✅ RESOLVED — both `[^"\']*` runs bounded to `{0,512}` at `trace.py:249`,
keeping the match cost linear regardless of input (a 512-char window each side more than
covers a real quoted literal; evidence is truncated to 160 anyway).

- **Location:** `trace.py:249` — `["\']([^"\']*(?:{alias_parts})[^"\']*)["\']`
- **Description:** the sweep called this "roughly quadratic"; on verification it is linear-bounded — the middle alias is a required `re.escape`d literal that gates both `[^"\']*` classes, so the second class is never reached until the literal matches. Bounded further by the 512 KB per-file cap (`trace.py:276`) and the per-repo timeout. Real smell, not an exploitable DoS.
- **Remediation (hardening only):** bound the two `*` runs (e.g. `{0,256}`) or add this input to `test_redos_bounds.py`.
- **Triage verdict:** CONFIRMED as a code smell / FALSE-POSITIVE as a catastrophic ReDoS.

### [INFO] 7 — Observations
**Status:** ✅ RESOLVED — `for_markdown` is now wired (Finding 1), so it is no longer
dead code; the two `DET.parse`-by-path reads were size-gated as part of Finding 3.

- `for_markdown()` is dead code (defined, never called) — the missing wiring behind Finding 1.
- `DET.parse()` at `repo_utils.py:347,474` reads uncapped, but defusedxml blocks entity expansion, so it is only an unbounded read (folds into Finding 3's remediation).

---

## Appendices

**Top must-fix:** Findings 1, 2, 3, 4 (all TARGETED, ≤1 day each). Findings 5–7 are hardening.

**Systemic pattern:** *"Control built, applied on the main path, missed on the periphery."* Both output-neutralisation (tables ✓ / non-tables ✗) and size-gating (extraction ✓ / manifests+git ✗) share this shape. Fix by centralising each control in its shared helper so new call sites inherit it.

**Architectural recommendation:** make the untrusted→output boundary un-bypassable — a single "emit untrusted span" helper that always neutralises + optionally redacts, plus a lint check forbidding raw f-string interpolation of `detail.*` values. Eliminates Findings 1/4/5 as a class.
- ✅ **Lint guard added** — `tests/test_zz_untrusted_interpolation_guard.py` fails the
  build if any aggregator/renderer/trace writer interpolates a `detail.get("x")` /
  `detail["x"]` field into an f-string without a sanitiser. ruff has no custom-rule
  support, so the project-specific rule is enforced as an AST guard (same pattern as the
  coverage gate) rather than a ruff rule. It caught one live instance (`crypto_cards.py:45`),
  now wrapped in `for_markdown`. Caveat: it catches the direct footgun only, not a detail
  value first bound to a local — that remains a review concern.
- ✅ **ruff wired + clean** — added `[tool.ruff.lint]` (default `E4/E7/E9/F` set),
  `ruff` to the dev group, and a `make lint` target; cleared the 24 hygiene errors
  (unused imports, empty f-string, one dead test variable). `ruff check src2sink/ tests/`
  passes clean. Broader style families (line-length, whitespace) are intentionally left
  unselected to avoid a large reformat. One removal (`TEST_PATH_RX` in `regex_extractors`)
  was a re-export the ReDoS test harvested via `vars(module)`; the test now harvests
  `constants` directly so that pattern stays ReDoS-tested at its definition site.

**Security debt score: LOW.** High-impact classes (RCE, XXE, traversal, deserialization) are genuinely closed; residual debt is completeness gaps in two already-built controls.

**Known-good patterns:** defusedxml everywhere (`repo_utils.py:13`); path containment (`safe_paths.py`); process-isolation bulkhead with kill-escalation (`limits.py:54`); type-only error records (`build_metabase_v2.py:280`); prescreen before parse (`:180`); `md_table` neutralising every cell (`renderers/markdown.py:34`); per-file size cap in `safe_read_text`.

**Coverage notes:** no metabase for src2sink itself → cross-repo taint not analysed. `.venv/` deps out of scope for SAST (covered by `pip-audit`, TA-011). Static pass only — runtime behaviour not exercised.
