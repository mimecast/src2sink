# TODO

Small, known pieces of work that are not worth a ticket yet but should not be
lost. Larger roadmap items live in [`NEXT_STEPS.md`](../NEXT_STEPS.md).

Format: one heading per item, with enough context to act on it cold.

---

## SLSA generator pins Node 20 actions (deprecation exposure)

**Status:** open (upstream) · **Raised:** 2026-07-29 · **Severity:** availability

Every release run warns three times, all from inside
`slsa-github-generator@v2.1.0` — nothing in this repository:

- `detect-workflow-js@v2.1.0`, and the generator's internal `checkout`,
  `setup-go`, `upload-artifact` and composite actions, target **Node.js 20**,
  which GitHub is deprecating and currently forces onto Node 24. When Node 20
  support is removed, the `provenance` job breaks and takes the release path
  with it.
- `Restore cache failed: Dependencies file is not found … Supported file pattern:
  go.sum` — the generator's `setup-go` looks for a Go module cache in our Python
  checkout. Cosmetic, unfixable from here, and irrelevant to the provenance.

We cannot get ahead of it: v2.1.0 is the newest release (2025-02-24) and the pin
**must stay a tag**, not a SHA, or `slsa-verifier` cannot derive the builder
identity. The project is alive (last commit 2026-03-09), so the likely resolution
is a v2.2.0 to bump to.

**Mitigation in place:** the monthly scheduled rehearsal in
[`release.yml`](../.github/workflows/release.yml) runs `build` + `provenance`
with no publishing, so upstream breakage surfaces on its own rather than
mid-release. Publishing is ordered after provenance, so a break costs a re-tag,
never a burned version number.

**When it fires:** check for a newer generator release and bump the tag. If there
is none, [`slsa.md`](slsa.md) §4 Route B (our own trusted reusable workflow) is
the fallback — at the cost of consumers no longer being able to verify against a
builder ID `slsa-verifier` recognises.
