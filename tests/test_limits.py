"""TA-001 — execution bulkhead tests (threat-model D-1).

Verifies that map_with_timeout runs work in isolated processes and that a single
hanging/looping unit of work is killed and reported without stalling the rest of
the run — the core availability guarantee for scanning hostile repos. Also
covers the per-repo file cap enforced in analyse_repo_v2 (D-4).

The dispatcher is exercised with picklable stdlib callables (abs / int /
time.sleep) so the tests work regardless of the multiprocessing start method
(spawn on macOS) without needing custom importable helper modules.
"""

from __future__ import annotations

import os
import time

import pytest

from src2sink import build_metabase_v2
from src2sink.build_metabase_v2 import analyse_repo_v2
from src2sink.limits import map_with_timeout


def test_map_with_timeout_runs_all_fast_items():
    results = sorted(map_with_timeout(abs, [-1, -2, -3], workers=2, timeout=10))
    assert results == [1, 2, 3]


def test_map_with_timeout_reports_worker_error():
    results = list(map_with_timeout(int, ["not-a-number"], workers=1, timeout=10))
    assert len(results) == 1
    assert results[0].get("_error") is True
    assert results[0].get("error") == "ValueError"


@pytest.mark.watchdog(30)
def test_map_with_timeout_kills_hang():
    start = time.monotonic()
    results = list(
        map_with_timeout(
            time.sleep,
            [60],
            workers=1,
            timeout=1,
            on_timeout=lambda item: {"_timeout": True, "seconds": item},
        )
    )
    elapsed = time.monotonic() - start
    assert results == [{"_timeout": True, "seconds": 60}]
    # Must be reclaimed promptly (~timeout + grace), nowhere near the 60s sleep.
    assert elapsed < 20


@pytest.mark.watchdog(30)
def test_one_hang_does_not_block_other_items():
    # A single func whose behavior varies by input: 0.01s = fast, 60s = hang.
    start = time.monotonic()
    results = list(
        map_with_timeout(time.sleep, [0.01, 60, 0.01], workers=3, timeout=2)
    )
    elapsed = time.monotonic() - start
    successes = [r for r in results if r is None]  # time.sleep returns None
    timeouts = [r for r in results if isinstance(r, dict) and r.get("_timeout")]
    assert len(successes) == 2
    assert len(timeouts) == 1
    assert elapsed < 20  # the two fast items are not blocked by the hang


def test_map_with_timeout_reports_worker_that_exits_silently():
    # os._exit bypasses _entry's try/finally, so the child sends nothing.
    results = list(map_with_timeout(os._exit, [0], workers=1, timeout=10))
    assert len(results) == 1
    assert results[0].get("_error") is True
    assert "without result" in results[0].get("error", "")


def test_map_with_timeout_disabled_runs_to_completion():
    # timeout <= 0 disables the limit; only pass non-hanging work here.
    assert list(map_with_timeout(abs, [-5], workers=1, timeout=0)) == [5]


# ---------------------------------------------------------------------------
# File cap (D-4)
# ---------------------------------------------------------------------------


