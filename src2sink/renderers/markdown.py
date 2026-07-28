"""Markdown rendering for metabase v2."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from ..sanitize import UNTRUSTED_CONTENT_NOTICE, for_table_cell

if TYPE_CHECKING:
    from pathlib import Path

    from ..schema import RepoSummaryV2

AUTO_START = "<!-- AUTO-GENERATED:START key={key} -->"
AUTO_END = "<!-- AUTO-GENERATED:END key={key} -->"


def auto_block(key: str, body: str) -> str:
    """Wrap body in AUTO-GENERATED start/end markers for the given key."""
    return AUTO_START.format(key=key) + "\n" + body.rstrip() + "\n" + AUTO_END.format(key=key) + "\n"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a markdown table with each cell scrubbed of untrusted content."""
    if not rows:
        return "_(none detected)_\n"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        # Every cell is scrubbed: untrusted extracted content cannot break the
        # table structure or open a code fence (see sanitize.for_table_cell).
        cells = [for_table_cell(c) for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_repo_md_v2(summary: RepoSummaryV2) -> str:
    """Render the full v2 repo markdown page from a RepoSummaryV2."""
    out: list[str] = []
    out.append(f"# {summary.group}/{summary.name}\n")
    out.append(
        "> **Metabase v2** (`schema_version="
        f"{summary.schema_version}`). Flow nodes use "
        "`kind`: source | propagator | sink | store.\n"
    )
    out.append(UNTRUSTED_CONTENT_NOTICE)

    out.append("## Identity\n")
    out.append(
        auto_block(
            "identity",
            md_table(
                ["Field", "Value"],
                [
                    ["Group", summary.group],
                    ["Name", summary.name],
                    ["Path", summary.path],
                    ["Git SHA", summary.git_sha or "_unknown_"],
                    ["Analysed at", summary.analysed_at],
                    ["Primary language", summary.primary_language],
                    ["Flow nodes", str(len(summary.nodes))],
                    ["Flow edges", str(len(summary.edges))],
                ],
            ),
        )
    )

    by_family = Counter(n.family for n in summary.nodes)
    out.append("## Flow summary by family\n")
    fam_rows = [[f, str(c)] for f, c in by_family.most_common()]
    out.append(auto_block("flow_families", md_table(["Family", "Count"], fam_rows)))

    out.append("## Sample nodes (first 80)\n")
    rows = []
    for n in summary.nodes[:80]:
        rows.append([
            n.kind,
            n.family,
            f"{n.file}:{n.line}",
            n.pii_classification or "",
            n.data_class or "",
            str(n.detail.get("symbol") or n.detail.get("path") or "")[:60],
        ])
    out.append(
        auto_block(
            "nodes_sample",
            md_table(
                ["Kind", "Family", "Location", "PII class", "Data class", "Detail"],
                rows,
            ),
        )
    )
    if len(summary.nodes) > 80:
        out.append(f"_… {len(summary.nodes) - 80} more nodes in JSON._\n")

    raw_eps = [n for n in summary.nodes if n.family == "raw-code-payload"]
    if raw_eps:
        out.append("## Raw code payload endpoints\n")
        ep_rows = [
            [
                n.detail.get("endpoint_path", ""),
                f"{n.file}:{n.line}",
                n.detail.get("sink_symbol", ""),
                n.confidence,
            ]
            for n in raw_eps[:30]
        ]
        out.append(
            auto_block(
                "raw_code_payload",
                md_table(["Path", "Field location", "Sink", "Confidence"], ep_rows),
            )
        )

    return "\n".join(out)


def merge_with_manual(md_path: Path, generated: str) -> str:
    """Merge generated AUTO-GENERATED blocks into an existing manual markdown file.

    Replaces each keyed block in the existing file, appending any block whose
    key is not already present. Returns the generated text if no file exists.
    """
    if not md_path.is_file():
        return generated
    existing = md_path.read_text(encoding="utf-8")
    blocks: dict[str, str] = {}
    for m in re.finditer(
        r"<!-- AUTO-GENERATED:START key=(\w+) -->(.*?)<!-- AUTO-GENERATED:END key=\1 -->",
        generated,
        re.DOTALL,
    ):
        blocks[m.group(1)] = m.group(0)
    if not blocks:
        return generated
    result = existing
    for key, block in blocks.items():
        pat = (
            rf"<!-- AUTO-GENERATED:START key={key} -->.*?<!-- AUTO-GENERATED:END key={key} -->"
        )
        if re.search(pat, result, re.DOTALL):
            result = re.sub(pat, block, result, count=1, flags=re.DOTALL)
        else:
            result = result.rstrip() + "\n\n" + block
    return result
