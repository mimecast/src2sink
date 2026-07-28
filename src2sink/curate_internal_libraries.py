#!/usr/bin/env python3
"""Auto-fill internal-library taint tables from cloned library source (stdlib)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from .aggregators.library_source_map import load_library_source_map
from .library_taint_java import render_taint_table, scan_java_public_api
from .repo_utils import _locate_library_source

TBD_ROW = "| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |\n"


def _extract_coordinate(lib_text: str) -> str | None:
    """Return the coordinate value from a library markdown table, or None."""
    m = re.search(r"\|\s*Coordinate\s*\|\s*([^|]+)\s*\|", lib_text)
    return m.group(1).strip() if m else None


def _resolve_library_root(
    repos_root: Path, coord: str, source_map: dict[str, Any] | None = None
) -> Path | None:
    """Resolve the on-disk source root for a library coordinate, or None.

    Args:
        repos_root: Root directory holding cloned repos.
        coord: Library coordinate to locate.
        source_map: Optional precomputed coordinate-to-source mapping.

    Returns:
        The library's source directory if it exists, else None.
    """
    source_rel = _locate_library_source(repos_root, coord, source_map=source_map)
    if not source_rel:
        return None
    rel = source_rel.removeprefix("repos/").lstrip("/")
    repo_root = repos_root / rel
    return repo_root if repo_root.is_dir() else None


def _replace_tbd_table(lib_text: str, table: str) -> str:
    """Substitute the placeholder taint table in lib_text with the given table."""
    if TBD_ROW in lib_text:
        return lib_text.replace(TBD_ROW, _table_body(table), count=1)
    return re.sub(
        r"(## Public-API taint table \(hand-curated\)\n\n"
        r"(?:Each row.*?\n\n)?)"
        r"\| Method signature \|.*?\| _TBD_ \| _TBD_ \| _TBD_ \| _TBD_ \| _TBD_ \|\n",
        r"\1" + table + "\n",
        lib_text,
        count=1,
        flags=re.DOTALL,
    )


def _table_body(table: str) -> str:
    """Return the data rows of a markdown table, dropping the header and separator."""
    body_lines = table.strip().splitlines()
    if len(body_lines) > 2:
        return "\n".join(body_lines[2:]) + "\n"
    return table


def curate_library_file(
    lib_path: Path,
    repos_root: Path,
    coord: str,
    source_map: dict[str, Any] | None = None,
) -> bool:
    """Fill a library file's placeholder taint table from scanned source.

    Args:
        lib_path: Path to the internal-library markdown file.
        repos_root: Root directory holding cloned repos.
        coord: Library coordinate used to locate the source.
        source_map: Optional precomputed coordinate-to-source mapping.

    Returns:
        True if the file was updated, False otherwise.
    """
    text = lib_path.read_text(encoding="utf-8")
    if "_TBD_" not in text:
        return False
    repo_root = _resolve_library_root(repos_root, coord, source_map=source_map)
    if not repo_root:
        return False
    rows = scan_java_public_api(repo_root)
    if not rows:
        return False
    table = render_taint_table(rows)
    new_text = _replace_tbd_table(text, table)
    if new_text == text:
        return False
    lib_path.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    """CLI entry point: curate placeholder taint tables across internal libraries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-root", required=True)
    parser.add_argument("--metabase-root", required=True)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    repos_root = Path(args.repos_root).resolve()
    metabase_root = Path(args.metabase_root).resolve()
    lib_dir = metabase_root / "internal-libraries"
    lib_dir.mkdir(parents=True, exist_ok=True)
    source_map = load_library_source_map(metabase_root / "library-source-map.json")
    updated = 0
    for path in sorted(lib_dir.glob("*.md")):
        if path.name == "INDEX.md":
            continue
        text = path.read_text(encoding="utf-8")
        if "_TBD_" not in text:
            continue
        coord = _extract_coordinate(text)
        if not coord:
            continue
        if curate_library_file(path, repos_root, coord, source_map=source_map):
            updated += 1
            print(f"  curated {path.name}")
        if updated >= args.limit:
            break
    print(f"Updated {updated} library files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
