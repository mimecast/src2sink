# Releasing src2sink to PyPI

The whole procedure, start to finish. It is deliberately manual — releases are
rare and irreversible, so the steps are ones you read before running.

**The one rule that matters:** a version number on PyPI can never be reused.
Upload `1.0.0`, notice a mistake, and your only options are to yank it and
release `1.0.1`. Test on TestPyPI first (step 6) whenever anything about the
packaging itself has changed.

Package: [`src2sink`](https://pypi.org/project/src2sink/) · built with
`setuptools`, driven by `uv`.

---

## 0. Prerequisites (once per machine)

- A PyPI account with **2FA enabled** (mandatory for new projects).
- An **API token**, scoped to this project once it exists:
  <https://pypi.org/manage/account/token/>. Keep it in your password manager;
  export it per shell session rather than storing it in a dotfile:

  ```sh
  export UV_PUBLISH_TOKEN='pypi-...'
  ```

  Never pass a token as a literal argument in a shared shell — it lands in
  history and, in CI, in logs (see [operations-security.md](operations-security.md) §1).
- A TestPyPI account + token if you intend to rehearse:
  <https://test.pypi.org/manage/account/token/>.

---

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

Write the release notes **first**, while the changes are still fresh: add a
dated section for the new version at the top of [`CHANGELOG.md`](../CHANGELOG.md),
and a `[x.y.z]: .../releases/tag/vx.y.z` link at the bottom. That section is the
release body in step 8, so write it for someone deciding whether to upgrade —
what changed for *them*, not which commits landed.

Bump `version` in `pyproject.toml`, then refresh and re-check the lockfile:

```sh
uv lock                                # records the new version
uv lock --check                        # must pass; CI installs with --locked
```

**`README.md` is the PyPI project page.** PyPI renders it standalone, with no
repository around it, so every link and image in it must be an **absolute URL** —
a relative `./images/logo.png` renders as a broken image and `./SCHEMA.md`
resolves to a pypi.org 404. Keep the banner on
`https://raw.githubusercontent.com/mimecast/src2sink/main/...` and in-repo links
on `https://github.com/mimecast/src2sink/blob/main/...`. Check with:

```sh
grep -noE '\]\((?!http)[^)]+\)' -P README.md   # must print nothing
```

## 3. Run every gate locally

```sh
make ci                                # lint, typecheck, test, srtm, bandit, audit
```

`make ci` covers everything CI does except `opengrep` (which needs the external
ruleset — see the Makefile target). Do not release off a red or unrun tree: the
CI badge reflects the last push, not your working copy.

## 4. Commit and tag

```sh
git commit -am "Release 1.0.0"
git tag -a v1.0.0 -m "src2sink 1.0.0"  # annotated, v-prefixed
git push origin main
git push origin v1.0.0
```

Push the commit *before* the tag, so the tag never points at a commit the remote
does not have. Wait for CI to go green on the pushed commit before continuing —
that is the last cheap moment to abort.

## 5. Build

```sh
rm -rf dist build src2sink.egg-info    # never ship a stale artefact
uv build                               # writes dist/*.whl and dist/*.tar.gz
uv run --with twine twine check dist/* # metadata + README render must PASS
```

`dist/` is gitignored; the artefacts are reproducible from the tag, so there is
nothing to commit here.

Sanity-check the wheel in a throwaway environment before it reaches anyone:

```sh
uv run --isolated --no-project --python 3.14 \
  --with ./dist/src2sink-1.0.0-py3-none-any.whl src2sink-build --help
```

## 6. Rehearse on TestPyPI (optional, but do it for packaging changes)

```sh
uv publish --publish-url https://test.pypi.org/legacy/ \
           --token "$TEST_PYPI_TOKEN" dist/*
uv run --isolated --no-project --python 3.14 \
  --index https://test.pypi.org/simple/ --index-strategy unsafe-best-match \
  --with src2sink src2sink-build --help
```

TestPyPI does not mirror all dependencies, hence the fallback index. If the
install resolves and the entry point runs, the real upload will behave.

## 7. Publish

```sh
uv publish --dry-run dist/*            # what would be uploaded, no upload
uv publish dist/*                      # uses $UV_PUBLISH_TOKEN
```

Equivalent with twine, if you prefer it:

```sh
uv run --with twine twine upload dist/*
```

Then verify from a clean environment:

```sh
uv run --isolated --no-project --python 3.14 --with src2sink src2sink-build --help
```

## 8. Publish the GitHub release

Use the version's `CHANGELOG.md` section as the body — one source of truth, so
the GitHub release and the changelog can never disagree:

```sh
# everything between this version's heading and the next one
awk '/^## \[1\.0\.0\]/{f=1; next} /^## \[/{f=0} f' CHANGELOG.md > /tmp/notes.md
gh release create v1.0.0 dist/* --title "src2sink 1.0.0" --notes-file /tmp/notes.md
```

Attaching the artefacts gives anyone who cannot reach PyPI a checksum-comparable
copy of exactly what was published.

---

## If something goes wrong

- **Bad artefact already uploaded.** You cannot overwrite it. Yank the release
  (`pypi.org` → *Manage* → *Yank*), which hides it from new resolutions while
  leaving existing pins working, then fix forward with a patch version.
- **Wrong tag.** If nothing has been published yet:
  `git tag -d v1.0.0 && git push --delete origin v1.0.0`. Once a version is on
  PyPI, leave the tag alone — it is the provenance record for what shipped.
- **Token leaked.** Revoke it at <https://pypi.org/manage/account/token/>
  immediately, then issue a project-scoped replacement.

## Worth doing next

Move publishing to **PyPI Trusted Publishing** (OIDC): a `release.yml` workflow
triggered on `v*` tags, with a `pypi` environment and `id-token: write`. It
removes the long-lived token entirely and makes the uploaded artefact the one CI
built from the tag, not one built on a laptop. Tracked in
[`todo.md`](todo.md) when someone picks it up.
