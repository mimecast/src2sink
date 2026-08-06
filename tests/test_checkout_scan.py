"""OI-31: the checkout was walked twenty-five times to find a handful of files.

`Path.rglob(name)` traverses the whole tree and filters by name, so asking for
four filenames costs four full traversals. Several scanners did that, and none
shared a walk with any other. Measured on one run before the fix:

```
aggregation            :  10 full walks    8x discover_openapi_specs (4 globs x 2 call sites)
                                           2x discover_helm_hosts
--discover-api-clients :  15 more         15x _iter_manifests
TOTAL                  :  25
```

The same defect `OI-30` fixed in the producer scan — the loop over *what to look
for* outside the loop over *where to look* — in two more places, which is why the
fix is a shared walk rather than a third local repair.

The count tests below matter, but the ones that matter more are the correctness
ones: a faster scan that finds different files is not a fix. In particular the
`SKIP_DIRS` prefix case, which this walk got wrong on the first attempt and
`test_skip_dirs_apply_below_the_root_only` caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src2sink import checkout_scan as cs


@pytest.fixture(autouse=True)
def _clean_cache():
    """The walk is cached per root; every test starts from a cold one."""
    cs.clear_cache()
    yield
    cs.clear_cache()


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    """A checkout with the shapes every caller cares about."""
    root = tmp_path / "repos"
    _write(root / "g/a/pom.xml")
    _write(root / "g/a/openapi.yaml")
    _write(root / "g/b/package.json")
    _write(root / "g/b/values.yaml")
    _write(root / "g/c/Thing.csproj")
    _write(root / "g/c/node_modules/pkg/pom.xml")   # must be skipped
    _write(root / "g/c/target/pom.xml")             # must be skipped
    return root


def _count_walks(monkeypatch) -> list[int]:
    """Return a one-element list counting full `rglob` traversals."""
    calls = [0]
    original = Path.rglob

    def counted(self, pattern, *a, **kw):
        calls[0] += 1
        return original(self, pattern, *a, **kw)

    monkeypatch.setattr(Path, "rglob", counted)
    return calls


def test_many_patterns_cost_one_walk(tree, monkeypatch):
    """The whole point. Four filenames used to mean four traversals."""
    calls = _count_walks(monkeypatch)
    found = cs.paths_by_name(tree, frozenset({
        "pom.xml", "package.json", "openapi.yaml", "values.yaml",
    }))
    assert calls[0] == 1
    assert [p.name for p in found["pom.xml"]] == ["pom.xml"]
    assert [p.name for p in found["package.json"]] == ["package.json"]


def test_a_second_caller_wanting_the_same_patterns_does_not_walk_again(tree, monkeypatch):
    """Aggregation calls the OpenAPI discovery from two places."""
    names = frozenset({"openapi.yaml"})
    cs.paths_by_name(tree, names)
    calls = _count_walks(monkeypatch)
    cs.paths_by_name(tree, names)
    assert calls[0] == 0


def test_a_subset_is_served_from_a_wider_walk(tree, monkeypatch):
    """A later phase asking for less must not start its own traversal."""
    cs.paths_by_name(tree, frozenset({"pom.xml", "package.json", "openapi.yaml"}))
    calls = _count_walks(monkeypatch)
    found = cs.paths_by_name(tree, frozenset({"pom.xml"}))
    assert calls[0] == 0
    assert [p.name for p in found["pom.xml"]] == ["pom.xml"]


def test_a_new_pattern_widens_the_walk_rather_than_forking_it(tree):
    """So the *next* caller is served too, whichever phase it belongs to.

    This is what lets aggregation and `--discover-api-clients` converge on one
    traversal without either having to know the other exists.
    """
    cs.paths_by_name(tree, frozenset({"pom.xml"}))
    cs.paths_by_name(tree, frozenset({"package.json"}))
    assert len(cs._CACHE) == 1, "a widened walk, not two private ones"
    assert cs._CACHE[tree.resolve()].patterns == {"pom.xml", "package.json"}


def test_prewalk_makes_the_whole_run_one_traversal(tree, monkeypatch):
    """What the CLI does when it knows every phase's patterns up front."""
    cs.prewalk(tree, frozenset({"pom.xml"}), frozenset({"openapi.yaml", "values.yaml"}))
    calls = _count_walks(monkeypatch)
    cs.paths_by_name(tree, frozenset({"pom.xml"}))
    cs.paths_by_name(tree, frozenset({"openapi.yaml"}))
    cs.paths_by_name(tree, frozenset({"values.yaml"}))
    assert calls[0] == 0


def test_skip_dirs_are_excluded(tree):
    """`node_modules` and `target` are not part of the scanned tree."""
    found = cs.paths_by_name(tree, frozenset({"pom.xml"}))
    assert [str(p.relative_to(tree)) for p in found["pom.xml"]] == ["g/a/pom.xml"]


