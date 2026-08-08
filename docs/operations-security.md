# src2sink — Security Operations Guide

How to run `src2sink` safely, how to handle its inputs and outputs, and the
operational controls that back the [threat model](threat-model.md) and
[gap analysis](security-privacy-gap-analysis.md). This document covers the
non-code controls (`SEC-NEW-5`, `SEC-NEW-11`, `PRV-NEW-1`, `SC-1`) that the
implementation cannot enforce on its own.

---

## 1. Data classification & handling (`TA-010`)

Audit evidence for `TA-010` (output/config sensitivity handling): the table below
is the handling standard a reviewer checks the deployment against — restricted,
encrypted-at-rest metabase store and `api-clients.json` supplied as a CI secret
file.

| Asset | Classification | Handling |
|---|---|---|
| **Metabase outputs** (`metabase/` — findings, cross-repo topology, PII field references, ROPA projection) | **CONFIDENTIAL / RESTRICTED** | An aggregated map of exploitable weaknesses + personal-data locations across the fleet. Store in an access-controlled location, **encrypted at rest**; restrict read access to the app-sec team; do not publish to broad internal channels. |
| **`api-clients.json`** (internal service topology) | **SECRET** | Provide as a **CI secret file**, never a plaintext CLI argument in shared logs and never committed (it is gitignored). Mount read-only; scope access to the pipeline identity. |
| **`internal-groups.json`** | Internal | Low sensitivity; may be committed. |
| **Scanned repos** (`repos/`) | Untrusted input | Treat as hostile (may include malware/malicious test files). See §3. |

**Why RESTRICTED:** the metabase is, by design, a concentrated "where are the SQL
sinks / weak crypto / PII" map. That is valuable to defenders *and* attackers, so
the control is handling and access, not suppression (threat-model `P-2`, `I-1`).

---

## 2. Running in CI (least privilege) (`TA-012`)

Audit evidence for `TA-012` (least-privilege CI + sandbox): the checklist below is
what a reviewer confirms for the pipeline that runs the scan.

- Run under a **dedicated least-privilege CI identity** with no access to
  production secrets beyond `api-clients.json`. Do not run as root. (`E-1`, `SEC-NEW-11`.)
- Mount `repos/` **read-only**; the tool only reads scanned repos.
- Write the metabase to a restricted output store (see §1).
- Provide `api-clients.json` via the CI secret-file mechanism, not an inline arg.
- The tool never executes scanned code, but defence-in-depth still applies: a
  filesystem sandbox / container with no outbound network need for the scan step
  limits blast radius if a dependency is compromised.

---

## 3. Handling untrusted / malicious repositories

The tool is hardened to scan hostile input safely; the operator controls tune it:

