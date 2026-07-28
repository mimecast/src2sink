# Security and Privacy by Design Analysis: src2sink

**Date:** 2026-07-01
**Version:** 1.0
**Frameworks:** STRIDE · LINDDUN · OWASP Top 10 · GDPR / Privacy by Design
**Regulatory scope:** GDPR (in scope — Art. 5, 17, 30, 32). CRA (assessed — **out of scope**, see App. E). NIS2 (assessed — **indirect**, App. E). PSTI/HIPAA/PCI-DSS: not applicable.

> This is a **gap analysis of an existing system**, not a greenfield design. It
> builds on [architecture.md](architecture.md) and [threat-model.md](threat-model.md)
> and reuses their finding IDs (`D-1`, `T-1`, `I-4`, `P-1`, …) and the
> architecture's surfaced requirements (`SEC-NEW-1..6`). It produces the
> requirement set + SRTM that the [implementation plan](implementation-plan.md)
> and the test-engineering phase consume.

---

## Executive Summary

`src2sink` is an offline batch CLI that scans a fleet of (mostly first-party,
occasionally malware-bearing) repositories and produces the "metabase" — a
concentrated map of security findings, cross-service topology, PII field
locations, and a GDPR ROPA projection. It runs unattended in CI.

**Most significant security risks:** the tool ingests untrusted content with
**no execution timeout** (`D-1`, High) — one crafted file hangs a CI job — and a
**git-HEAD path-traversal** (`T-1`, High) that, combined with symlink-file reads
(`T-2`) and log leakage (`I-2`), lets a malicious repo turn the scanner into a
**read-and-publish gadget** for CI secrets (`I-1`, High). Manifest XML uses the
unsafe `xml.etree` (`D-3`). Reassurance: the tool **never executes** scanned
code, so these are DoS / disclosure / poisoning risks, not RCE.

**Most significant privacy risks:** the metabase is a durable **cross-fleet map
of where personal data lives** with **no retention or erasure policy** (`P-1`,
GDPR Art. 5(1)(e)/17) and code snippets that may incidentally capture literal
PII from test fixtures. The output as a whole is a target-selection map (`P-2`).

**Data sensitivity ruling (the item you asked me to assess):**
- **Metabase outputs → CONFIDENTIAL / RESTRICTED.** They aggregate exploitable
  weaknesses + internal topology + personal-data locations across the fleet —
  an attacker's treasure map. Must be access-controlled and encrypted at rest.
- **`api-clients.json` → SECRET.** Internal service topology; must be a CI
  **secret file**, never a plaintext build argument or committed.

**Top 5 priorities for engineering (in order):**
1. `D-1` — add a per-repo/per-file execution timeout (bulkhead) + files cap.
2. `T-1`/`T-2` — contain the git-HEAD ref path; skip/contain symlinked files.
3. `I-4` — neutralise untrusted content in LLM-facing outputs (indirect prompt
   injection carrier).
4. `D-3` — harden manifest XML parsing (`defusedxml`).
5. `P-1`/`P-2` + handling — classify outputs Restricted; snippet literal
   redaction; retention policy.

---

## 1. Assumptions and Context

Answers to the Phase 1 intake (from stakeholder input + code verification):

1. **What:** batch static-analysis pipeline producing the metabase (see
   architecture.md §1).
2. **Actors:** the operator / CI job (trusted); authors of scanned repos
   (untrusted — may be malicious); downstream human + LLM consumers of the
   metabase; no end-users / no interactive sessions.
3. **Data:** untrusted source code; sensitive config (`api-clients.json`);
   outputs containing findings + topology + **PII field references** +
   classifications + a **ROPA projection**. **No raw PII values or credential
   values are written** (verified — only field *names*/keys, snippets truncated
   ~100 chars).
4. **Deployment:** CI/automation + analyst workstations; internal; single-user
   batch; no network service exposed.
5. **Compliance:** GDPR (the org processes EU personal data and this tool builds
   a map of it). CRA/NIS2 assessed in Appendix E.
