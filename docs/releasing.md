# Releasing src2sink to PyPI

Pushing a `v*` tag publishes the release. The
[`Release` workflow](../.github/workflows/release.yml) builds from the tagged
tree, uploads to PyPI over **Trusted Publishing** (OIDC — no API token exists
anywhere in this repository), and attaches the same artefacts to the GitHub
release. Your job is everything up to the tag.

**The one rule that matters:** a version number on PyPI can never be reused.
Upload `1.0.1`, notice a mistake, and your only options are to yank it and
release `1.0.2`. The workflow refuses to publish if the tag and the packaged
version disagree, but it cannot check that the *contents* are right — that is
what step 3 is for.

Package: [`src2sink`](https://pypi.org/project/src2sink/) · built with
`setuptools`, driven by `uv`.

---

## 0. One-time setup

Already done for this repository — recorded here because it breaks silently if
anyone renames things.

**On PyPI** (project → *Manage* → *Publishing* → *Add a new publisher* → GitHub):

| Field | Value |
|---|---|
| Owner | `mimecast` |
| Repository name | `src2sink` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

PyPI matches all four. Renaming the workflow file or the environment stops
publishing until the publisher entry is updated to match.

**On GitHub** (*Settings* → *Environments* → `pypi`): optionally add required
reviewers to gate every upload on a human approval, and restrict deployments to
tags matching `v*`. The `publish` job is the only one granted `id-token: write`,
and it holds no other permission.

No token is needed for the normal path. Keep a project-scoped API token in your
password manager only as the break-glass fallback (appendix).

## 1. Decide the version

Semantic versioning on the metabase's observable contract — the CLI flags, the
JSON/Markdown schema in [`SCHEMA.md`](../SCHEMA.md), and `SCHEMA_VERSION`:

| Change | Bump |
|---|---|
| A schema field is removed or its meaning changes; a CLI flag is dropped | **major** |
| A new node/edge family, new flag, new aggregator output | **minor** |
| Bug fix, hardening, docs, dependency bump | **patch** |

A `SCHEMA_VERSION` change is always at least a minor bump, and consumers must be
able to tell from the version alone whether their stored metabase still parses.

## 2. Prepare the tree

```sh
git switch main && git pull            # release from main only
git status --short                     # must be empty
```

Add a dated section for the new version at the top of
[`CHANGELOG.md`](../CHANGELOG.md), plus a `[x.y.z]: .../releases/tag/vx.y.z` link
at the bottom. **This is not optional:** the workflow extracts that section as
the GitHub release body and fails the release if it is empty. Write it for
someone deciding whether to upgrade — what changed for *them*, not which commits
landed.

Bump `version` in `pyproject.toml`, then refresh and re-check the lockfile:

```sh
uv lock                                # records the new version
uv lock --check                        # must pass; CI installs with --locked
```

**`README.md` is the PyPI project page.** PyPI renders it standalone, with no
repository around it, so every link and image in it must be an **absolute URL** —
a relative `./images/logo.png` renders as a broken image and `./SCHEMA.md`
resolves to a pypi.org 404. Check with:

```sh
grep -noE '\]\((?!http)[^)]+\)' -P README.md   # must print nothing
```

## 3. Run every gate locally

```sh
make ci                                # lint, typecheck, test, srtm, bandit, audit
```

`make ci` covers everything CI does except `opengrep` (which needs the external
ruleset — see the Makefile target). Rehearse the build while you are here:

```sh
rm -rf dist build src2sink.egg-info
uv build
uv run --with twine twine check dist/*
uv run --isolated --no-project --python 3.14 \
  --with ./dist/src2sink-<version>-py3-none-any.whl src2sink-build --help
```

These artefacts are a rehearsal only — the ones that ship are built by the
workflow from the tag. `dist/` is gitignored.

## 4. Commit, push, and wait for green

```sh
git commit -am "Release 1.0.1"
git push origin main
gh run watch "$(gh run list --workflow=CI --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

This is the last cheap moment to abort. Everything after the tag is public.

## 5. Tag — this publishes

```sh
git tag -a v1.0.1 -m "src2sink 1.0.1"
git push origin v1.0.1
```

Watch it land:

```sh
gh run watch "$(gh run list --workflow=Release --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

The workflow builds, checks the tag against `pyproject.toml`, `twine check`s the
metadata, publishes to PyPI, and creates the GitHub release with your changelog
section as the body and the artefacts attached.

## 6. Verify from outside

```sh
uv run --isolated --no-project --python 3.14 --with src2sink src2sink-build --help
```

Then open <https://pypi.org/project/src2sink/> and confirm the banner renders and
the README links resolve.

---

## If something goes wrong

- **Bad artefact already published.** You cannot overwrite it. Yank the release
  (`pypi.org` → *Manage* → *Yank*), which hides it from new resolutions while
  leaving existing pins working, then fix forward with a patch version.
- **Wrong tag, nothing published yet.**
  `git tag -d v1.0.1 && git push --delete origin v1.0.1`. Once a version is on
  PyPI, leave the tag alone — it is the provenance record for what shipped.
- **The publish job fails with an OIDC/trusted-publisher error.** The four fields
  in §0 must match exactly, including the environment name. A workflow renamed
  or moved is the usual cause.
- **Tag/version mismatch.** The build job fails before anything is uploaded.
  Delete the tag, fix `pyproject.toml`, commit, re-tag.

## Appendix: publishing by hand (break-glass)

Only if the workflow is unavailable and a release cannot wait. This puts a
long-lived token on a laptop, which is what Trusted Publishing exists to avoid.

```sh
export UV_PUBLISH_TOKEN='pypi-...'     # project-scoped token
rm -rf dist && uv build
uv run --with twine twine check dist/*
uv publish --dry-run dist/*
uv publish dist/*
gh release create v1.0.1 dist/* --title "src2sink 1.0.1" --notes-file <(
  awk '/^## \[1\.0\.1\]/{f=1; next} /^## \[/{f=0} f' CHANGELOG.md)
```

Rehearsing on TestPyPI first, when the packaging itself has changed:

```sh
uv publish --publish-url https://test.pypi.org/legacy/ \
           --token "$TEST_PYPI_TOKEN" dist/*
uv run --isolated --no-project --python 3.14 \
  --index https://test.pypi.org/simple/ --index-strategy unsafe-best-match \
  --with src2sink src2sink-build --help
```

TestPyPI does not mirror all dependencies, hence the fallback index.
