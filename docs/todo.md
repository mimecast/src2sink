# TODO

Small, known pieces of work that are not worth a ticket yet but should not be
lost. Larger roadmap items live in [`NEXT_STEPS.md`](../NEXT_STEPS.md).

Format: one heading per item, with enough context to act on it cold.

---

## Publish to PyPI via Trusted Publishing instead of a token

**Status:** open · **Raised:** 2026-07-28 · **Severity:** hardening

[`releasing.md`](releasing.md) is a manual procedure driven by a long-lived PyPI
API token on a laptop. A `release.yml` workflow triggered on `v*` tags, using
OIDC Trusted Publishing (`permissions: id-token: write`, a `pypi` environment,
and a PyPI publisher configured for `mimecast/src2sink`), would remove the token
entirely and guarantee the uploaded artefact is the one CI built from the tag.

Do this before the second release, while the habit is still cheap to change.
