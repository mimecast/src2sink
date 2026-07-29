# TODO

Small, known pieces of work that are not worth a ticket yet but should not be
lost. Larger roadmap items live in [`NEXT_STEPS.md`](../NEXT_STEPS.md).

Format: one heading per item, with enough context to act on it cold.

---

## Build provenance: reach SLSA Build L2, then L3

**Status:** open · **Raised:** 2026-07-29 · **Severity:** hardening

Released artefacts carry no provenance today — neither a GitHub attestation nor
a PEP 740 attestation on PyPI — so the Build track level is L0. Steps 1–4 of the
checklist in [`slsa.md`](slsa.md) reach L2 in about an hour and are worth doing
on their own: they are what lets anyone outside this repository verify that a
published artefact came from a build of this source. Steps 5–8 reach L3.