| Control | Flag / mechanism | Default | Purpose |
|---|---|---|---|
| Per-repo timeout (bulkhead) | `--repo-timeout <s>` (0 disables) | 300s | Kill a repo that hangs (pathological parse) or pegs a CPU (ReDoS) — `D-1`. |
| Files-per-repo cap | `--max-files-per-repo <n>` (0 disables) | 50000 | Bound file-count amplification — `D-4`. |
| Per-file size cap | `--max-file-bytes <n>` (0 disables) | 1500000 | Skip an oversized file (recorded in the repo's `notes`) before it is read/parsed — `D-4`/`SEC-1`. |
| Minified-line pre-screen | `--max-line-bytes <n>` (0 disables) | 50000 | Skip a file with any single line this long (minified/obfuscated → parser/ReDoS hazard), recorded in the repo's `notes` — `SEC-NEW-4`. |
| Content pre-screen | `--prescreen-indicators <file>` | structural checks always on | Skip binary files always; add opt-in content indicators (one substring per line, `#` comments) — `SEC-NEW-4`. |

Binary pre-screening runs unconditionally; the minified/oversized-line check is
on by default but tunable (or disabled) via `--max-line-bytes`. Content
indicators are **opt-in** because the fleet includes legitimate
security/malware-analysis tooling that generic signatures would falsely flag.
Skipped files and timed-out repos are recorded (in `summary.notes` and the run
summary), never silently dropped.

Built-in structural safety also includes: `.git/HEAD` symref containment and
escaping-symlink skipping (`T-1`, `T-2`), hardened XML parsing via `defusedxml`
(`D-3`), untrusted-content neutralisation in Markdown outputs (`I-4`), and
literal-PII redaction in snippets (`PRV-NEW-2`).

---

## 4. Retention & erasure (`PRV-NEW-1`, `TA-014`, GDPR Art. 5(1)(e) / Art. 17)

The metabase records personal-data **references** (field names/classifications,
a ROPA projection) — not values — but it is still a durable cross-fleet map and
needs a lifecycle:

- **Retention:** keep only the current metabase plus whatever history your
  process requires; do not accumulate indefinitely. Recommended: regenerate on a
  schedule and retain a bounded window (e.g. the current run + N prior).
- **Erasure:** the metabase is fully regenerable from source, so deletion is
  safe — to remove a repo's footprint, delete `metabase/repos/<group>/<repo>.*`
  and re-run aggregation (`--aggregate-only`). Purge outputs from any backups per
  your retention window.
- **Provenance:** each build writes `metabase/run-manifest.json` (tool version,
  secret-free invocation summary, per-repo SHAs, counts, UTC timestamps) to
  support reproducibility and Art. 30 records (`R-1`).
- **Cost:** the same manifest carries a `timing` block — total wall clock plus a
  nested breakdown per phase, with shares of the whole run at every depth. Time
  no phase claimed is reported as `unattributed` rather than absorbed into a
  neighbour, so the table never implies coverage it does not have. The same
  breakdown is printed at the end of every run, including `--aggregate-only`
  (which prints it but writes no manifest, because that mode re-renders without
  scanning and would otherwise replace a full run's provenance with a partial
  one). It names phases only — no repo, path or duration attributable to an
  individual — so it adds nothing to the record's sensitivity.

---

## 5. Dependency & supply-chain hygiene (`SC-1`, `SEC-NEW-9`)

- A vulnerability audit runs on every push and pull request: the `pip-audit` job
  in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) fails the build on
  any advisory hit. Locally:

  ```sh
  make audit        # == uv run pip-audit
  ```

- The same workflow runs the SAST gates — `bandit` and `opengrep` (pinned rules)
  — plus `mypy --strict` and the SRTM traceability check (§`TA-011`).
- The runtime XML parser is `defusedxml` (permissive PSFL); `pip-audit`,
  `bandit`, `mypy` and `pytest-cov` are dev-only and never ship in the build
  artefact.
- **Published releases carry signed build provenance** (SLSA Build L3 from 1.0.3;
  L2 for 1.0.2; none before). The
  [release workflow](../.github/workflows/release.yml) publishes to PyPI over
  OIDC Trusted Publishing — no API token exists in this repository — and the
  provenance is generated by an isolated builder the build steps cannot reach.
  Before deploying a src2sink release into a pipeline, verify it rather than
  trusting the download:

  ```sh
  slsa-verifier verify-artifact src2sink-<version>-py3-none-any.whl \
    --provenance-path multiple.intoto.jsonl \
    --source-uri github.com/mimecast/src2sink --source-tag v<version>
  ```

  Scope and limits are in [slsa.md](slsa.md); the release procedure is in
  [releasing.md](releasing.md).
- **The lockfile is committed** and every CI job installs with `uv sync --locked`,
  which fails if `uv.lock` has drifted from `pyproject.toml`. Installs are
  therefore reproducible and hash-verified, and a dependency change is a reviewed
  diff — the pin half of `SC-1`, asserted by `TA-011`
  (`tests/test_dependency_pinning.py`).

---

## 6. CI log hygiene (`I-2`, `I-3`)

- Per-repo failures are logged as the **exception type + repo id only**, never
  the exception message (which could carry paths or scanned content).
- A missing/malformed `api-clients.json` is surfaced as a warning naming only the
  **filename and error type** (never contents), and reports `0 bindings` — so a
  silent misconfiguration cannot masquerade as a complete run.
- Avoid enabling verbose/debug logging of scanned content in shared CI logs.

---

## Related documents

- [architecture.md](architecture.md) · [threat-model.md](threat-model.md) ·
  [security-privacy-gap-analysis.md](security-privacy-gap-analysis.md) ·
  [api-clients-json.md](api-clients-json.md)
- Release-side controls: [releasing.md](releasing.md) · [slsa.md](slsa.md) ·
  [todo.md](todo.md)
