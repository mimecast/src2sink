"""One walk of the checkout, shared by everything that needs to find files in it.

`Path.rglob(name)` traverses the entire tree and filters by name, so asking for
four filenames costs four full traversals. Several scanners did exactly that, and
none of them shared a walk with any other. Measured on one run:

```
aggregation            :  10 full walks    8x discover_openapi_specs (4 globs x 2 call sites)
                                           2x discover_helm_hosts
--discover-api-clients :  15 more         15x _iter_manifests
TOTAL                  :  25
```

Twenty-five traversals of a 34 GB checkout to find files a single traversal could
have found. Same defect as `OI-30` in the producer scan — the loop over *what to
look for* sat outside the loop over *where to look* — which is why that fix is
generalised here rather than repeated a third time.

So: walk once, remember what was found, and let every caller query the result. A
later caller asking for patterns the walk already covered is served from it, and
one asking for something new *widens* the walk rather than starting a private
one — so a run that both aggregates and discovers converges on a single
traversal. :func:`prewalk` lets a caller that knows every pattern up front
guarantee it from the start.

The walk is cached per root, because a run is a batch process over a checkout
that does not change under it. `clear_cache` exists for tests and for any caller
that knows the tree moved.

Only *matching* files are kept. Holding every path in a 34 GB checkout would
trade a time problem for a memory one, and every caller here wants specific names.
"""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path

from .constants import SKIP_DIRS


class _Walk:
    """What one traversal of a checkout found, and which patterns it looked for."""

    def __init__(
        self, patterns: frozenset[str], by_filename: dict[str, list[Path]]
    ) -> None:
        """Record the patterns walked for and the files they matched."""
        self.patterns = patterns
        # Keyed by *filename*, not by pattern. Attribution to a pattern depends
        # on which patterns the caller asked for — a file claimed by an exact
        # name in one request may be a glob's match in another — so it is
        # resolved per request rather than baked in here.
        self.by_filename = by_filename


_CACHE: dict[Path, _Walk] = {}


def clear_cache() -> None:
    """Forget every cached walk. For tests, and for a caller that moved the tree."""
    _CACHE.clear()


def _is_skipped(path: Path, root: Path) -> bool:
    """True if a path segment *below* ``root`` names an excluded directory.

    Only the segments under ``root`` count. The absolute prefix is the operator's
    filesystem layout, not the scanned tree — a repos root under `/tmp/repos` or
    `~/build/repos` is perfectly legitimate, and matching `SKIP_DIRS` against it
    excludes *everything* beneath it, silently and with no error.

    Duplicates `repo_utils.is_skipped_path` rather than importing it, because
    `repo_utils` imports this module. The first version of this walk matched the
    absolute path and reintroduced exactly the defect that function's docstring
    describes; `test_skip_dirs_apply_below_the_root_only` caught it.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = path
    return any(part in SKIP_DIRS for part in rel.parts)


def _matches(filename: str, patterns: frozenset[str]) -> bool:
    """Whether a filename satisfies any pattern, exact or glob."""
    if filename in patterns:
        return True
    return any(fnmatch(filename, p) for p in patterns if "*" in p or "?" in p)


def _walk(repos_root: Path, patterns: frozenset[str]) -> _Walk:
    """Traverse the checkout once, keeping every file matching any pattern."""
    found: dict[str, list[Path]] = defaultdict(list)
    for path in repos_root.rglob("*"):
        if not _matches(path.name, patterns):
            continue
        # Checked after the match, because matching is cheap and this walks the
        # path's parts — and almost nothing matches.
        if _is_skipped(path, repos_root):
            continue
        found[path.name].append(path)
    return _Walk(patterns, {name: sorted(paths) for name, paths in found.items()})


def _cached_walk(repos_root: Path, patterns: frozenset[str]) -> _Walk:
    """The walk covering ``patterns``, reusing or widening the cached one.

    A cached walk that already looked for everything asked of it is reused
    outright. One that did not is *widened* — re-walked for the union — so the
    next caller, whichever it is, is served without another traversal. That is
    what lets aggregation and `--discover-api-clients` share a pass without
    either having to know the other exists.
    """
    key = repos_root.resolve()
    cached = _CACHE.get(key)
    if cached is not None and patterns <= cached.patterns:
        return cached
    widened = patterns if cached is None else (patterns | cached.patterns)
    walk = _walk(repos_root, widened)
    _CACHE[key] = walk
    return walk


def prewalk(repos_root: Path, *pattern_sets: frozenset[str]) -> None:
    """Walk once for every pattern a run will ask about.

    For a caller that knows all of them up front — a CLI given both
    `--api-clients` and `--discover-api-clients`, say — so the whole run costs a
    single traversal instead of one per phase.

    Purely an optimisation: every function here widens the walk on demand, so
    skipping this changes how many times the tree is read and nothing else.
    """
    if not repos_root.is_dir() or not pattern_sets:
        return
    _cached_walk(repos_root, frozenset().union(*pattern_sets))


def paths_by_name(repos_root: Path, names: frozenset[str]) -> dict[str, list[Path]]:
    """Every file under ``repos_root`` matching one of ``names``, from one walk.

    ``names`` may be exact filenames (`pom.xml`) or filename globs (`*.csproj`).
    Returns a mapping from *pattern* to the sorted paths matching it; a pattern
    absent from the tree maps to an empty list, so a caller never has to
    distinguish "not found" from "not asked for".

    A file matching several of the caller's patterns is attributed to one —
    exact names before globs — because callers iterate patterns and would
    otherwise process the same file twice.
    """
    if not repos_root.is_dir():
        return {name: [] for name in names}

    walk = _cached_walk(repos_root, names)
    exact = {n for n in names if "*" not in n and "?" not in n}
    globs = sorted(names - exact)

    out: dict[str, list[Path]] = {name: [] for name in names}
    for filename, paths in walk.by_filename.items():
        pattern = _attribute(filename, exact, globs)
        if pattern is not None:
            out[pattern].extend(paths)
    return {name: sorted(paths) for name, paths in out.items()}


def _attribute(filename: str, exact: set[str], globs: list[str]) -> str | None:
    """Which of the caller's patterns owns this filename, if any.

    Exact names win, so a caller iterating its patterns processes each file once.
    """
    if filename in exact:
        return filename
    return next((g for g in globs if fnmatch(filename, g)), None)


def iter_paths_named(repos_root: Path, names: frozenset[str], name: str) -> list[Path]:
    """The paths for one pattern, from the shared walk over ``names``.

    Convenience for a caller that queries one pattern at a time but knows the
    whole set up front — passing the full set is what collapses the walks.
    """
    return paths_by_name(repos_root, names).get(name, [])
