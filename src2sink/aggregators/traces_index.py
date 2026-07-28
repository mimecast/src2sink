"""Generate graphs/traces/INDEX.md from batch trace outputs."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE

TRACE_HEADER_RX = re.compile(r"^# Flow trace:\s*(\S+)\s*$", re.MULTILINE)
PATH_FILTER_RX = re.compile(r"^_\s*Path filter:\s*`([^`]+)`", re.MULTILINE)


def _parse_trace_file(md_path: Path) -> tuple[str, str]:
    """Extract (repo, path filter) from a trace report's header."""
    try:
        head = md_path.read_text(encoding="utf-8")[:500]
    except OSError:
        return "?", "?"
    m = TRACE_HEADER_RX.search(head)
    repo = m.group(1) if m else "?"
    pf = PATH_FILTER_RX.search(head)
    path = pf.group(1) if pf else "—"
    return repo, path


def _load_catalogue(cat_path: Path) -> set[tuple[str, str]]:
    """Load (repo, endpoint_path) pairs from a raw-code-payload endpoints jsonl."""
    catalogue: set[tuple[str, str]] = set()
    if not cat_path.is_file():
        return catalogue
    with cat_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            repo = rec.get("repo", "")
            path = (rec.get("detail") or {}).get("endpoint_path", "")
            if repo and path:
                catalogue.add((repo, path))
    return catalogue


def _trace_index_rows(
    traces_dir: Path, catalogue: set[tuple[str, str]]
) -> tuple[list[list[str]], set[tuple[str, str]]]:
    """Build index table rows and the set of traced (repo, path) pairs."""
    rows: list[list[str]] = []
    traced: set[tuple[str, str]] = set()
    for md_path in sorted(traces_dir.glob("*.md")):
        if md_path.name == "INDEX.md":
            continue
        repo, path = _parse_trace_file(md_path)
        if path != "—":
            traced.add((repo, path))
        in_cat = "yes" if (repo, path) in catalogue else "—"
        rows.append([repo, path, in_cat, f"[{md_path.name}](./{md_path.name})"])
    return rows, traced


def write_traces_index(metabase_root: Path) -> int:
    """Write graphs/traces/INDEX.md from trace reports and return the report count."""
    traces_dir = metabase_root / "graphs" / "traces"
    if not traces_dir.is_dir():
        return 0

    catalogue = _load_catalogue(metabase_root / "taint" / "raw-code-payload-endpoints.jsonl")
    rows, traced = _trace_index_rows(traces_dir, catalogue)

    md: list[str] = [
        "# Raw-code-payload trace reports\n",
        UNTRUSTED_CONTENT_NOTICE,
        f"_{len(rows)} reports from `trace_batch.py` / `trace.py`._\n",
        "\n## Reports\n",
        md_table(
            ["Repo", "Endpoint", "In catalogue", "Report"],
            rows[:500],
        ),
    ]
    if len(rows) > 500:
        md.append(f"\n_{len(rows) - 500} more trace files in this directory._\n")

    if catalogue:
        missing = sorted(catalogue - traced)
        md.append(
            f"\n**Catalogue coverage:** {len(traced & catalogue)} / "
            f"{len(catalogue)} endpoints have traces.\n",
        )
        if missing:
            md.append("\n### Missing traces (sample)\n")
            md.append(
                md_table(
                    ["Repo", "Endpoint"],
                    [[r, p] for r, p in missing[:40]],
                ),
            )
            if len(missing) > 40:
                md.append(f"\n_{len(missing) - 40} more — run `trace_batch.py --skip-existing`._\n")

    (traces_dir / "INDEX.md").write_text("\n".join(md), encoding="utf-8")
    return len(rows)
