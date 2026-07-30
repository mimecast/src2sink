"""Phase E gate: security-critical modules must stay >=90% line-covered.

The global ``--cov-fail-under=80`` in ``pyproject.toml`` only enforces the
project-wide floor. The implementation plan additionally requires the four
security modules (``limits``, ``safe_paths``, ``sanitize``, ``prescreen``) to be
>=90% covered. coverage.py's ``fail_under`` is a single global threshold with no
per-module concept, so we enforce the per-module rule here instead.

This test reads the live ``pytest-cov`` coverage object, so it must observe the
whole suite. Its filename sorts last (``test_zz_*``) so that — under pytest's
default alphabetical collection order — every other test has already contributed
to the collected data by the time it runs. If coverage is not active (the suite
was run without ``pytest-cov``), the gate skips rather than passing silently.
"""

from __future__ import annotations

import pytest

from src2sink import limits, prescreen, safe_paths, sanitize

# The security-critical modules and the floor they must hold. Keep this list in
# sync with the security-critical modules the coverage policy calls out.
SECURITY_MODULES = (limits, safe_paths, sanitize, prescreen)
MIN_COVERAGE = 90.0


def _covered_percent(cov, module):
    """Return the line-coverage percentage for ``module`` from live cov data.

    Uses ``Coverage.analysis2`` which flushes the active collector, so the
    result reflects every test that has run so far in this session.
    """
    _fname, statements, _excluded, missing, _fmt = cov.analysis2(module.__file__)
    total = len(statements)
    if total == 0:
        return 100.0
    return (total - len(missing)) / total * 100.0


def test_security_modules_meet_90pct(cov):
    """Fail the run if any security module drops below the 90% floor."""
    if cov is None:
        pytest.skip("coverage not active — run under pytest-cov to enforce this gate")

    shortfalls = {
        module.__name__: pct
        for module in SECURITY_MODULES
        if (pct := _covered_percent(cov, module)) < MIN_COVERAGE
    }

    assert not shortfalls, (
        f"security modules below the {MIN_COVERAGE:.0f}% coverage gate: "
        + ", ".join(
            f"{name} at {pct:.1f}%" for name, pct in sorted(shortfalls.items())
        )
    )
