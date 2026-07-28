# TODO

Small, known pieces of work that are not worth a ticket yet but should not be
lost. Larger roadmap items live in [`NEXT_STEPS.md`](../NEXT_STEPS.md).

Format: one heading per item, with enough context to act on it cold.

---

## CI: uv cache save race between parallel jobs

**Status:** open · **Raised:** 2026-07-28 · **Severity:** cosmetic

The five jobs in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) that
call `astral-sh/setup-uv` with `enable-cache: true` start simultaneously and all
compute the *same* cache key, so they race to save it. The losers annotate the
run with:

```
Failed to save: Unable to reserve cache with key
setup-uv-2-x86_64-unknown-linux-gnu-ubuntu-24.04-3.14-<hash>,
another job may be creating this cache.
```

Harmless — whichever job wins writes the cache and every later run restores it —
but it puts two or three warning annotations on every green run, which trains
people to ignore annotations.

**Fix options, cheapest first:**

1. Leave `enable-cache: true` on the `test` job only and set the other jobs to
   restore-only. `setup-uv` exposes `cache-local-path` / `save-cache`; check the
   pinned version's inputs before wiring this up.
2. Add a tiny `warm-cache` job the others `needs:`. Correct, but serialises the
   fan-out and costs more wall-clock than the cache saves at this tree size.
3. Give each job a distinct `cache-suffix` (e.g. the job name). No more races,
   but N copies of the same dependency set in the cache quota.

Option 1 is the intended one. Verify by pushing a branch and confirming a green
run with zero annotations.

---

## Publish to PyPI via Trusted Publishing instead of a token

**Status:** open · **Raised:** 2026-07-28 · **Severity:** hardening

[`releasing.md`](releasing.md) is a manual procedure driven by a long-lived PyPI
API token on a laptop. A `release.yml` workflow triggered on `v*` tags, using
OIDC Trusted Publishing (`permissions: id-token: write`, a `pypi` environment,
and a PyPI publisher configured for `mimecast/src2sink`), would remove the token
entirely and guarantee the uploaded artefact is the one CI built from the tag.

Do this before the second release, while the habit is still cheap to change.
