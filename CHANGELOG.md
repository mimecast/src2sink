# Changelog

Notable changes to src2sink. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html) applied to the
observable contract — the CLI flags and the output schema (`SCHEMA_VERSION`), as
set out in [`docs/releasing.md`](docs/releasing.md).

## [Unreleased]

### Fixed

- Build provenance no longer attests `dist/.gitignore`. `subject-path: dist/*`
  matched the file uv writes there, so 1.0.2's provenance lists it as a third
  subject alongside the wheel and sdist. Cosmetic — the real artefacts are
  attested correctly — but confusing to read.

## [1.0.2] — 2026-07-29

**No functional changes.** Adds build provenance to the release process.

### Added

- **Signed build provenance — SLSA Build L2.** Every published artefact now
  carries provenance generated and signed by the build platform, in two forms:
  a GitHub attestation, and a PEP 740 attestation stored alongside the files on
  PyPI so it is available to anyone who installed from there rather than from
  the GitHub release. Verify with:

  ```sh
  gh attestation verify src2sink-1.0.2-py3-none-any.whl --repo mimecast/src2sink
  python -m pypi_attestations verify pypi --repo mimecast/src2sink src2sink-1.0.2-*.whl
  ```

  Provenance ties an artefact to the source commit, tag, and workflow run that
  produced it, so a file claiming to be src2sink can be checked rather than
  trusted. 1.0.0 and 1.0.1 have none — they predate this.

  This is Build **L2**, not L3: the identity that signs belongs to the same job
  that runs the build, so it is not beyond the reach of a compromised build step.
  [`docs/slsa.md`](docs/slsa.md) sets out what closes that gap.

### Changed

- The publish step uses `pypa/gh-action-pypi-publish` instead of `uv publish`.
  uv uploads PEP 740 attestations that already exist but does not generate them;
  the PyPA action does both. Publishing is still tokenless Trusted Publishing,
  and the PyPI publisher configuration is unchanged.

## [1.0.1] — 2026-07-29

**No functional changes.** The analyser, its output schema, and the CLI are
byte-for-byte what 1.0.0 shipped. There is nothing here a user of 1.0.0 needs;
the version exists to carry the release automation and documentation below.

### Added

- This changelog, and a release procedure ([`docs/releasing.md`](docs/releasing.md))
  covering versioning, the gate run, tagging, building, and recovery when a bad
  version reaches PyPI.
- Automated publishing via **PyPI Trusted Publishing** (OIDC). A `v*` tag now
  builds from the tagged tree, verifies the tag matches the packaged version,
  publishes to PyPI, and attaches the same artefacts to the GitHub release —
  with no API token stored anywhere in the repository.
- A link to this changelog from the README.

### Fixed

- **CI cache save race.** Every cached job derived the same `uv` cache key and
  started at once, so they raced for the save reservation and the losers
  annotated each run with "Unable to reserve cache". One job now writes the
  cache and the rest restore only; `srtm`, which installs nothing, opts out
  entirely (the input defaults to `auto`, which had quietly opted it in).
  Verified on a green run with zero annotations.

## [1.0.0] — 2026-07-28

> **Yanked on PyPI, 2026-07-29 — "Superseded by 1.0.1".** Not a defect: 1.0.1 is
> functionally identical, and this release is sound. Existing `== 1.0.0` pins
> keep resolving as before; new installs resolve to 1.0.1 regardless.

First public release. src2sink builds a **source-to-sink metabase**: a structured,
human-readable knowledge base of an entire source-code estate, designed to be
loaded as context for LLM-assisted SAST so taint can be followed *across*
repositories — a SQL fragment built in one service and executed in another, an
internal library that silently forwards to a JDBC sink, PII entering at an
ingress and surfacing in a log three repos away.

### Extraction

- Tree-sitter extractors for **Java, Kotlin, Python, Go, JavaScript, and
  TypeScript**, plus configuration-file extraction (Spring `application.yml` /
  `.properties`, and friends) for facts that never appear in code.
- Output schema **v2** (`SCHEMA_VERSION = 2`): a flow graph of `FlowNode`
  (`source` / `propagator` / `sink` / `store`, with a `family`, a
  `pii_classification`, a `data_class`, and a confidence rating) joined by
  `FlowEdge` at intra-file, intra-repo, and **cross-repo** scope.