6. **EU market:** the tool is **internal**, not sold/distributed → CRA out of
   scope.
7. **Downloadable/embedded software:** internal CLI, not a placed-on-market
   product.

**Assumptions:** (A1) repos are cloned by a trusted prior step; (A2) the CI
identity running the tool is (or should be) least-privilege; (A3) the metabase
output store is org-internal; (A4) Python ≥3.14 runtime (affects symlink
defaults).

---

## 2. Requirements Classification and Gap Analysis

### Existing (implicit) requirements, classified

| ID | Requirement | Type |
|---|---|---|
| FR-1 | Discover repos under `repos/<group>/<repo>` and extract flow nodes/edges per file | FR |
| FR-2 | Aggregate cross-repo taint catalogs, service/queue/data graphs, PII lifecycle, ROPA | FR |
| FR-3 | Incrementally skip repos whose git SHA is unchanged | FR |
| FR-4 | Load optional `api-clients.json` / `internal-groups.json` config | FR |
| FR-5 | Emit machine- and human-readable artifacts (JSON/JSONL/Markdown) | FR |
| SEC-1 | Bound single-file memory via `MAX_FILE_BYTES` on the main read path | SEC |
| SEC-2 | Isolate per-repo failures (one bad repo must not abort the run) | SEC |
| SEC-3 | Keep `api-clients.json` out of version control (gitignored) | SEC |
| PRV-1 | Do not write raw PII values or credential values to outputs | PRV |
| PERF-1 | Parallelise extraction across CPUs (multiprocessing pool) | PERF |

### Gap analysis — missing controls (each becomes a `[NEW]` requirement)

**Security gaps**
- **No execution timeout** per file/parse/worker → `SEC-NEW-1` (carries arch req; threat `D-1`).
- **No path containment** for reads derived from untrusted content (git ref, symlink targets) → `SEC-NEW-2` (`T-1`,`T-2`).
- **Unsafe XML parsing** of manifests → `SEC-NEW-3` (`D-3`).
- **No malicious-content pre-screening / quarantine** → `SEC-NEW-4` (stakeholder requirement).
- **No CI log hygiene** (error strings / paths / config) → `SEC-NEW-5b` (`I-2`); and **no output/config sensitivity classification** → `SEC-NEW-5` (`P-2`,`I-1`).
- **No observability of config load** (silent-empty) → `SEC-NEW-6` (`I-3`).
- **Unbounded content regexes** (ReDoS) → `SEC-NEW-7` (`D-2`).
- **Untrusted content passed verbatim to LLM-facing outputs** → `SEC-NEW-8` (`I-4`).
- **No dependency / supply-chain scanning or pinning** → `SEC-NEW-9` (`SC-1`).
- **No decoded-size / files-per-repo caps** → `SEC-NEW-10` (`D-4`).
- **No least-privilege / sandbox guidance for CI** → `SEC-NEW-11` (`E-1`).

**Privacy gaps**
- **No retention/erasure policy** for the metabase PII map → `PRV-NEW-1` (`P-1`, GDPR Art. 5(1)(e)/17).
- **Snippets may capture literal PII** → `PRV-NEW-2` (`P-1`).
- **No run provenance / records of processing** → `COMP-NEW-1` (`R-1`, GDPR Art. 30).

**Compliance gaps** — see Appendix E.

---

## 3. Use Cases

```
UC-001: Full fleet build
Actor: CI job / analyst
Goal: Produce/refresh the metabase from repos/
Preconditions: repos/ populated by a trusted clone step; optional config present
Main Flow:
  1. Parse args; load internal-groups + api-clients config
  2. Discover repos; fan out per-repo extraction across mp.Pool workers
  3. Each worker reads files (untrusted), parses (regex + tree-sitter), emits nodes
  4. Write per-repo JSON/MD; aggregate cross-repo catalogs/graphs; write outputs
Postconditions: metabase/ refreshed
Data involved: reads untrusted source + sensitive config; writes findings+topology+PII refs
Trust boundary crossings: TB1 (repo→tool), TB2 (config→tool), TB3 (tool→outputs/logs/LLM)

UC-002: Incremental build
Actor: CI job
Goal: Re-scan only repos whose git SHA changed
Trust boundary crossings: TB1 — includes reading .git/HEAD (see T-1)

UC-003: Endpoint trace
Actor: analyst
Goal: Build a bidirectional trace for a target repo/endpoint
Data involved: reads metabase + repos; builds dynamic regex from repo/config values
Trust boundary crossings: TB1, TB2, TB3
```

