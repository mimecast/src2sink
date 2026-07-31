"""Tests for configurable internal dependency namespace patterns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src2sink.internal_groups import (
    DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS,
    configure_internal_group_patterns,
    resolve_internal_group_pattern_strings,
)
from src2sink.repo_utils import is_internal_coordinate


def test_default_patterns_classify_example_coordinates() -> None:
    configure_internal_group_patterns(DEFAULT_INTERNAL_GROUP_PATTERN_STRINGS)
    assert is_internal_coordinate("com.example.acme", "sql-runner-api")
    assert is_internal_coordinate("@example/ui-lib", None)
    assert not is_internal_coordinate("com.google.guava", "guava")


def test_config_file_replaces_defaults(tmp_path: Path) -> None:
    config = tmp_path / "internal-groups.example.json"
    config.write_text(
        json.dumps({"patterns": [r"^com\.acme(\..+)?$"]}),
        encoding="utf-8",
    )
    patterns = resolve_internal_group_pattern_strings(
        config_file=config,
        extra_patterns=[],
    )
    configure_internal_group_patterns(patterns)
    assert is_internal_coordinate("com.acme.shared", "shared-jdbc")
    assert not is_internal_coordinate("com.example.datawarehouse", "sql-runner-api")


def test_auto_discover_under_metabase_root(tmp_path: Path) -> None:
    (tmp_path / "internal-groups.txt").write_text(
        "^com\\.acme(\\..+)?$\n",
        encoding="utf-8",
    )
    patterns = resolve_internal_group_pattern_strings(metabase_root=tmp_path)
    assert patterns == [r"^com\.acme(\..+)?$"]


def test_invalid_regex_raises() -> None:
    with pytest.raises(ValueError, match="Invalid internal-group regex"):
        configure_internal_group_patterns(["("])
