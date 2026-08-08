"""`OI-32`, 4.0 phase 0: the run reports where its time went.

Every performance number in 3.0.0 came from instrumenting a build by hand, and
the one that mattered most — aggregation being 78% of the run — was found only
because someone went looking. These tests hold the two properties that make the
manifest field a substitute for that: the tree must nest the way the code nests,
and time nothing claimed must be *reported* as unclaimed rather than absorbed
into a neighbour.

The second is the one worth guarding. A timing table whose parts silently sum to
the whole is the same failure as `OI-36`: a confident answer with a hole in it,
and nothing in the output to say so.
"""

from __future__ import annotations

import threading

import pytest

from src2sink import run_timing


@pytest.fixture(autouse=True)
def _clean():
    """The recorder is process-global, so each test starts from empty."""
    run_timing.reset()
    yield
    run_timing.reset()


def _record(name: str, seconds: float, clock: list[float]) -> None:
    """Enter `name` and advance the fake clock inside it."""
    with run_timing.phase(name):
        clock[0] += seconds


@pytest.fixture
def clock(monkeypatch):
    """A perf_counter the test drives, so durations are exact rather than flaky."""
    now = [0.0]
    monkeypatch.setattr(run_timing.time, "perf_counter", lambda: now[0])
    return now


# --- the tree -----------------------------------------------------------------


def test_phases_nest_the_way_the_code_nests(clock):
    """A step inside a step must be reported inside it, not beside it."""
    with run_timing.phase("aggregation"):
        _record("shared-load", 3.0, clock)
        _record("payload-producers", 7.0, clock)

    [aggregation] = run_timing.timings(10.0)
    assert aggregation["phase"] == "aggregation"
    assert aggregation["seconds"] == 10.0
    assert [s["phase"] for s in aggregation["steps"]] == [
        "shared-load", "payload-producers",
    ]


def test_shares_are_of_the_whole_run_at_every_depth(clock):
    """The comparison that found the 78% crosses levels, so it must not need arithmetic."""
    with run_timing.phase("aggregation"):
        _record("payload-producers", 39.0, clock)
        clock[0] += 1.0
    _record("extraction", 10.0, clock)

    rows = run_timing.timings(50.0)
    aggregation, extraction = rows[0], rows[1]
    assert aggregation["share"] == 0.8
    assert extraction["share"] == 0.2
    producers = aggregation["steps"][0]
    assert producers["share"] == 0.78, "a sub-step's share is of the run, not of its parent"


def test_a_repeated_phase_accumulates_and_counts(clock):
    """Wrapping something in a loop must give a total, not one row per iteration."""
    for _ in range(3):
        _record("per-repo", 2.0, clock)

    [row] = run_timing.timings(6.0)
    assert row["seconds"] == 6.0
    assert row["calls"] == 3


def test_a_single_call_does_not_carry_a_count(clock):
    """`calls: 1` on every row would be noise on the ordinary case."""
    _record("extraction", 5.0, clock)
    assert "calls" not in run_timing.timings(5.0)[0]


# --- the honesty properties ---------------------------------------------------


def test_time_no_phase_claimed_is_reported(clock):
    """The property that makes the table trustworthy rather than merely tidy."""
    _record("extraction", 10.0, clock)
    clock[0] += 90.0    # something nobody wrapped

    rows = run_timing.timings(100.0)
    assert [r["phase"] for r in rows] == ["extraction", "unattributed"]
    assert rows[1]["seconds"] == 90.0
    assert rows[1]["share"] == 0.9


def test_a_parents_unclaimed_time_is_reported_inside_it(clock):
    """A gap must be attributed to the phase that has it, not to the run at large."""
    with run_timing.phase("aggregation"):
        _record("shared-load", 1.0, clock)
        clock[0] += 9.0

    [aggregation] = run_timing.timings(10.0)
    assert [s["phase"] for s in aggregation["steps"]] == ["shared-load", "unattributed"]
    assert aggregation["steps"][1]["seconds"] == 9.0


def test_a_negligible_remainder_is_not_reported(clock):
    """Below the noise floor the line is rounding across a few clock reads.

    Printing it every run teaches the reader to skip the row, which is how they
    come to skip it on the run where it is 90%.
    """
    _record("extraction", 99.9, clock)
    assert [r["phase"] for r in run_timing.timings(100.0)] == ["extraction"]


def test_a_leaf_phase_reports_no_gap(clock):
    """A phase with no sub-steps has not failed to account for anything."""
    _record("extraction", 5.0, clock)
    assert "steps" not in run_timing.timings(5.0)[0]


# --- the edges ----------------------------------------------------------------


def test_a_phase_that_raises_is_still_timed(clock):
    """A run that died partway is exactly when you want to know where it was."""
    with pytest.raises(ValueError):
        with run_timing.phase("extraction"):
            clock[0] += 4.0
            raise ValueError("boom")

    rows = run_timing.timings(4.0)
    assert rows[0]["phase"] == "extraction"
    assert rows[0]["seconds"] == 4.0


def test_a_raise_does_not_leave_the_tree_mis_nested(clock):
    """An unbalanced stack would nest every later phase under the dead one."""
    with pytest.raises(ValueError):
        with run_timing.phase("aggregation"):
            raise ValueError("boom")
    _record("extraction", 1.0, clock)

    assert [r["phase"] for r in run_timing.timings(1.0)] == ["aggregation", "extraction"]


def test_a_worker_thread_does_not_corrupt_the_tree(clock):
    """`OI-32` step 2 proposes threading the reads; this is what that must not break.

    A worker's phase would interleave with the main thread's and nest under
    whatever happened to be open. Its time is still visible — as the enclosing
    phase's unattributed remainder — which is a gap that reads as a gap rather
    than a confident wrong number.
    """
    def worker() -> None:
        with run_timing.phase("from-a-thread"):
            pass

    with run_timing.phase("extraction"):
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        clock[0] += 5.0

    [extraction] = run_timing.timings(5.0)
    assert extraction["phase"] == "extraction"
    assert "steps" not in extraction, "a worker thread must not append to the tree"


def test_reset_clears_a_previous_run(clock):
    """Two runs in one process must not report the first one's phases."""
    _record("extraction", 1.0, clock)
    run_timing.reset()
    _record("discovery", 1.0, clock)
    assert [r["phase"] for r in run_timing.timings(1.0)] == ["discovery"]


def test_a_zero_length_run_does_not_divide_by_zero():
    """Aggregate-only over an empty metabase can finish inside the clock's resolution."""
    assert run_timing.timings(0.0) == []


# --- the printed form ---------------------------------------------------------


def test_the_printed_table_shows_the_nesting_and_the_total(clock):
    """The manifest is where you look once you suspect; this is what makes you suspect."""
    with run_timing.phase("aggregation"):
        _record("payload-producers", 78.0, clock)
        clock[0] += 2.0
    _record("extraction", 20.0, clock)

    lines = run_timing.render_lines(100.0)
    assert lines[0] == "timing: 100.0s total"
    assert "aggregation" in lines[1] and "80.0%" in lines[1]
    assert lines[2].index("payload-producers") > lines[1].index("aggregation"), (
        "a sub-step must be indented under its parent"
    )
    assert "78.0%" in lines[2]