---

## 4. Threat Analysis

### 4.1 Security threats (STRIDE)

Full STRIDE analysis lives in [threat-model.md](threat-model.md) (risk register
with `D-1`,`T-1`,`T-2`,`D-2`,`D-3`,`I-1..I-4`,`S-1`,`SC-1`,`E-1`,`R-1`). This
document does not restate them; the SRTM (§8) maps each to a requirement, a
control, and a test. Key: `D-1` (no timeout, High), `T-1`+`I-1` (traversal→
exfiltration, High), `I-4` (indirect prompt injection, High).

### 4.2 Privacy threats (LINDDUN)

```
PT-001: Cross-fleet linkability of personal-data locations
LINDDUN: Linkability / Detectability
Affected UC: UC-001
Description: The metabase links, across all repos, which fields carry which PII
  categories and where they flow (pii-lifecycle, ropa). It is a single artifact
  from which the fleet-wide personal-data footprint is derivable.
Affected personal data: PII field references/classifications (not values)
Affected data subjects: data subjects of all systems in the fleet (indirectly)
Likelihood: High (by design)  Impact: Medium (references, not values, but a map)
GDPR relevance: Art. 5(1)(c) minimisation, Art. 32 security of processing
```
```
PT-002: Incidental disclosure of literal PII in code snippets
LINDDUN: Disclosure of information
Affected UC: UC-001
Description: taint/*.md snippets (~100 chars) around a matched field can capture
  a literal value present in source — most likely from test fixtures containing
  sample SSNs/emails/phone numbers.
Affected personal data: any literal PII in scanned source
Likelihood: Medium  Impact: Medium
GDPR relevance: Art. 5(1)(c), Art. 32
```
```
PT-003: Non-compliance — no retention / erasure / records of processing
LINDDUN: Non-compliance / Unawareness
Affected UC: UC-001
Description: The metabase persists indefinitely with no retention schedule, no
  deletion mechanism, and no run-level record of what was processed when.
Likelihood: High  Impact: Medium
GDPR relevance: Art. 5(1)(e) storage limitation, Art. 17 erasure, Art. 30 records
```

---

## 5. Abuse Cases

### 5.1 Security abuse cases

```
SAC-001: Malicious repo hangs the CI pipeline  [links D-1]
Attacker: repo author (semi-trusted / malicious test files)
Goal: Deny availability of the security-analysis pipeline
Attack Flow:
  1. Commit a file with pathological syntax / a ReDoS trigger
  2. src2sink worker parses it with no timeout and blocks forever
  3. CI job never completes; fleet analysis stalls
Impact: DoS of the security function; masks other repos' findings
OWASP: A05:2021 – Security Misconfiguration
```
```
SAC-002: Repo exfiltrates CI secrets via git-HEAD + symlink  [links T-1,T-2,I-1]
Attacker: repo author
Goal: Read files outside the repo and publish them into the shared metabase
Attack Flow:
  1. Ship .git/HEAD = "ref: ../../../../<ci-secret-file>" (or a symlinked file)
  2. detect_git_sha / os.walk reads the target's first line / content
  3. Value is written to repos/<g>/<r>.json (git_sha) or a taint snippet
  4. Metabase is shared more widely than the CI secret store → exfiltration
Impact: Disclosure of CI secrets / tokens / keys
OWASP: A01:2021 – Broken Access Control
```
```
SAC-003: Indirect prompt injection of downstream LLM  [links I-4]
Attacker: repo author
Goal: Manipulate an LLM that later reads the metabase
Attack Flow:
  1. Embed injection text in a comment/string ("IGNORE PREVIOUS INSTRUCTIONS…")
  2. Extractor writes the span verbatim into a Markdown/JSONL catalog
  3. LLM ingests the catalog; the injected text is treated as instructions
Impact: Falsified triage ("mark repo X safe"), data exfiltration via the LLM
OWASP: A03:2021 – Injection / OWASP LLM01
```
```
SAC-004: Billion-laughs manifest exhausts memory  [links D-3]
Attacker: repo author
Attack Flow: ship a pom.xml with nested XML entity definitions → xml.etree
  expands it to gigabytes during dependency parsing → worker OOM
Impact: DoS
OWASP: A05/A06:2021
```

