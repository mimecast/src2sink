# Reaching SLSA Build L2 and L3

What src2sink would have to change to make a defensible SLSA claim, in the order
it should be done. Written against [SLSA v1.0](https://slsa.dev/spec/v1.0/levels),
whose **Build track** is the only track with stable levels; the Source and
Dependency tracks are still drafts and are out of scope here.

The short version: the hard parts are already done. The release runs on a hosted
platform from a tag, actions are SHA-pinned, publishing is tokenless, and a human
approves each upload. What is missing is the actual product of SLSA — **signed
provenance** — which nothing in the pipeline currently produces.

---

## 1. Where we are today

Verified on 2026-07-29 against the published 1.0.1 artefacts:

```console
$ gh attestation verify src2sink-1.0.1-py3-none-any.whl --repo mimecast/src2sink
Error: HTTP 404: Not Found (…/attestations/sha256:bcb8c1da…)

$ curl -H 'Accept: application/vnd.pypi.simple.v1+json' https://pypi.org/simple/src2sink/ | jq '.files[].provenance'
null
null
null
null
```

No provenance exists in either place, so the Build track level is **L0**, not L1.
`uv publish` uploads PEP 740 attestations when they sit alongside the
distributions but does not generate them, and nothing in
[`release.yml`](../.github/workflows/release.yml) generates any.

What *is* already in place — all of it required by, or supporting, L2/L3:

| Property | Status | Where |
|---|---|---|
| Build runs on hosted, ephemeral infrastructure | ✅ | GitHub-hosted `ubuntu-latest`; no self-hosted runners |
| Build is scripted and consistent, not a laptop | ✅ | `release.yml` `build` job, `uv build` |
| Build triggered from an immutable ref | ✅ | `on: push: tags: ["v*"]` |
| Tag and packaged version cross-checked | ✅ | `build` job's version check |
| Actions pinned to commit SHAs | ✅ | every `uses:` in both workflows |
| Upload credential is short-lived, not a stored token | ✅ | PyPI Trusted Publishing (OIDC) |
| Upload gated on human approval | ✅ | `pypi` environment, required reviewer |
| Publish job holds no permission but `id-token: write` | ✅ | `release.yml` `publish` job |
| Dependencies hash-pinned and installed `--locked` | ✅ | `uv.lock`, CI `uv sync --locked` |
| **Signed provenance produced** | ❌ | nothing generates it |
| **Provenance distributed to consumers** | ❌ | — |
| **Provenance generated outside user-controlled steps** | ❌ | required for L3 only |

---

## 2. What each level actually requires

Quoting the spec, with the reading that matters for this repo:

**Build L1 — provenance exists.** A consistent build process; provenance
describing the platform, process and top-level inputs; distributed to consumers.
*Protects against: mistakes, e.g. releasing from the wrong commit.* Unsigned
provenance counts.

**Build L2 — hosted build platform.** L1, plus the build "runs on dedicated
infrastructure, not an individual's workstation, and the provenance is tied to
that infrastructure through a digital signature", and verification includes
"validating the authenticity of the provenance".
*Protects against: tampering after the build.* It does **not** protect against
tampering during the build.

**Build L3 — hardened builds.** L2, plus the platform must "prevent runs from
influencing one another, even within the same project" and "prevent secret
material used to sign the provenance from being accessible to the user-defined
build steps".
*Protects against: tampering during the build — insider threat, compromised
credentials, other tenants.*

That last clause is the whole difficulty of L3 on GitHub Actions. A step you
write in your own workflow, running in the same job that signs, is by definition
a user-defined build step with access to the signing identity. Reaching L3 means
moving provenance generation somewhere your build steps cannot reach.

---

## 3. Phase 1 — Build L1 and L2

One change to `release.yml` gets both, because GitHub's attestation action signs
via Sigstore using the workflow's OIDC identity and stores the result in GitHub's
attestation API. The signature is what lifts L1 to L2.

### 3.1 Attest the build artefacts

In the `build` job, after `uv build` and the `twine check`:

```yaml
    permissions:
      contents: read
      id-token: write       # sign via Sigstore
      attestations: write   # write to the repository's attestation store
    steps:
      # … existing checkout / setup-uv / version check / build / twine check …
      - uses: actions/attest-build-provenance@0f67c3f4856b2e3261c31976d6725780e5e4c373 # v4.1.1
        with:
          subject-path: "dist/*"
```

Note the trade-off: the `build` job currently holds only the workflow-level
`contents: read`, and this adds two write permissions to the job that runs your
build steps. That is unavoidable at L2 — something has to sign, and here it is
the same job — and it is precisely the weakness Phase 2 removes.

### 3.2 Attach PEP 740 attestations to the PyPI upload

PyPI stores attestations per file and exposes them in the Simple API, which is
how a consumer installing from PyPI (rather than from the GitHub release) can
verify anything at all. `uv publish` will not generate these, so the `publish`
job switches to the PyPA action, which generates and uploads them by default:

```yaml
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        # attestations: true is the default; it signs each dist with the
        # workflow's OIDC identity and uploads the .publish.attestation files.
```

The trusted-publisher entry on PyPI matches on owner, repository, **workflow
filename**, and environment — not on which action performs the upload — so this
swap needs no PyPI-side change. Keep the filename `release.yml`.

Alternative if you would rather keep `uv publish`: generate the attestations
first with `python -m pypi_attestations sign dist/*`, then upload; uv will pick
up the `.publish.attestation` files sitting next to the distributions.

### 3.3 Acceptance test

After the next release, all three must pass:

```sh
gh attestation verify src2sink-<v>-py3-none-any.whl --repo mimecast/src2sink
curl -H 'Accept: application/vnd.pypi.simple.v1+json' https://pypi.org/simple/src2sink/ \
  | jq '.files[] | select(.filename|contains("<v>")) | .provenance'   # must be a URL, not null
python -m pypi_attestations verify pypi --repo mimecast/src2sink dist/*
```

**Effort:** an hour, most of it waiting for a release to prove it.
**Risk:** low, and it cannot corrupt a release — attestation failures fail the
job before or after upload without altering the artefacts.

---

## 4. Phase 2 — Build L3

L3 requires provenance the build itself cannot forge. Two credible routes.

### Route A — SLSA generator reusable workflow (recommended)

`slsa-framework/slsa-github-generator` provides reusable workflows that generate
and sign provenance in a job whose steps the calling workflow cannot modify, so
the signing identity is never exposed to your build. This is the route
`slsa-verifier` is built to check, and the one an auditor will recognise.

Shape of the change:

```yaml
  build:
    # … unchanged, plus: emit the artefact digests for the generator …
    outputs:
      digests: ${{ steps.hash.outputs.digests }}
    steps:
      - id: hash
        run: echo "digests=$(sha256sum dist/* | base64 -w0)" >> "$GITHUB_OUTPUT"

  provenance:
    needs: build
    permissions:
      actions: read      # read the workflow run that produced the artefacts
      id-token: write    # sign
      contents: write    # attach provenance to the release
    uses: slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0
    with:
      base64-subjects: ${{ needs.build.outputs.digests }}
      upload-assets: true
```

**This one `uses:` must be a tag, not a SHA.** `slsa-verifier` derives the
expected builder identity from the tag, and a SHA reference makes the provenance
unverifiable. It is the single deliberate exception to the SHA-pinning rule used
everywhere else in this repository — say so in a comment, or the next person will
"fix" it.

Then re-point PyPI attestations at the generated provenance, or keep both: the
GitHub attestation store carries the SLSA provenance, PyPI carries the PEP 740
attestation. They are different artefacts with different verification tools, and
consumers reach for whichever matches how they installed.

### Route B — your own trusted reusable workflow

Move the build and the attestation into a reusable workflow in a repository the
release workflow cannot modify, and call it. This satisfies the same isolation
property without a third-party dependency, but you own the correctness argument,
and `slsa-verifier` will not recognise the builder ID — consumers must verify
against your identity by hand. Only worth it if depending on
`slsa-github-generator` is unacceptable for policy reasons.

### 4.1 What else L3 demands

- **Ephemeral, isolated runs.** GitHub-hosted runners satisfy this. Introducing a
  self-hosted runner into the release path would break L3 unless it is
  provably ephemeral and isolated per run — don't.
- **No secrets reachable by build steps.** Already true: the release path holds
  no secrets at all, because publishing is tokenless. Adding any `secrets.*` to
  the build job would need re-examining.
- **Provenance completeness.** The generator records the source repo, the tag,
  the workflow, the builder version, and the artefact digests. Nothing to do,
  but review the emitted provenance once and confirm it says what you expect.

### 4.2 Acceptance test

```sh
slsa-verifier verify-artifact src2sink-<v>-py3-none-any.whl \
  --provenance-path multiple.intoto.jsonl \
  --source-uri github.com/mimecast/src2sink \
  --source-tag v<version>
```

**Effort:** half a day, mostly reading the generator's docs and getting the
digest plumbing right.
**Risk:** moderate — the failure mode is a release that builds but does not
publish. Rehearse on a `v0.0.0-slsa-test` tag against **TestPyPI** before using it
for a real version, since a failed publish still burns the version number on PyPI
if it gets that far.

---

## 5. How consumers verify

Worth documenting in the README once Phase 1 lands, because provenance nobody
checks buys nothing:

```sh
# Installed from the GitHub release:
gh attestation verify src2sink-1.0.2-py3-none-any.whl --repo mimecast/src2sink

# Installed from PyPI (PEP 740 attestation):
python -m pypi_attestations verify pypi --repo mimecast/src2sink src2sink-1.0.2-*.whl

# Full SLSA provenance, after Phase 2:
slsa-verifier verify-artifact … --source-uri github.com/mimecast/src2sink
```

---

## 6. What this does not buy

Be precise when claiming a level, because the Build track is narrower than
"supply-chain secure" suggests:

- **It says nothing about the dependencies.** Provenance proves *this* tree
  produced *this* artefact; it makes no claim about `tree-sitter` or anything
  else in `uv.lock`. That is the draft Dependency track. `pip-audit` and the
  hash-pinned lockfile are what cover it here.
- **It says nothing about the source being trustworthy.** A malicious commit on
  `main` yields perfectly valid L3 provenance. That is the draft Source track;
  branch protection and review are the controls, and they are worth having on
  their own merits.
- **It does not make the build reproducible.** SLSA v1.0 dropped the old L4;
  bit-for-bit reproducibility is a separate goal.
- **L2 does not protect the build itself.** Until Phase 2, a compromised step in
  the release workflow could produce a tampered artefact with authentic-looking
  provenance.

---

## 7. Ordered checklist

| # | Step | Level | Effort |
|---|---|---|---|
| 1 | Add `attest-build-provenance` to the `build` job | L1 → L2 | 20 min |
| 2 | Swap `uv publish` for `gh-action-pypi-publish` (or pre-sign with `pypi-attestations`) | L2 on PyPI | 20 min |
| 3 | Cut a release; run the three acceptance checks in §3.3 | verifies L2 | 30 min |
| 4 | Document consumer verification in the README | — | 15 min |
| 5 | Emit artefact digests from `build` | prep | 30 min |
| 6 | Add the `generator_generic_slsa3.yml` job, tag-pinned with a comment explaining why | L2 → L3 | 2–3 h |
| 7 | Rehearse on TestPyPI with a throwaway tag | — | 1 h |
| 8 | Cut a release; run the §4.2 acceptance check | verifies L3 | 30 min |
| 9 | State the claimed level and verification steps in the README | — | 15 min |

Steps 1–4 are independently valuable and worth doing regardless of whether L3
ever happens: they are what let anyone outside this repository check that a
published artefact came from a build of this source.

---

## 8. Before implementing

Pinned versions in this document were current on 2026-07-29
(`attest-build-provenance` v4.1.1, `gh-action-pypi-publish` v1.14.1,
`slsa-github-generator` v2.1.0, `slsa-verifier` v2.7.1). Re-check the upstream
docs when you start — this is fast-moving ground, and the SLSA generator's inputs
in particular have changed between major versions.
