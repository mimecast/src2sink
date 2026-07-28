"""Scan Java library sources for public-API taint table rows."""

from __future__ import annotations

import re
from pathlib import Path

JAVA_PUBLIC_METHOD_RX = re.compile(
    r"^\s*public\s+(?:static\s+)?[\w.<>,\s\[\]]+\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)

SINK_HINTS: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"String\s+sql", re.I), "sink", "sql", "String SQL parameter"),
    (re.compile(r"String\s+query", re.I), "sink", "sql", "String query parameter"),
    (re.compile(r"String\s+exp(?:ression)?", re.I), "propagator", "expression", "Expression text"),
    (re.compile(r"execute(?:Query|Update)?\s*\(", re.I), "sink", "sql", "JDBC execution"),
    (re.compile(r"\.send\s*\(", re.I), "sink", "http", "HTTP client send"),
    (re.compile(r"RestTemplate|WebClient|HttpClient", re.I), "propagator", "http", "HTTP client API"),
    (re.compile(r"kafkaTemplate\.send", re.I), "sink", "queue", "Kafka publish"),
]

PROPAGATOR_DEFAULT = ("propagator", "opaque", "Treat arguments as flowing to library internals")

_SKIP_PARTS = frozenset({"test", "tests", "generated"})


def _classify_method(method: str, params: str, snippet: str) -> tuple[str, str, str]:
    """Classify a Java method as (role, sink type, notes) using sink hint patterns."""
    role, sink_type, notes = PROPAGATOR_DEFAULT
    for pat, r, st, note in SINK_HINTS:
        if pat.search(params) or pat.search(snippet):
            return r, st, note
    return role, sink_type, notes


def scan_java_public_api(repo_root: Path, *, max_rows: int = 40) -> list[dict[str, str]]:
    """Scan Java sources under repo_root for public methods as taint table rows.

    Args:
        repo_root: Root of the library source tree to scan.
        max_rows: Maximum number of rows to return.

    Returns:
        A list of taint table row dicts, capped at ``max_rows``.
    """
    rows: list[dict[str, str]] = []
    for path in repo_root.rglob("*.java"):
        if any(p in path.parts for p in _SKIP_PARTS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(repo_root)
        for m in JAVA_PUBLIC_METHOD_RX.finditer(text):
            method = m.group(1)
            if method in {"equals", "hashCode", "toString"}:
                continue
            params = m.group(2)
            snippet = text[m.start() : m.start() + 400]
            role, sink_type, notes = _classify_method(method, params, snippet)
            rows.append({
                "Method signature": f"{method}({params[:60]})",
                "Role": role,
                "Sink type": sink_type,
                "Sanitiser?": "partial" if "validate" in method.lower() else "unknown",
                "Notes": f"{notes} @ {rel}",
            })
    return rows[:max_rows]


def render_taint_table(rows: list[dict[str, str]]) -> str:
    """Render taint table rows as a markdown table string."""
    if not rows:
        return (
            "| Method signature | Role | Sink type | Sanitiser? | Notes |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| _none detected_ | — | — | — | — |\n"
        )
    headers = ["Method signature", "Role", "Sink type", "Sanitiser?", "Notes"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(str(row.get(h, "")).replace("|", "\\|") for h in headers)
            + " |",
        )
    return "\n".join(lines) + "\n"