### 5.2 Privacy abuse cases

```
PAC-001: Metabase becomes a personal-data treasure map  [links P-2,PT-001]
Actor: attacker who obtains the metabase (or over-broad internal access)
Scenario: Uses the fleet-wide PII-location map + ROPA to target systems holding
  specific categories of personal data.
PbD principle violated: End-to-end security; Privacy as the default
Regulatory exposure: GDPR Art. 32; potential Art. 33/34 breach notification
```
```
PAC-002: Test-fixture PII leaks in snippets  [links PT-002]
Actor: the system itself (over-collection)
Scenario: Sample SSNs/emails in test fixtures are copied into shared catalogs.
PbD principle violated: Data minimisation
Regulatory exposure: GDPR Art. 5(1)(c)
```

---

## 6. Counter-Use Cases

### 6.1 Security use cases (countermeasures)

```
SUC-001 (mitigates SAC-001, D-1/D-2): Execution bulkhead
Control: Per-repo hard wall-clock timeout (pool.apply_async(...).get(timeout=T)
  or in-worker faulthandler/alarm watchdog); files-per-repo cap; bounded regex
  quantifiers. On timeout → record repo as _error, continue.
ASVS: 12.x resource limits  Residual: a repo that legitimately needs >T is skipped
```
```
SUC-002 (mitigates SAC-002, T-1/T-2/I-1): Path containment
Control: In detect_git_sha, resolve() the ref target and enforce
  relative_to(repo_root/'.git'); accept only 40/64-hex SHAs. In file iteration,
  skip symlinks or require resolved target within repo_root.
ASVS: 12.3 file path validation  Residual: none material
```
```
SUC-003 (mitigates SAC-003, I-4): Untrusted-content neutralisation at TB3
Control: Escape/fence extracted spans in Markdown; strip control chars; wrap in
  a clearly delimited "UNTRUSTED EXTRACTED CONTENT" region. Deterministic, outside
  any downstream model. Residual: an LLM may still be mis-prompted if it ignores
  delimiters — documented as a downstream consumer responsibility.
```
```
SUC-004 (mitigates SAC-004, D-3): Hardened XML
Control: Parse pom.xml/*.csproj with defusedxml (or reject DOCTYPE/ENTITY).
ASVS: 5.5 deserialization  Residual: none material
```
```
SUC-005 (mitigates I-2/I-3): Log hygiene + fail-loud-but-safe config load
Control: Error records carry exception type + repo id, not raw messages/paths;
  log "loaded N bindings" / "WARN: api-clients.json parse failed (<type>)"
  without echoing contents; document config as a CI secret file.
ASVS: 7.1 log content  Residual: low
```
```
SUC-006 (mitigates SAC-002 environmentally, E-1): Least-privilege CI
Control: Run under a minimal CI identity with no production-secret access beyond
  api-clients.json; read-only mount of repos/; no root. Residual: depends on CI config.
```
```
SUC-007 (mitigates SC-1): Dependency hygiene
Control: Pin+hash tree-sitter + grammars; run pip-audit/uv audit in CI; review
  grammar provenance. Residual: zero-day in a grammar.
```

### 6.2 Privacy use cases (controls)

