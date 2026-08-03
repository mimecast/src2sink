"""Pytest configuration and shared safety harness for metabase v2 tests.

This module installs two autouse safeguards for *every* test:

1. A per-test **SIGALRM watchdog** that hard-aborts any test running longer than
   a bounded number of seconds. The project has already been bitten once by a
   runaway that consumed every core; the watchdog guarantees a future
   infinite-loop / catastrophic-backtracking / non-interruptible-parser
   regression fails fast instead of pegging the machine. It is opt-outable and
   overridable per test via markers, and it is skipped on platforms without
   ``SIGALRM`` or when not running on the main thread.

2. A **cache reset** that clears the module-global identity caches in
   ``repo_utils`` between tests, so tests remain independent.

Tests should stay single-process with tiny fixtures and must never drive the
``mp.Pool`` extraction branch (keep fixtures under 4 repos / ``workers=1``).
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

import pytest

from src2sink import repo_utils

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

# Default per-test wall-clock budget. Individual tests may override with
# ``@pytest.mark.watchdog(seconds=N)`` or disable with ``@pytest.mark.no_watchdog``.
DEFAULT_WATCHDOG_SECONDS = 15


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "fleet: requires metabase/repos v2 JSONs (optional slow check)",
    )
    config.addinivalue_line(
        "markers",
        "no_watchdog: disable the autouse per-test timeout watchdog",
    )
    config.addinivalue_line(
        "markers",
        "watchdog(seconds): override the per-test watchdog timeout (seconds)",
    )


def _watchdog_seconds(request: pytest.FixtureRequest) -> int | None:
    """Resolve the watchdog budget for this test, or ``None`` to disable it."""
    if request.node.get_closest_marker("no_watchdog") is not None:
        return None
    marker = request.node.get_closest_marker("watchdog")
    if marker is not None:
        if marker.args:
            return int(marker.args[0])
        if "seconds" in marker.kwargs:
            return int(marker.kwargs["seconds"])
    return DEFAULT_WATCHDOG_SECONDS


@pytest.fixture(autouse=True)
def _watchdog(request: pytest.FixtureRequest):
    """Abort any test that hangs, before it can consume the machine."""
    seconds = _watchdog_seconds(request)
    # SIGALRM is Unix-only and can only be armed from the main thread.
    if (
        seconds is None
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _fire(signum, frame):  # noqa: ANN001
        raise TimeoutError(
            f"test exceeded {seconds}s — likely an infinite loop, runaway "
            f"recursion, or catastrophic regex backtracking"
        )

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(autouse=True)
def _clear_repo_utils_caches():
    """Reset the module-level identity caches so each test is independent."""
    repo_utils._repo_artifact_index_cache = None
    repo_utils._component_identity_index_cache = None
    yield
    repo_utils._repo_artifact_index_cache = None
    repo_utils._component_identity_index_cache = None


@pytest.fixture(autouse=True)
def _clear_api_client_bindings():
    """Reset the configured api-client bindings between tests.

    Bindings are module-global and change extraction results: a binding's
    ``payload_fields`` raises the confidence of a ``sql-payload-out`` node, and
    its ``class_patterns`` add an entire unguarded call-site tier. A test that
    configured bindings and did not restore them therefore changed the *output*
    of every test that ran after it — which is how a committed extractor
    snapshot came to depend on collection order.

    Restoring here rather than in each test means the next one cannot reintroduce
    the leak by forgetting a ``finally``.
    """
    from src2sink import known_api_clients as kac
    from src2sink.extractors.http_out import configure_http_out_client_patterns

    yield
    kac.configure_api_client_bindings(())
    configure_http_out_client_patterns(())