- Build-system and framework detection across Maven, Gradle, npm/yarn/pnpm, pip,
  Poetry, Pipenv, Go modules, and Cargo. Declared dependencies are parsed from
  `pom.xml`, `build.gradle*`, and `package.json`; component *identity* (which
  repo publishes a given coordinate) additionally resolves `pyproject.toml`,
  `setup.cfg`, `Cargo.toml`, `composer.json`, `go.mod`, `*.csproj`/`*.fsproj`/
  `*.vbproj`, and `*.gemspec`. An internal-vs-external coordinate classifier
  runs off your own namespace patterns.

### Cross-repo analysis

- **Taint catalogues** — SQL sources and execution sinks, file sinks, outbound
  HTTP sinks, PII sources and sinks, crypto operations, raw-code-payload
  endpoints, and security-relevant configuration.
- **Graphs** — service-call graph (HTTP out ↔ HTTP in), queue producer/consumer
  graph, data-store graph, payload-endpoint producers, PII lifecycle, and
  cross-repo phone-number flows.
- **OpenAPI discovery** — specs found in the estate are matched to services and
  folded into the service-call edges.
- **Phase 3 models** — per-repo auth cards, crypto-agility cards, a PII lifecycle
  model, and a **GDPR Article 30 ROPA projection**.
- **Bidirectional tracing** — `src2sink-trace` follows a target repo or endpoint
  upstream to its producers and downstream to its sinks; `src2sink-trace-batch`
  does it for every raw-payload endpoint discovered.

### Command-line tools

`src2sink-build` (extract + aggregate), `src2sink-trace`, `src2sink-trace-batch`,
`src2sink-curate` (internal-library taint tables), and `src2sink-baseline`
(fleet baseline). Builds are incremental by git SHA — unchanged repos are
skipped, cross-repo aggregation always re-runs so the estate stays consistent.

### Built to scan hostile input

Scanned repositories are treated as untrusted, and the outputs are treated as
prompt material for an LLM. Every control below is traced to a test through the
[SRTM](docs/security-privacy-gap-analysis.md):

- **Execution bulkhead** — each repo is analysed in its own process with a
  wall-clock budget, so a pathological parse or catastrophic regex kills one
  repo, not the run.
- **Path containment** — crafted `.git/HEAD` symrefs and escaping symlinks cannot
  read outside the repo.
- **Hardened XML** — manifests are parsed with `defusedxml`; entity-expansion
  payloads do not expand.
- **Size, file-count, and line-length caps**, plus a content pre-screen that
  skips binary and minified/obfuscated files before a parser sees them. Every
  skip is recorded, never silent.
- **Untrusted-output neutralisation** — extracted content is escaped and fenced
  in Markdown and JSONL so a comment in a scanned repo cannot issue instructions
  to a downstream LLM.
- **Literal-PII redaction** in code snippets, and log hygiene that reports the
  exception *type* and repo id rather than paths or content.
- **Run provenance** — every build writes a secret-free `run-manifest.json`
  (tool version, per-repo SHAs, counts, UTC timestamps) for reproducibility and
  Article 30 records.

Operational guidance — data classification, least-privilege CI, retention and
erasure — is in [`docs/operations-security.md`](docs/operations-security.md);
the risk register is in [`docs/threat-model.md`](docs/threat-model.md).

### Quality gates

CI runs six gates on every push and pull request, and weekly:

| Gate | Enforces |
|---|---|
| `test` | 302 tests, 84% coverage overall, 90% on the security-critical modules |
| `srtm` | every requirement in the SRTM still has an implementing test or a documented audit |
| `mypy (strict)` | `mypy --strict`, clean across the package and scripts |
| `bandit` | Python SAST, clean |
| `pip-audit` | no known advisories against the locked dependency set |
| `opengrep` | pattern SAST with a pinned ruleset, clean at ERROR severity |

Dependencies are hash-pinned in a committed `uv.lock` and installed with
`uv sync --locked`. Known false positives are annotated inline with a stated
reason rather than suppressed wholesale.

### Requirements

Python **3.14+**. Install with `pip install src2sink` or `uv add src2sink`.

### Known limitations

- Detection is heuristic and rated (`high` / `medium` / `low`); treat `low` as
  *investigate*, not *confirmed*.
- Scala files are counted in the language breakdown and get the language-agnostic
  regex passes, but there is no tree-sitter grammar for them yet, so no AST-level
  extraction.
- The metabase is a concentrated map of weaknesses and personal-data locations.
  Store it access-controlled and encrypted at rest — see the operations guide.

[1.0.2]: https://github.com/mimecast/src2sink/releases/tag/v1.0.2
[1.0.1]: https://github.com/mimecast/src2sink/releases/tag/v1.0.1
[1.0.0]: https://github.com/mimecast/src2sink/releases/tag/v1.0.0
