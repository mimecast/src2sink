# src2sink — Closed Detection Issues

Issues that have been **fixed and verified**, moved here from
[`src2sink-open-issues.md`](src2sink-open-issues.md). That file is the backlog;
this one is the record. Nothing is ever deleted from here — a closed issue is
evidence about how a detection path once failed, which is exactly the context
someone needs when the same area breaks again.

**Anonymisation notice:** as in the open-issues document — every repository,
package, artifact id, service, class, constant and URL path is fictitious.
References to `src2sink`'s own source (file:line) and to third-party library
names are real.

---

## How an issue is closed

1. **Verify.** The fix is merged, `make ci` is green, and the issue's own
   regression test exists and fails against the pre-fix code. An issue is not
   closed by a fix that has no test.
2. **Move the section verbatim.** Cut the whole `## n. Title  \`OI-n\`` section
   out of the open-issues file and paste it below, under the same `OI-n` id.
   Do not rewrite it — the original symptom and root-cause text is the record.
   Renumber the heading to `## OI-n — Title`, since section ordering no longer
   applies.
3. **Prepend a `### Resolution` block** to the moved section, with:
   * **Fixed in** — the release version;
   * **Commit** — the sha(s) of the fix, short form, and the PR number if there
     was one;
   * **Tests** — the test ids that now guard it, so the link from issue to test
     is greppable in both directions;
   * **What changed** — two or three sentences of what was actually done,
     including where it **deviated** from the fix proposed in the original
     section. A proposed fix that was amended during implementation is the most
     valuable thing on the page; record why.
   * **Behaviour change** — any output that a consumer would see differ, or
     "none".
4. **Add a row to the index below.**
5. **Update the open-issues §5 priority table** — remove the row.

**On the commit sha:** the sha of the fix is not knowable inside the fix commit
itself, so the move is a *follow-up* commit — normally the release-prep commit,
which can close several issues at once. Do not leave a `TBD` in the sha column;
an entry without a sha is not a record of anything.

**Do not repeat the issue text in `CHANGELOG.md`.** The changelog says what
changed for a user; this file says why the detection was wrong. They serve
different readers.

---

## Index

| id | Issue | Fixed in | Commit | Behaviour change for consumers |
|---|---|---|---|---|
| _(none yet)_ | | | | |

---

<!--
Template — copy for each closed issue, then paste the original section beneath it.

## OI-n — Original title

### Resolution

**Fixed in:** 1.2.0
**Commit:** `abc1234` (PR #NN)
**Tests:** `tests/test_x.py::test_y`, `tests/test_z.py::test_w`
**What changed:** ...
**Deviation from the proposed fix:** ... (or "none — implemented as proposed")
**Behaviour change:** ... (or "none")

<original section text, verbatim, from Severity through Residual not covered>

---
-->
