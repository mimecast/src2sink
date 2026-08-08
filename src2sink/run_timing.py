"""Where a run spent its time, recorded by the run itself (`OI-32`, 4.0 phase 0).

`run-manifest.json` recorded `started_at` and `finished_at` and nothing between
them, so the only answerable question was how long the whole thing took. Every
number in the 3.0.0 performance work came from instrumenting a build by hand —
including the finding that **aggregation was 78% of the run**, which nobody
suspected and which redirected the entire effort. A measurement that important
should not depend on someone deciding to go looking.

So the run times itself. `phase()` wraps a step, nests under whatever step is
already running, and costs one `perf_counter` call — cheap enough to leave on
permanently, which is the point: an optional profiler tells you about the run you
profiled, and a manifest field tells you about the run that was slow.

Two things it deliberately does *not* do:

* **It does not claim the parts sum to the whole.** Time inside a phase that no
  child accounted for is emitted as `unattributed` rather than dropped. A gap
  that reads as a gap is worth more than a tidy table that implies full coverage
  it does not have — `OI-36`, applied to measurement.
* **It does not record from worker threads.** Their phases would interleave with
  the main thread's and mis-nest the tree, and a confidently wrong number is
  worse than a missing one. Their time still appears, inside the enclosing
  phase's remainder.

Shares are always of the **whole run**, at every depth, so a sub-step can be
compared against a top-level phase without arithmetic. That comparison is the
one that found the 78%.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# An unmeasured remainder below this share of the run is rounding noise across a
# few dozen `perf_counter` reads, and printing it would train readers to ignore
# the line that matters when it is genuinely large.
NOISE_FLOOR = 0.005

_root: list[dict[str, Any]] = []
_stack: list[dict[str, Any]] = []


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time a named step, nested under whatever phase is already running.

    Re-entering a name at the same level accumulates into the one entry and
    counts the calls, so wrapping something inside a loop yields a total rather
    than hundreds of rows.

    The timing is recorded even when the body raises: a phase that died partway
    still consumed the time it consumed, and losing that is how a crashing run
    becomes unexplainable.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    siblings = _stack[-1]["steps"] if _stack else _root
    entry = next((e for e in siblings if e["phase"] == name), None)
    if entry is None:
        entry = {"phase": name, "elapsed": 0.0, "calls": 0, "steps": []}
        siblings.append(entry)
    entry["calls"] += 1
    _stack.append(entry)
    started = time.perf_counter()
    try:
        yield
    finally:
        entry["elapsed"] += time.perf_counter() - started
        _stack.pop()


def reset() -> None:
    """Forget everything recorded so far.

    The recorder is process-global, which is what lets a phase deep inside an
    aggregator nest under one opened in `main` without either knowing about the
    other. The cost is that a second run in the same process would otherwise
    accumulate onto the first — so tests, and any in-process re-run, start here.
    """
    _root.clear()
    _stack.clear()


def timings(total_seconds: float) -> list[dict[str, Any]]:
    """The recorded tree as manifest-ready data, with shares and remainders.

    `total_seconds` is the whole run's wall clock, including whatever was never
    wrapped in a phase at all — so the top-level `unattributed` entry is a true
    statement about coverage, not a rounding artefact.
    """
    return _render(_root, total_seconds, total_seconds)


def _render(
    entries: list[dict[str, Any]], parent_seconds: float, total: float
) -> list[dict[str, Any]]:
    """Convert recorded entries to output rows, appending any unmeasured remainder."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        row: dict[str, Any] = {
            "phase": entry["phase"],
            "seconds": round(entry["elapsed"], 2),
        }
        if total > 0:
            row["share"] = round(entry["elapsed"] / total, 4)
        if entry["calls"] > 1:
            row["calls"] = entry["calls"]
        steps = _render(entry["steps"], entry["elapsed"], total)
        if steps:
            row["steps"] = steps
        out.append(row)

    remainder = parent_seconds - sum(e["elapsed"] for e in entries)
    if out and total > 0 and remainder / total >= NOISE_FLOOR:
        out.append({
            "phase": "unattributed",
            "seconds": round(remainder, 2),
            "share": round(remainder / total, 4),
        })
    return out


def render_lines(total_seconds: float, rows: list[dict[str, Any]] | None = None) -> list[str]:
    """Format the tree for stdout, one indented line per phase.

    Printed at the end of every run because the manifest is where you look once
    you already suspect something; this is what makes the shape of the run
    unavoidable to the person who just watched it happen.
    """
    rows = timings(total_seconds) if rows is None else rows
    lines = [f"timing: {total_seconds:.1f}s total"]
    # One label width for the whole tree, so the seconds and share columns line
    # up across depths. Comparing a sub-step against a top-level phase is the
    # comparison this exists for, and a ragged column makes the eye do work the
    # numbers already did.
    _append_lines(rows, lines, depth=1, width=_label_width(rows, depth=1))
    return lines


def _label_width(rows: list[dict[str, Any]], *, depth: int) -> int:
    """The widest indented label anywhere in the tree."""
    return max(
        (
            max(len(r["phase"]) + 2 * depth, _label_width(r.get("steps", []), depth=depth + 1))
            for r in rows
        ),
        default=0,
    )


def _append_lines(
    rows: list[dict[str, Any]], lines: list[str], *, depth: int, width: int
) -> None:
    """Append one line per row, recursing into steps at a deeper indent."""
    for row in rows:
        label = "  " * depth + row["phase"]
        calls = f"  x{row['calls']}" if row.get("calls") else ""
        share = f"{row.get('share', 0.0) * 100:5.1f}%"
        lines.append(f"  {label:<{width}}  {row['seconds']:8.1f}s  {share}{calls}")
        _append_lines(row.get("steps", []), lines, depth=depth + 1, width=width)
