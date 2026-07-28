"""Tests for curate_internal_libraries (auto-filling taint tables)."""

from __future__ import annotations

import sys

from src2sink.curate_internal_libraries import (
    _extract_coordinate,
    _table_body,
    curate_library_file,
    main,
)

_LIB_MD = """# Library: com.acme:widget

| Field | Value |
| --- | --- |
| Coordinate | com.acme:widget |

## Public-API taint table (hand-curated)

| Method signature | Role | Source | Sink | Notes |
| --- | --- | --- | --- | --- |
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
"""


def _make_library_source(tmp_path):
    src = tmp_path / "repos" / "com.acme" / "widget" / "src" / "main" / "java"
    src.mkdir(parents=True)
    (src / "Widget.java").write_text(
        "public class Widget {\n  public String run(String sql) { return sql; }\n}\n",
        encoding="utf-8",
    )


def test_extract_coordinate():
    assert _extract_coordinate("| Coordinate | com.acme:widget |") == "com.acme:widget"
    assert _extract_coordinate("no table here") is None


def test_table_body_strips_header():
    body = _table_body("| H1 | H2 |\n| --- | --- |\n| a | b |\n")
    assert body.strip() == "| a | b |"


def test_curate_library_file_fills_tbd(tmp_path):
    _make_library_source(tmp_path)
    lib = tmp_path / "widget.md"
    lib.write_text(_LIB_MD, encoding="utf-8")
    source_map = {"com.acme:widget": {"status": "cloned", "clone_path": "com.acme/widget"}}
    changed = curate_library_file(lib, tmp_path / "repos", "com.acme:widget", source_map=source_map)
    assert changed is True
    assert "_TBD_" not in lib.read_text(encoding="utf-8")


def test_curate_library_file_no_tbd_returns_false(tmp_path):
    lib = tmp_path / "done.md"
    lib.write_text("# already curated, no placeholders\n", encoding="utf-8")
    assert curate_library_file(lib, tmp_path / "repos", "com.acme:widget") is False


def test_curate_main(tmp_path, monkeypatch):
    _make_library_source(tmp_path)
    mb = tmp_path / "metabase"
    (mb / "internal-libraries").mkdir(parents=True)
    (mb / "internal-libraries" / "widget.md").write_text(_LIB_MD, encoding="utf-8")
    (mb / "library-source-map.json").write_text(
        '{"mappings": [{"coordinate": "com.acme:widget", "status": "cloned", '
        '"clone_path": "com.acme/widget"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv",
        ["src2sink-curate", "--repos-root", str(tmp_path / "repos"),
         "--metabase-root", str(mb)],
    )
    assert main() == 0