```
PUC-001 (mitigates PAC-001, P-2): Output classified Restricted + protected
Control: Classify metabase CONFIDENTIAL/RESTRICTED; access-control + encrypt at
  rest; restrict who can read it. PbD: Privacy as default. GDPR Art. 32.
```
```
PUC-002 (mitigates PAC-002, PT-002): Snippet literal redaction
Control: Mask quoted-literal and long digit runs inside snippets before writing.
  PbD: Data minimisation. GDPR Art. 5(1)(c).
```
```
PUC-003 (mitigates PT-003): Retention + provenance
Control: Define/enforce a metabase retention window; emit run-manifest.json
  (tool version, args-minus-secrets, per-repo SHA, UTC ts, counts).
  PbD: Visibility/transparency. GDPR Art. 5(1)(e), Art. 30.
```

---

## 7. Refined Requirements (superset of §2)

**Functional:** FR-1 … FR-5 (unchanged).

**Security (existing + new):**
- SEC-1, SEC-2, SEC-3 (existing)
- **SEC-NEW-1** [from D-1/SAC-001] Bound per-repo/per-file/parse execution (timeout bulkhead) + files-per-repo cap.
- **SEC-NEW-2** [from T-1/T-2/SAC-002] Contain all reads derived from untrusted content (git ref, symlinks).
- **SEC-NEW-3** [from D-3/SAC-004] Hardened XML parsing for manifests.
- **SEC-NEW-4** [stakeholder] Pre-screen scanned content for indicators; quarantine/skip-and-record suspicious files.
- **SEC-NEW-5** [from P-2/I-1] Classify + protect outputs (Restricted) and `api-clients.json` (Secret).
- **SEC-NEW-5b** [from I-2] CI log hygiene (sanitised errors, no config echo).
- **SEC-NEW-6** [from I-3] Observable, fail-loud-but-safe config loading.
- **SEC-NEW-7** [from D-2] Bound content-regex quantifiers; ReDoS-resistant.
- **SEC-NEW-8** [from I-4/SAC-003] Neutralise untrusted content in LLM-facing outputs.
- **SEC-NEW-9** [from SC-1] Pin+hash dependencies; CI dependency audit.
- **SEC-NEW-10** [from D-4] Size-gate all reads; cap decoded length + files-per-repo.
- **SEC-NEW-11** [from E-1] Least-privilege CI identity + filesystem sandbox (documented).

**Privacy (existing + new):**
- PRV-1 (existing)
- **PRV-NEW-1** [from P-1/PT-003] Metabase retention + erasure policy.
- **PRV-NEW-2** [from P-1/PT-002] Redact literal PII in snippets.

**Compliance:**
- **COMP-NEW-1** [from R-1/PT-003] Run provenance record (GDPR Art. 30 support).
- **COMP-NEW-2** [App. E] Maintain an SBOM + vulnerability-report channel (good practice; CRA-aligned even though out of scope).

**Performance:** PERF-1 (unchanged); note SEC-NEW-1 timeout must not materially regress throughput for benign repos.

---

## 8. Security Requirements Traceability Matrix

