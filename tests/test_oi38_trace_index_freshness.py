"""OI-38: the trace index was written by the one command that makes no traces.

Found on the first completed fleet-wide batch: **94 reports written, no index
produced.**

`write_traces_index` had exactly one caller — `aggregate_graphs_v2`, the build's
aggregation phase. Neither entry point that creates traces called it. The normal
workflow is build, then trace, so the index was generated *before* any trace from
this cycle existed and therefore always described the previous batch. On a clean
metabase the traces directory does not exist during aggregation, so the index was
never written at all.

This matters beyond a stale link list, because the index states catalogue
coverage — *"N / M endpoints have traces"* — followed by a Missing traces table
and the instruction to re-run with `--skip-existing`. That is the operational
signal saying how complete the work is, and it was computed from whichever traces
happened to be on disk during the last aggregation.

Stale in the direction that matters: immediately after a batch, when someone is
checking whether the batch covered everything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src2sink.aggregators.traces_index import write_traces_index


def _metabase(tmp_path: Path, *, catalogue: list[tuple[str, str]]) -> Path:
    """A metabase with a raw-code-payload catalogue and no traces yet."""
    root = tmp_path / "metabase"
    taint = root / "taint"
    taint.mkdir(parents=True)
    (taint / "raw-code-payload-endpoints.jsonl").write_text(
        "".join(
            json.dumps({"repo": repo, "detail": {"endpoint_path": path}}) + "\n"
            for repo, path in catalogue
        ),
        encoding="utf-8",
    )
    return root


def _write_trace(root: Path, name: str, repo: str, endpoint: str) -> Path:
    """Place a trace report where the batch would, with the header the index reads.

    The index recovers (repo, endpoint) from the report's *content*, not its
    filename — so a stub without the header counts as untraced and every
    coverage assertion would pass vacuously.
    """
    traces = root / "graphs" / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    path = traces / name
    path.write_text(
        f"# Flow trace: {repo}\n\n_ Path filter: `{endpoint}`_\n",
        encoding="utf-8",
    )
    return path


def test_a_first_run_leaves_no_index_if_only_the_build_writes_it(tmp_path):
    """The observed failure, reproduced: aggregation runs before traces exist.

    `write_traces_index` opens with `if not traces_dir.is_dir(): return 0`, so on
    a clean metabase it does nothing — and the batch then writes every report
    into a directory nobody indexes afterwards.
    """
    root = _metabase(tmp_path, catalogue=[("g/r", "/run")])
    assert write_traces_index(root) == 0, "nothing to index before the batch"
    assert not (root / "graphs" / "traces" / "INDEX.md").exists()


def test_indexing_after_the_batch_finds_the_reports(tmp_path):
    """The same call, made at the right time, indexes everything."""
    root = _metabase(tmp_path, catalogue=[("g/r", "/run")])
    write_traces_index(root)                      # the build's call, too early
    _write_trace(root, "g-r-run.md", "g/r", "/run")   # the batch
    assert write_traces_index(root) == 1
    assert (root / "graphs" / "traces" / "INDEX.md").is_file()


def test_the_batch_writes_the_index(tmp_path, monkeypatch):
    """The fix at the level the report was made: run a batch, get an index."""
    from src2sink import trace_batch

    root = _metabase(tmp_path, catalogue=[("g/r", "/run")])

    def fake_batch(metabase_root, **_kw):
        _write_trace(metabase_root, "g-r-run.md", "g/r", "/run")
        return 1, 0, 0

    monkeypatch.setattr(trace_batch, "batch_trace", fake_batch)
    monkeypatch.setattr(
        "sys.argv",
        ["src2sink-trace-batch", "--metabase-root", str(root)],
    )
    assert trace_batch.main() == 0
    index = root / "graphs" / "traces" / "INDEX.md"
    assert index.is_file(), "a batch must leave an index behind"
    assert "1 / 1 endpoints have traces" in index.read_text(encoding="utf-8")


def test_the_coverage_figure_describes_the_batch_that_just_ran(tmp_path):
    """The number that says how complete this is must not be one run behind.

    Two catalogue entries, one traced. Under the defect the figure was computed
    during the build — before the second trace existed — and an operator checking
    completeness read a number about a different set.
    """
    root = _metabase(tmp_path, catalogue=[("g/r", "/one"), ("g/r", "/two")])
    _write_trace(root, "g-r-one.md", "g/r", "/one")
    write_traces_index(root)
    assert "1 / 2 endpoints have traces" in (
        root / "graphs" / "traces" / "INDEX.md"
    ).read_text(encoding="utf-8")

    _write_trace(root, "g-r-two.md", "g/r", "/two")
    write_traces_index(root)
    assert "2 / 2 endpoints have traces" in (
        root / "graphs" / "traces" / "INDEX.md"
    ).read_text(encoding="utf-8")


def test_the_build_still_indexes(tmp_path):
    """The two calls are complements, not duplicates.

    The build's call refreshes coverage when the *catalogue* moves; the batch's
    when the *traces* move. Removing either leaves one of those stale.
    """
    from src2sink.aggregators import graphs

    root = _metabase(tmp_path, catalogue=[("g/r", "/one")])
    _write_trace(root, "g-r-one.md", "g/r", "/one")
    repos = root / "repos" / "g"
    repos.mkdir(parents=True)
    record = repos / "r.json"
    record.write_text(json.dumps({
        "schema_version": 2, "group": "g", "name": "r",
        "nodes": [], "edges": [], "dependencies_internal": [],
    }), encoding="utf-8")

    graphs.aggregate_graphs_v2(root, [record], phase3=False)
    assert (root / "graphs" / "traces" / "INDEX.md").is_file()


# --- a single trace should not surprise the operator --------------------------


def test_a_single_trace_into_the_traces_dir_refreshes_the_index(tmp_path):
    """A re-traced endpoint must not leave a figure describing a set it left."""
    from src2sink.trace import _is_in_traces_dir

    root = _metabase(tmp_path, catalogue=[("g/r", "/one")])
    assert _is_in_traces_dir(root / "graphs" / "traces" / "g-r-one.md", root)


@pytest.mark.parametrize("output", ["/tmp/elsewhere.md", "report.md"])
def test_a_trace_written_elsewhere_does_not_rewrite_the_metabase(tmp_path, output):
    """`--output` to an arbitrary path has not changed the indexed set.

    Rewriting the metabase index as a side effect of writing a file somewhere
    else would be surprising, and the index would be unchanged anyway.
    """
    from src2sink.trace import _is_in_traces_dir

    root = _metabase(tmp_path, catalogue=[("g/r", "/one")])
    assert not _is_in_traces_dir(Path(output), root)
