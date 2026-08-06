#!/usr/bin/env python3
"""Fail the build when detection changes without a ``DETECTION_VERSION`` bump.

``DETECTION_VERSION`` is part of the incremental scan's cache key: bumping it is
what makes a detection fix reach repositories that have not themselves changed.
Recording it only helps if it actually changes when detection does, and relying
on an author to remember reproduces the failure the version exists to prevent —
silent, and visible only much later as findings that never updated (OI-16).

So the detection inputs are fingerprinted by content and frozen, in the same
shape as ``complexity_check.py``: change one without bumping the version and the
build fails; bump it and re-freeze with ``--update``.

Deliberately **not** git-based. CI checks out at depth 1, so a diff against a
base ref would either need a full fetch or degrade to passing when it cannot find
one — and a gate that silently passes is not a gate.

Deliberately **not** a hash used as the version itself, either: that would
invalidate the whole fleet on a comment or a rename. The hash decides *whether a
bump was required*; the human decides what the version is.

Usage:
  uv run python scripts/detection_version_check.py
  uv run python scripts/detection_version_check.py --update
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import TypedDict


class FrozenFingerprint(TypedDict):
    """The committed record: which detector version, and what it was built from."""

    detection_version: int | None
    files: dict[str, str]
    derivation_version: int | None
    derivation_files: dict[str, str]


_ROOT = Path(__file__).resolve().parent.parent
FINGERPRINT_FILE = Path("scripts/detection-fingerprint.json")

# Every file whose content can change what ends up in a **repo record**.
# Directories are expanded to their .py files, so a new module inside one is
# picked up automatically — a new *directory* is not, which is why the set is
# explicit.
#
# This originally listed only `extractors/` plus the vocabularies, on the
# assumption that "detection" meant extraction. It does not: a record is
# produced by everything on the path from `build_metabase_v2` down, and a change
# to dependency parsing in `repo_utils` altered records while the gate stayed
# green. `tests/test_detection_input_coverage.py` now derives this set from the
# import closure rather than trusting the list, because a hand-maintained list
# drifts the moment an import changes — which is how that gap opened.
#
# Aggregators, renderers and models are deliberately absent: they run *after*
# records are written and their artefacts are rebuilt from records on every run,
# so they are never served stale. Fingerprinting them would force fleet-wide
# rescans to fix output that was already being regenerated.
# Derivation is versioned separately, because changing it costs a re-derive over
# existing records rather than a fleet rescan. Fingerprinting it under
# DETECTION_VERSION would force the expensive event for the cheap change, which
# is exactly the coupling docs/plans/observe-then-classify.md removed.
DERIVATION_INPUT_FILES = (
    "src2sink/derive.py",
    # `OI-17` step 3. Call resolution is a derivation for the same reason
    # classification is: every tier is a judgement about what a syntactic read
    # is worth, and those get revised. Watched at the cheaper granularity, so
    # changing a tier rule costs a re-derive over records, not a fleet rescan.
    "src2sink/resolve.py",
    # `OI-17` step 4. The path search decides what counts as a value
    # travelling, which is a judgement over recorded observations — revising
    # it must cost a re-derive, not a fleet rescan.
    "src2sink/paths.py",
)

DETECTION_INPUT_DIRS = ("src2sink/extractors",)
DETECTION_INPUT_FILES = (
    "src2sink/constants.py",
    "src2sink/vocabulary.py",
    "src2sink/library_taint_java.py",
    "src2sink/prescreen.py",
    "src2sink/known_api_clients.py",
    # Produce or shape record content directly.
    "src2sink/build_metabase_v2.py",  # dependency parsing, analyse_repo_v2, summary_to_dict
    "src2sink/repo_utils.py",         # pom/package.json parsing, frameworks, build systems
    "src2sink/internal_groups.py",    # the internal/external decision on every dependency
    "src2sink/graph_common.py",       # url/path extraction feeding node detail
    "src2sink/sanitize.py",           # redaction applied to detail on the way in
    "src2sink/limits.py",             # per-repo caps decide what gets scanned at all
    "src2sink/safe_paths.py",         # decides which files are eligible to scan
    "src2sink/schema.py",             # record shape and field defaults
    "src2sink/dependencies.py",       # manifest/lockfile parsing -> dependencies_internal
    "src2sink/maven.py",              # pom version resolution -> dependencies_internal
)


def _expand_derivation() -> list[str]:
    """Return every declared derivation input as a repo-relative path."""
    return sorted(DERIVATION_INPUT_FILES)


def _expand(root: Path) -> list[str]:
    """Return every declared detection input as a repo-relative path."""
    paths: list[str] = list(DETECTION_INPUT_FILES)
    for d in DETECTION_INPUT_DIRS:
        paths.extend(
            str(p.relative_to(root)) for p in sorted((root / d).rglob("*.py"))
        )
    return sorted(set(paths))


DETECTION_INPUTS: tuple[str, ...] = tuple(_expand(_ROOT))


def compute_fingerprint(root: Path) -> dict[str, str]:
    """Return {repo-relative path: sha256} over the current detection inputs."""
    out: dict[str, str] = {}
    for rel in _expand(root):
        path = root / rel
        if not path.is_file():
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _load_frozen(root: Path) -> FrozenFingerprint:
    """Read the committed fingerprint, or an empty record if there is none yet."""
    path = root / FINGERPRINT_FILE
    if not path.is_file():
        return {
            "detection_version": None, "files": {},
            "derivation_version": None, "derivation_files": {},
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "detection_version": raw.get("detection_version"),
        "files": dict(raw.get("files") or {}),
        "derivation_version": raw.get("derivation_version"),
        "derivation_files": dict(raw.get("derivation_files") or {}),
    }


def _current_version() -> int:
    from src2sink.schema import DETECTION_VERSION

    return DETECTION_VERSION


def _current_derivation_version() -> int:
    from src2sink.derive import DERIVATION_VERSION

    return DERIVATION_VERSION


def compute_derivation_fingerprint(root: Path) -> dict[str, str]:
    """Return {repo-relative path: sha256} over the current derivation inputs."""
    out: dict[str, str] = {}
    for rel in _expand_derivation():
        path = root / rel
        if path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def check_fingerprint(
    root: Path, *, frozen: FrozenFingerprint | None = None
) -> tuple[bool, str]:
    """Return (ok, message) for the current tree against the frozen fingerprint.

    Passes when nothing detection-relevant changed, or when it changed *and* the
    version was bumped. Fails only for the case the gate exists to catch: changed
    inputs, unchanged version.
    """
    record = _load_frozen(root) if frozen is None else frozen
    recorded_version = record["detection_version"]
    recorded_files = record["files"]
    current_files = compute_fingerprint(root)
    version_now = _current_version()

    deriv_now = compute_derivation_fingerprint(root)
    deriv_recorded = record.get("derivation_files") or {}
    deriv_version_now = _current_derivation_version()
    if deriv_now != deriv_recorded and record.get("derivation_version") == deriv_version_now:
        changed = sorted(
            k for k in set(deriv_now) | set(deriv_recorded)
            if deriv_now.get(k) != deriv_recorded.get(k)
        )
        return False, (
            f"{len(changed)} derivation input(s) changed with DERIVATION_VERSION "
            f"still {deriv_version_now}:\n  " + "\n  ".join(changed) + "\n\n"
            "Findings derived by the previous rules would be kept, because a repo "
            "is only re-derived when the recorded derivation version differs.\n"
            # Not schema.py. DERIVATION_VERSION was moved into derive.py because
            # schema.py is a *detection* input, so bumping it there tripped the
            # detection fingerprint and forced the fleet rescan the split exists
            # to avoid. The message kept pointing at the old home.
            "Bump DERIVATION_VERSION in src2sink/derive.py, then re-freeze with:\n"
            "  uv run python scripts/detection_version_check.py --update\n"
            "This costs a re-derive over existing records, not a fleet rescan — "
            "which is the point of versioning the two separately."
        )

    if current_files == recorded_files:
        return True, (
            f"detection inputs unchanged (DETECTION_VERSION={version_now}, "
            f"DERIVATION_VERSION={deriv_version_now})"
        )

    changed = sorted(
        k for k in set(current_files) | set(recorded_files)
        if current_files.get(k) != recorded_files.get(k)
    )
    if recorded_version != version_now:
        return True, (
            f"detection inputs changed and DETECTION_VERSION was bumped to "
            f"{version_now}; re-freeze with --update"
        )
    return False, (
        f"{len(changed)} detection input(s) changed with DETECTION_VERSION still "
        f"{version_now}:\n  " + "\n  ".join(changed) + "\n\n"
        "Extraction output may differ, and the incremental scan will not rebuild "
        "records produced by the previous detector — so the change would reach "
        "only repositories that happen to commit afterwards (OI-16).\n"
        "Bump DETECTION_VERSION in src2sink/schema.py, then re-freeze with:\n"
        "  uv run python scripts/detection_version_check.py --update\n"
        "\n"
        "Re-freezing WITHOUT a bump is the deliberate, reviewable escape hatch, "
        "and there are two honest reasons for it:\n"
        "  1. the change cannot affect output at all (a comment, a docstring);\n"
        "  2. it affects only *aggregate* output, which is recomputed from "
        "records on every run and so is never served stale — some watched files "
        "straddle both paths, `graph_common` most of all, and the fingerprint is "
        "file-level so it cannot tell them apart.\n"
        "Anything else means records built by the previous detector would be "
        "kept, so bump."
    )


def freeze(root: Path) -> int:
    """Write the current fingerprint and version to the frozen record."""
    files = compute_fingerprint(root)
    payload: dict[str, object] = {
        "_comment": (
            "Content fingerprint of the detection inputs, frozen against "
            "DETECTION_VERSION. Regenerate with "
            "scripts/detection_version_check.py --update."
        ),
        "detection_version": _current_version(),
        "files": files,
        "derivation_version": _current_derivation_version(),
        "derivation_files": compute_derivation_fingerprint(root),
    }
    (root / FINGERPRINT_FILE).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return len(files)


def main() -> int:
    """CLI entry point: check the fingerprint, or re-freeze it with --update."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Re-freeze the fingerprint at the current DETECTION_VERSION",
    )
    args = parser.parse_args()

    if args.update:
        count = freeze(_ROOT)
        print(
            f"Froze {count} detection input(s) at "
            f"DETECTION_VERSION={_current_version()}."
        )
        return 0

    ok, message = check_fingerprint(_ROOT)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
