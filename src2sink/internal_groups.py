"""Configurable regex patterns for organization-internal dependency coordinates."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Iterable

# Open-source defaults — generic placeholders aligned with SCHEMA.md examples.
DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS: tuple[str, ...] = (
    r"^com\.example(\..+)?$",
    r"^@example(/.+)?$",
    r"^internal[-_].+$",
)

INTERNAL_GROUP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p) for p in DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS
]


def compile_internal_group_patterns(pattern_strings: Iterable[str]) -> list[re.Pattern[str]]:
    """Compile non-blank pattern strings into regexes; require at least one."""
    compiled: list[re.Pattern[str]] = []
    for raw in pattern_strings:
        pattern = raw.strip()
        if not pattern:
            continue
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(f"Invalid internal-group regex {pattern!r}: {exc}") from exc
    if not compiled:
        raise ValueError("At least one internal-group pattern is required")
    return compiled


def configure_internal_group_patterns(pattern_strings: Iterable[str]) -> None:
    """Replace the module-level compiled patterns used by is_internal_coordinate()."""
    global INTERNAL_GROUP_PATTERNS
    INTERNAL_GROUP_PATTERNS = compile_internal_group_patterns(pattern_strings)


def _worker_init(pattern_strings: list[str]) -> None:
    """Multiprocessing pool initializer: configure this worker's internal-group patterns."""
    configure_internal_group_patterns(pattern_strings)


def _read_pattern_file(path: Path) -> list[str]:
    """Read regex pattern strings from a JSON or line-oriented config file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            patterns = data.get("patterns", [])
        elif isinstance(data, list):
            patterns = data
        else:
            raise ValueError(
                f"{path}: JSON must be an object with a 'patterns' array or a bare array"
            )
        if not isinstance(patterns, list):
            raise ValueError(f"{path}: 'patterns' must be an array of regex strings")
        return [str(p).strip() for p in patterns if str(p).strip()]
    patterns: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def discover_internal_groups_file(metabase_root: Path | None) -> Path | None:
    """Return the internal-groups config file under metabase_root, if one exists."""
    if metabase_root is None:
        return None
    for name in ("internal-groups.json", "internal-groups.txt"):
        candidate = metabase_root / name
        if candidate.is_file():
            return candidate
    return None


def resolve_internal_group_pattern_strings(
    *,
    metabase_root: Path | None = None,
    config_file: str | Path | None = None,
    extra_patterns: Iterable[str] | None = None,
) -> list[str]:
    """Resolve pattern strings from defaults, file, and CLI extras."""
    file_arg = config_file or os.environ.get("METABASE_INTERNAL_GROUPS_FILE")
    path = Path(file_arg).expanduser() if file_arg else discover_internal_groups_file(metabase_root)

    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"Internal-groups config not found: {path}")
        patterns = _read_pattern_file(path)
    else:
        patterns = list(DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS)

    if extra_patterns:
        patterns.extend(p.strip() for p in extra_patterns if p and p.strip())

    if not patterns:
        raise ValueError("No internal-group patterns resolved")
    return patterns


def add_internal_groups_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the --internal-groups-file and --internal-group-pattern CLI flags."""
    parser.add_argument(
        "--internal-groups-file",
        default=None,
        help="JSON or line-oriented file of regex patterns for "
        "organization-internal Maven/npm coordinates. Replaces built-in "
        "defaults when set. Also read from $METABASE_INTERNAL_GROUPS_FILE "
        "or <metabase-root>/internal-groups.{json,txt} if present.",
    )
    parser.add_argument(
        "--internal-group-pattern",
        action="append",
        default=[],
        dest="internal_group_patterns",
        metavar="REGEX",
        help="Additional internal coordinate regex (repeatable). Appended "
        "after defaults or --internal-groups-file patterns.",
    )


def apply_internal_groups_from_args(args: argparse.Namespace) -> list[str]:
    """Resolve internal-group patterns from parsed CLI args and configure them globally."""
    pattern_strings = resolve_internal_group_pattern_strings(
        metabase_root=Path(args.metabase_root).resolve(),
        config_file=args.internal_groups_file,
        extra_patterns=args.internal_group_patterns,
    )
    configure_internal_group_patterns(pattern_strings)
    return pattern_strings