def test_file_cap_truncates_with_note(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for i in range(4):
        (repo / "src" / f"F{i}.java").write_text(f"class F{i} {{}}", encoding="utf-8")

    monkeypatch.setattr(build_metabase_v2, "_MAX_FILES_PER_REPO", 2)
    summary = analyse_repo_v2(repo, "grp", "repo", "grp/repo")
    assert any("file cap reached (2)" in n for n in summary.notes)
    # At most the cap's worth of files were counted toward the language breakdown.
    assert sum(summary.language_breakdown.values()) <= 2


def test_no_file_cap_scans_all(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for i in range(4):
        (repo / "src" / f"F{i}.java").write_text(f"class F{i} {{}}", encoding="utf-8")

    monkeypatch.setattr(build_metabase_v2, "_MAX_FILES_PER_REPO", 0)  # disabled
    summary = analyse_repo_v2(repo, "grp", "repo", "grp/repo")
    assert not any("file cap reached" in n for n in summary.notes)
    assert summary.language_breakdown.get("java") == 4


def test_oversized_file_skipped_with_note(tmp_path, monkeypatch):
    """TA-006 — a file over --max-file-bytes is skipped AND recorded (no silent cap)."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Small.java").write_text("class Small {}", encoding="utf-8")
    (repo / "src" / "Big.java").write_text("class Big {}\n" + "// x\n" * 5000, encoding="utf-8")

    monkeypatch.setattr(build_metabase_v2, "_MAX_FILE_BYTES", 200)
    summary = analyse_repo_v2(repo, "grp", "repo", "grp/repo")
    note = next((n for n in summary.notes if "Big.java" in n), "")
    assert "file exceeds size cap" in note
    assert "--max-file-bytes" in note              # tells the user which knob
    assert summary.language_breakdown.get("java") == 1  # only Small.java parsed


def test_zero_disables_size_cap(tmp_path, monkeypatch):
    """--max-file-bytes 0 reads any size and records no size-cap note."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Big.java").write_text("class Big {}\n" + "// x\n" * 5000, encoding="utf-8")

    monkeypatch.setattr(build_metabase_v2, "_MAX_FILE_BYTES", 0)  # disabled
    summary = analyse_repo_v2(repo, "grp", "repo", "grp/repo")
    assert not any("file exceeds size cap" in n for n in summary.notes)
    assert summary.language_breakdown.get("java") == 1


def test_limits_hit_summary(tmp_path):
    """The run-end summary names each limit that skipped content, else is empty."""
    from src2sink.build_metabase_v2 import _limits_hit_summary
    import json

    repo_dir = tmp_path / "repos" / "grp"
    repo_dir.mkdir(parents=True)
    (repo_dir / "a.json").write_text(json.dumps({"notes": [
        "skipped src/Big.java: file exceeds size cap (9 > 2 bytes; raise --max-file-bytes)",
        "file cap reached (50000); remaining files skipped",
    ]}), encoding="utf-8")

    out = _limits_hit_summary([repo_dir / "a.json"], timed_out=1)
    assert "--max-file-bytes" in out
    assert "--max-files-per-repo" in out
    assert "--repo-timeout" in out               # from the timed_out counter
    # Nothing skipped anywhere → empty suffix.
    (repo_dir / "clean.json").write_text(json.dumps({"notes": []}), encoding="utf-8")
    assert _limits_hit_summary([repo_dir / "clean.json"], timed_out=0) == ""


# ---------------------------------------------------------------------------
# The bulkhead's own promises (WI-10, Tier A)
#
# A mutation sweep left 25 survivors in this module. The existing tests prove a
# hang is killed and the run continues, which is the headline guarantee — but
# not the mechanisms it rests on. Everything below is a promise the module makes
# that nothing was checking.
# ---------------------------------------------------------------------------

class _StubProcess:
    """A process-like object that can be told to ignore ``terminate()``.

    Standing in for a real child keeps this a unit test of the escalation logic:
    a process that ignores SIGTERM needs a custom signal handler in the child,
    and the suite deliberately uses only picklable stdlib callables so it works
    under spawn.
    """

    def __init__(self, *, ignores_terminate: bool) -> None:
        self._ignores_terminate = ignores_terminate
        self.alive = True
        self.calls: list[str] = []

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.calls.append("terminate")
        if not self._ignores_terminate:
            self.alive = False

    def kill(self) -> None:
        self.calls.append("kill")
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.calls.append(f"join({timeout})")


def test_kill_escalates_when_a_worker_ignores_terminate():
    """SIGTERM is a request; a hostile or wedged worker can decline it.

    Escalating to kill() is the difference between the bulkhead holding and a
    scan hanging forever on one repo, and nothing tested that the second step
    happens.
    """
    from src2sink.limits import _TERMINATE_GRACE_S, _kill

    proc = _StubProcess(ignores_terminate=True)
    _kill(proc)
    assert proc.calls == ["terminate", f"join({_TERMINATE_GRACE_S})", "kill", "join(None)"]
    assert not proc.alive


def test_kill_does_not_escalate_when_terminate_is_enough():
    """The grace period is real: a cooperative worker is never SIGKILLed."""
    from src2sink.limits import _kill

    proc = _StubProcess(ignores_terminate=False)
    _kill(proc)
    assert "kill" not in proc.calls


def test_kill_is_a_no_op_for_an_already_dead_worker():
    """Reaping a finished worker must not signal a recycled pid."""
    from src2sink.limits import _kill

    proc = _StubProcess(ignores_terminate=False)
    proc.alive = False
    _kill(proc)
    assert proc.calls == []


@pytest.mark.watchdog(30)
def test_worker_count_below_one_is_normalised():
    """`workers=0` must still make progress rather than spin on an empty pool.

    The value reaches this function from a CLI flag, so zero is reachable from
    outside. Without the normalisation the dispatcher never starts a process and
    never terminates.
    """
    assert sorted(map_with_timeout(abs, [-1, -2], workers=0, timeout=10)) == [1, 2]


@pytest.mark.watchdog(30)
def test_closing_the_generator_leaves_no_workers_running():
    """Abandoning the iterator must not leak processes.

    The `finally` block exists for exactly this, and a caller that stops early —
    an exception downstream, a `break`, a consumer giving up — is the normal case
    rather than an exotic one. One quick item lets the first `next()` return
    while two long ones are still running, which is the state that leaks.
    """
    import multiprocessing as mp

    gen = map_with_timeout(time.sleep, [0.05, 30, 30], workers=3, timeout=60)
    next(gen)  # returns as soon as the quick item finishes; two workers remain
    assert mp.active_children(), "fixture must leave workers running to be a test"

    gen.close()

    deadline = time.monotonic() + 10
    while mp.active_children() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not mp.active_children(), "generator close left workers running"