def test_skip_dirs_match_below_the_root_only(tmp_path):
    """A repos root under `/tmp/build/` must not exclude its whole tree.

    The first version of this walk matched `SKIP_DIRS` against the *absolute*
    path, which silently emptied the index for any operator whose checkout sits
    under a colliding prefix — and made everything resolve to "not found" with no
    error. `repo_utils` documents the same trap; this walk fell into it anyway,
    which is why the rule is asserted here as well as there.
    """
    root = tmp_path / "tmp" / "build" / "repos"      # hostile prefix
    _write(root / "g/a/pom.xml")
    found = cs.paths_by_name(root, frozenset({"pom.xml"}))
    assert [str(p.relative_to(root)) for p in found["pom.xml"]] == ["g/a/pom.xml"]


def test_a_glob_pattern_matches_by_extension(tree):
    """`*.csproj` and friends are real globs, not filenames."""
    found = cs.paths_by_name(tree, frozenset({"*.csproj"}))
    assert [p.name for p in found["*.csproj"]] == ["Thing.csproj"]


def test_an_exact_name_wins_over_a_glob(tree):
    """A caller iterating its patterns must not process one file twice."""
    found = cs.paths_by_name(tree, frozenset({"pom.xml", "*.xml"}))
    assert [p.name for p in found["pom.xml"]] == ["pom.xml"]
    assert found["*.xml"] == [], "already claimed by the exact name"


def test_a_pattern_matching_nothing_maps_to_empty(tree):
    """A caller never has to tell 'not found' from 'not asked for'."""
    found = cs.paths_by_name(tree, frozenset({"Cargo.toml"}))
    assert found == {"Cargo.toml": []}


def test_a_missing_checkout_is_not_an_error(tmp_path):
    """The scan is optional, so its absence degrades rather than raises."""
    assert cs.paths_by_name(tmp_path / "nope", frozenset({"pom.xml"})) == {"pom.xml": []}
    cs.prewalk(tmp_path / "nope", frozenset({"pom.xml"}))  # must not raise


def test_results_are_sorted(tmp_path):
    """Two runs over one checkout must agree, and directory order is not stable."""
    root = tmp_path / "repos"
    for name in ("z", "a", "m"):
        _write(root / "g" / name / "pom.xml")
    found = cs.paths_by_name(root, frozenset({"pom.xml"}))
    assert [p.parent.name for p in found["pom.xml"]] == ["a", "m", "z"]


def test_the_discovery_phases_share_one_walk(tree, monkeypatch):
    """End to end, at the level the 70-minute report was made at.

    `discover_openapi_specs` is called from two places and `discover_helm_hosts`
    from a third; between them they used to cost ten traversals.
    """
    from src2sink.aggregators.openapi_discovery import (
        discover_helm_hosts,
        discover_openapi_specs,
    )

    calls = _count_walks(monkeypatch)
    discover_openapi_specs(tree)
    discover_openapi_specs(tree)
    discover_helm_hosts(tree)
    assert calls[0] <= 2, f"expected at most one walk per pattern set, got {calls[0]}"


def test_the_manifest_index_walks_once(tree, monkeypatch):
    """`_iter_manifests` was fifteen traversals per run."""
    import src2sink.repo_utils as ru

    ru._component_identity_index_cache = None
    calls = _count_walks(monkeypatch)
    ru._build_component_identity_index(tree)
    assert calls[0] == 1


# --- The CLI paths, which is where the walks actually add up -----------------


def _metabase(tmp_path):
    """A metabase with one record, enough for the aggregate-only paths."""
    import json

    root = tmp_path / "metabase"
    d = root / "repos" / "g"
    d.mkdir(parents=True)
    (d / "r.json").write_text(json.dumps({
        "schema_version": 2, "group": "g", "name": "r",
        "nodes": [], "edges": [], "dependencies_internal": [],
    }), encoding="utf-8")
    return root


@pytest.mark.parametrize("graphs_only", [True, False])
def test_discovery_runs_from_the_aggregate_only_paths(tmp_path, tree, graphs_only):
    """`--discover-api-clients` was silently ignored unless a full scan ran.

    Combined with `--graphs-only` or `--aggregate-only` the flag was accepted,
    did nothing, and printed nothing — so a user asking for candidates against an
    existing metabase got an empty result with no clue why. Discovery reads
    records and the checkout, both of which those modes have.
    """
    import argparse

    from src2sink.build_metabase_v2 import _run_aggregate_only

    metabase = _metabase(tmp_path)
    args = argparse.Namespace(
        phase3_only=False, graphs_only=graphs_only,
        no_phase3=True, discover_api_clients=True,
    )
    assert _run_aggregate_only(metabase, tree, args) == 0
    assert (metabase / "api-clients.discovered.json").is_file(), (
        "the flag must produce candidates, not silence"
    )


def test_the_aggregate_only_path_walks_the_checkout_once(tmp_path, tree, monkeypatch):
    """`--graphs-only --discover-api-clients` is two phases and one traversal."""
    import argparse

    import src2sink.repo_utils as ru
    from src2sink.build_metabase_v2 import _run_aggregate_only

    metabase = _metabase(tmp_path)
    ru._component_identity_index_cache = None
    calls = _count_walks(monkeypatch)
    _run_aggregate_only(metabase, tree, argparse.Namespace(
        phase3_only=False, graphs_only=True, no_phase3=True, discover_api_clients=True,
    ))
    assert calls[0] == 1, f"expected a single traversal, got {calls[0]}"