| Req ID | Requirement (brief) | Type | Use Case | Threat / Abuse | Control | Test ID | Test Description | Priority |
|---|---|---|---|---|---|---|---|---|
| SEC-NEW-1 | Execution timeout + files cap | SEC | UC-001/2 | D-1 / SAC-001 | SUC-001 | TA-001 | Repo with a hang/ReDoS file is skipped as `_error` within T; run completes | Critical |
| SEC-NEW-2 | Path containment (git ref, symlinks) | SEC | UC-002/1 | T-1,T-2 / SAC-002 | SUC-002 | TA-002 | Crafted `.git/HEAD` ref and symlinked file do not read outside repo | Critical |
| SEC-NEW-8 | Neutralise untrusted output content | SEC | UC-001 | I-4 / SAC-003 | SUC-003 | TA-003 | Injection string in source is escaped/fenced in Markdown output | High |
| SEC-NEW-3 | Hardened manifest XML | SEC | UC-001 | D-3 / SAC-004 | SUC-004 | TA-004 | Billion-laughs pom.xml does not expand/OOM; parse rejected safely | High |
| SEC-NEW-7 | Bounded content regexes | SEC | UC-001 | D-2 | SUC-001 | TA-005 | Pathological input matched in bounded time (watchdog asserts) | High |
| SEC-NEW-10 | Size-gate all reads; caps | SEC | UC-001 | D-4 | SUC-001 | TA-006 | Oversized/expanding manifest and >cap file count handled without OOM | Medium |
| SEC-NEW-4 | Malicious-content pre-screen | SEC | UC-001 | (stakeholder) | (new prescreen) | TA-007 | Flagged file is quarantined/skipped and recorded, not parsed | High |
| SEC-NEW-6 | Fail-loud-but-safe config load | SEC | UC-001 | I-3 | SUC-005 | TA-008 | Malformed api-clients.json → warning + 0 bindings, no content echoed | Medium |
| SEC-NEW-5b | CI log hygiene | SEC | UC-001 | I-2 | SUC-005 | TA-009 | Error record contains type+repo id, not path/message/secret | Medium |
| SEC-NEW-5 | Output/config sensitivity handling | SEC | UC-001 | I-1,P-2 | PUC-001 | TA-010 | Docs/CI enforce Restricted output store + secret-file config (audit) | High |
| SEC-NEW-9 | Dependency pin/hash + audit | SEC | build | SC-1 | SUC-007 | TA-011 | CI runs pip-audit; lockfile hashes present | Medium |
| SEC-NEW-11 | Least-privilege CI + sandbox | SEC | UC-001 | E-1 | SUC-006 | TA-012 | Documented CI identity + read-only repos mount (audit) | Medium |
| PRV-NEW-2 | Redact literal PII in snippets | PRV | UC-001 | PT-002 / PAC-002 | PUC-002 | TA-013 | Snippet with a sample SSN/email is masked in output | High |
| PRV-NEW-1 | Metabase retention/erasure | PRV | UC-001 | PT-003 / PAC-001 | PUC-003 | TA-014 | Retention doc exists; deletion procedure verified on a sample | Medium |
| COMP-NEW-1 | Run provenance record | COMP | UC-001 | R-1 / PT-003 | PUC-003 | TA-015 | run-manifest.json emitted with version/SHAs/ts/counts, no secrets | Medium |
| SEC-1 | MAX_FILE_BYTES on all read paths | SEC | UC-001 | D-4 | SUC-001 | TA-006 | (regression) large file skipped on every read path | Medium |
| SEC-2 | Per-repo failure isolation | SEC | UC-001 | D-1 | SUC-001 | TA-001 | One bad repo does not abort the run | High |
| PRV-1 | No raw PII/credential values written | PRV | UC-001 | PT-002 | PUC-002 | TA-016 | Output scan asserts no value-shaped secrets/PII literals | High |

---

## 9. Test Artifacts

> These `TA-xxx` are the security/privacy test specifications the
> [software-test-engineer phase](implementation-plan.md) will implement
> (alongside the general coverage push to >80%). All must run **single-process,
> tiny fixtures, with a watchdog timeout** (per the lesson already codified in
> `tests/test_source_map_fix.py`).

### 9.1 Functional security tests
- **TA-001 (Bulkhead):** fixture repo containing a synthetic hang/ReDoS file; assert the repo is recorded `_error` within timeout T and the overall run completes; benign repos still processed. *Unit/integration, High automation.*
- **TA-004 (Hardened XML):** `pom.xml` with a small nested-entity payload; assert parse does not expand (memory bound) and returns no deps / safe error. *Unit, High.*
- **TA-006 (Size/caps):** oversized file + UTF-16 expanding file + >cap file count; assert skipped/capped without OOM. *Unit, High.*
- **TA-008 (Config load):** malformed / non-dict / missing `api-clients.json`; assert 0 bindings + a warning emitted, no file contents in the message. *Unit, High.*
- **TA-011 (Deps):** CI check that a hash-pinned lockfile exists and `pip-audit` passes. *CI, High.*
- **TA-015 (Provenance):** assert `run-manifest.json` fields present and secret-free. *Unit, High.*

