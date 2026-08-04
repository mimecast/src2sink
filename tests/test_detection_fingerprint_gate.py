"""The gate that keeps DETECTION_VERSION honest (OI-16).

Recording the detector identity only helps if the identity actually changes when
detection changes. Relying on a human to remember the bump reproduces the failure
the version exists to prevent — silent, and only visible much later as findings
that never updated.

So the detection inputs are fingerprinted by content and frozen, in the same
shape as the complexity ratchet: change an extractor without bumping the version
and the build fails. Deliberately *not* git-based — CI checks out at depth 1, and
a gate that silently degrades when it cannot find a base ref is no gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from detection_version_check import (  # noqa: E402
    DETECTION_INPUTS,
    check_fingerprint,
    compute_fingerprint,
)

_ROOT = Path(__file__).resolve().parent.parent


def _frozen(version: int, files: dict[str, str]) -> dict[str, object]:
    """Build a frozen-fingerprint record for the gate to check against."""
    return {"detection_version": version, "files": files}


def test_the_repository_is_currently_consistent():
    """The committed fingerprint must match the committed extractors."""
    ok, message = check_fingerprint(_ROOT)
    assert ok, message


def test_every_declared_input_exists():
    """A path that has been renamed away would silently stop being fingerprinted."""
    missing = [p for p in DETECTION_INPUTS if not (_ROOT / p).exists()]
    assert not missing, f"declared detection inputs no longer present: {missing}"


def test_an_extractor_change_without_a_bump_fails(tmp_path):
    """The whole point: silent detector drift becomes a red build."""
    current = compute_fingerprint(_ROOT)
    tampered = dict(current)
    tampered[next(iter(tampered))] = "0" * 64

    ok, message = check_fingerprint(
        _ROOT, frozen=_frozen(_current_version(), tampered)
    )
    assert not ok
    assert "DETECTION_VERSION" in message


def test_an_extractor_change_with_a_bump_passes(tmp_path):
    """A bump is the author saying "detection changed" — that is the escape hatch."""
    current = compute_fingerprint(_ROOT)
    tampered = dict(current)
    tampered[next(iter(tampered))] = "0" * 64

    ok, _message = check_fingerprint(
        _ROOT, frozen=_frozen(_current_version() - 1, tampered)
    )
    assert ok


def test_a_bump_with_no_extractor_change_is_allowed():
    """Bumping deliberately — to force a fleet rescan — must not be blocked."""
    ok, _message = check_fingerprint(
        _ROOT, frozen=_frozen(_current_version() - 1, compute_fingerprint(_ROOT))
    )
    assert ok


def test_a_new_detection_input_is_noticed():
    """Adding an extractor module must not slip past an already-frozen fingerprint."""
    current = compute_fingerprint(_ROOT)
    without_one = {k: v for k, v in list(current.items())[1:]}

    ok, message = check_fingerprint(
        _ROOT, frozen=_frozen(_current_version(), without_one)
    )
    assert not ok
    assert "DETECTION_VERSION" in message


def _current_version() -> int:
    from src2sink.schema import DETECTION_VERSION

    return DETECTION_VERSION


@pytest.mark.parametrize("path", sorted(DETECTION_INPUTS))
def test_declared_inputs_are_inside_the_package(path):
    """A fingerprint over files outside the package would fire on unrelated edits."""
    assert path.startswith("src2sink/"), path
