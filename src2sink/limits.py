"""Resource bulkheads for scanning untrusted repositories.

A single crafted file can make a worker hang (a non-interruptible tree-sitter C
parse) or peg a CPU (catastrophic regex backtracking). Neither a Python signal
nor a ``concurrent.futures`` cancellation can stop C-level work, so the only
reliable way to reclaim a stuck worker is to run each unit of work in its own
process and ``terminate()`` it if it exceeds a wall-clock budget.

:func:`map_with_timeout` is that bulkhead: it runs ``func(item)`` for each item
in a dedicated process, bounded to ``workers`` concurrent processes, and kills
any process that overruns ``timeout`` seconds — yielding a timeout marker for it
and moving on so one hostile repo cannot stall the whole run. See
docs/threat-model.md finding D-1.
"""

from __future__ import annotations

import faulthandler
import multiprocessing as mp
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

# Defaults (overridable via CLI). A repo exceeding the timeout is killed and
# recorded; a repo with more than the file cap is truncated with a note.
DEFAULT_PER_REPO_TIMEOUT_S = 300
DEFAULT_MAX_FILES_PER_REPO = 50_000

_TERMINATE_GRACE_S = 5.0
_POLL_INTERVAL_S = 0.05


def _entry(func, item, initializer, initargs, timeout, conn) -> None:  # pragma: no cover - runs in child
    """Child-process entry point: init, run ``func(item)``, send the result back."""
    # If we hang past the deadline, dump a traceback to stderr before the parent
    # kills us — so a hostile/pathological repo leaves a diagnostic breadcrumb.
    if timeout and timeout > 0:
        faulthandler.dump_traceback_later(timeout, exit=False)
    try:
        if initializer is not None:
            initializer(*initargs)
        result = func(item)
        conn.send(result)
    except BaseException as exc:  # noqa: BLE001 - report, never crash silently
        try:
            conn.send({"_error": True, "error": type(exc).__name__})
        except Exception:
            pass
    finally:
        faulthandler.cancel_dump_traceback_later()
        conn.close()


def _kill(proc: "mp.process.BaseProcess") -> None:
    """Terminate a worker, escalating to ``kill()`` if it ignores the grace period."""
    if proc.is_alive():
        proc.terminate()
        proc.join(_TERMINATE_GRACE_S)
    if proc.is_alive():
        proc.kill()
        proc.join()


def _close(conn: Any) -> None:
    """Close a pipe endpoint, ignoring an already-closed descriptor."""
    try:
        conn.close()
    except OSError:
        pass


def _collect(conn: Any) -> Any:
    """Read a finished child's result, or an error marker if it sent nothing."""
    try:
        if conn.poll():
            return conn.recv()
    except (EOFError, OSError):
        pass
    finally:
        _close(conn)
    return {"_error": True, "error": "worker exited without result"}


def _poll_worker(r: dict[str, Any], on_timeout: "Callable[[Any], Any] | None") -> tuple[bool, Any]:
    """Check one running worker: return (finished, result-or-timeout-marker)."""
    proc = r["proc"]
    if not proc.is_alive():
        result = _collect(r["conn"])
        proc.join()
        return True, result
    if r["deadline"] is not None and time.monotonic() > r["deadline"]:
        _kill(proc)
        _close(r["conn"])
        return True, (on_timeout(r["item"]) if on_timeout else {"_timeout": True})
    return False, None


def map_with_timeout(
    func: Callable[[Any], Any],
    items: Iterable[Any],
    *,
    workers: int = 1,
    timeout: float = DEFAULT_PER_REPO_TIMEOUT_S,
    initializer: Callable[..., None] | None = None,
    initargs: tuple[Any, ...] = (),
    on_timeout: Callable[[Any], Any] | None = None,
) -> Iterator[Any]:
    """Run ``func(item)`` for each item in its own process, killing overruns.

    Yields each item's return value in completion order. For an item that
    exceeds ``timeout`` seconds, its process is terminated and ``on_timeout(item)``
    is yielded instead (default ``{"_timeout": True}``). At most ``workers``
    processes run at once. ``timeout <= 0`` disables the time limit.

    ``func`` and ``initializer`` must be importable (picklable) because the
    child processes may be spawned, not forked.
    """
    ctx = mp.get_context()
    items = list(items)
    workers = max(1, workers)
    use_timeout = bool(timeout and timeout > 0)
    next_idx = 0
    running: list[dict[str, Any]] = []

    def _spawn(item: Any) -> dict[str, Any]:
        """Start a worker process for ``item`` and return its tracking record."""
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_entry,
            args=(func, item, initializer, initargs, timeout, child_conn),
            daemon=True,
        )
        proc.start()
        child_conn.close()  # only the child keeps its end
        deadline = time.monotonic() + timeout if use_timeout else None
        return {"proc": proc, "conn": parent_conn, "item": item, "deadline": deadline}

    try:
        while next_idx < len(items) or running:
            while len(running) < workers and next_idx < len(items):
                running.append(_spawn(items[next_idx]))
                next_idx += 1

            progressed = False
            still: list[dict[str, Any]] = []
            for r in running:
                done, result = _poll_worker(r, on_timeout)
                if done:
                    progressed = True
                    yield result
                else:
                    still.append(r)
            running = still

            if running and not progressed:
                time.sleep(_POLL_INTERVAL_S)
    finally:
        # Never leave orphaned workers behind (e.g. on generator close).
        for r in running:
            _kill(r["proc"])
            _close(r["conn"])