### 9.2 Security attack tests (simulate abuse cases)
- **TA-002 (Path traversal — SAC-002):** fixture with `.git/HEAD` = `ref: ../../../<tmp secret>` and a symlinked file → assert the secret is **not** read into `git_sha`/snippets. *Automated, High.*
- **TA-003 (Prompt injection — SAC-003):** source containing an injection string → assert it is escaped/fenced (not verbatim) in Markdown/JSONL. *Automated, High.*
- **TA-005 (ReDoS — D-2):** craft inputs targeting each greedy pattern; assert bounded match time under the watchdog. *Automated, High.*
- **TA-007 (Pre-screen — SEC-NEW-4):** file with configured indicator → assert quarantined/skipped and recorded, never handed to extractors. *Automated, Medium.*

### 9.3 Privacy verification tests
- **TA-013 (Snippet redaction — PT-002):** source with sample SSN/email/phone literal near a PII field → assert the literal is masked in output. *Automated, High.*
- **TA-016 (No values written — PRV-1):** scan a fixture with credentials/PII values → grep all outputs; assert only field *names*/classifications, no value-shaped strings. *Automated, High.*
- **TA-014 (Retention — PRV-NEW-1):** verify retention doc + a deletion procedure that removes a repo's artifacts. *Manual/audit + partial automation, Medium.*

### 9.4 Penetration testing scenarios
- **TA-010/TA-012 (Environment audit):** review that the metabase store is access-controlled + encrypted at rest, `api-clients.json` is a CI secret file, and the tool runs under a least-privilege identity with a read-only `repos/` mount. *Manual audit.*
- **Malicious-repo red-team:** craft a single repo attempting D-1, T-1, T-2, D-3, I-4 simultaneously; confirm none succeed and the run completes with the repo flagged. *Manual/automated harness.*

---

## Appendix E: Regulatory Compliance Summary

**GDPR — IN SCOPE.** The metabase builds a cross-fleet map of personal-data
locations and a ROPA projection. Relevant: Art. 5(1)(c) minimisation (PT-002),
Art. 5(1)(e) storage limitation + Art. 17 erasure (PRV-NEW-1), Art. 30 records
(COMP-NEW-1), Art. 32 security of processing (SEC-NEW-5, PUC-001), Art. 33/34
breach notification if the metabase leaks (PAC-001). Note: the tool processes
personal-data *references*, not values — but the aggregate is still an asset
requiring Art. 32 protection.

**EU CRA — OUT OF SCOPE (documented, not skipped).** CRA applies to products
with digital elements *placed on the EU market*. `src2sink` is internal-use
tooling, not sold or distributed. If that changes (e.g. distributed to
customers), CRA would apply and would additionally class a security-analysis
tool near the "important" tier — re-assess then. Regardless, **COMP-NEW-2**
recommends adopting CRA-aligned good practice now: maintain an **SBOM**
(supports SEC-NEW-9) and a **vulnerability-report channel**, because it is cheap
insurance and improves the tool's own supply-chain posture.

**NIS2 — INDIRECT.** If the operating organisation is an essential/important
entity, NIS2 obligations (risk management, supply-chain security, MFA,
incident handling) apply to the *organisation*, and this tool is part of its
security tooling/supply chain rather than a regulated service itself. Practical
implication: SEC-NEW-9 (supply chain), SEC-NEW-11 (privileged access), and
COMP-NEW-1 (incident-relevant provenance) align the tool with the org's NIS2
posture. No tool-specific NIS2 obligation identified.

**PSTI / HIPAA / PCI-DSS — NOT APPLICABLE** (not a consumer IoT device; no
health or cardholder data handled by the tool).

---

## Next steps

All refined requirements and `TA-xxx` tests flow into the
[implementation plan](implementation-plan.md), which sequences the design-flaw
fixes first, then the docstrings / cognitive-complexity refactoring, then the
test build-out to >80% coverage including these security/privacy tests.
