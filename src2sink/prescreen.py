"""Pre-screen scanned files for suspicious/pathological content before parsing.

Some scanned repositories may contain malicious or pathological test files (the
fleet includes malware-analysis tooling). Since this tool never *executes*
scanned content, the real risks are denial of service — huge, minified, or
obfuscated files that stress the tree-sitter parser and the regex engines — and
content poisoning. This module applies cheap, low-false-positive checks BEFORE a
file reaches the extractors, so a suspicious file is skipped and recorded rather
than parsed. See docs/threat-model.md finding SEC-NEW-4.

Structural checks (binary content, over-long "minified" lines) are always on and
have negligible false-positive risk. Content-substring *indicators* are opt-in
and operator-supplied — deliberately not built in, because legitimate security
tooling in the fleet would trip generic malware signatures.
"""

from __future__ import annotations

from pathlib import Path

# A single line longer than this is almost always minified/packed/obfuscated
# content — a parser/ReDoS hazard with no analytical value. Default; overridable
# per worker via configure_max_line_bytes (CLI --max-line-bytes; 0 disables).
MAX_LINE_BYTES = 50_000
_MAX_LINE_BYTES = MAX_LINE_BYTES
_BINARY_SNIFF_CHARS = 8192
_REPLACEMENT_CHAR = "�"
_MAX_REPLACEMENT_RATIO = 0.10

# Operator-supplied content indicators (lower-cased), configured per worker.
_INDICATORS: tuple[str, ...] = ()


def configure_indicators(indicators: tuple[str, ...] | list[str]) -> None:
    """Set the process-wide content indicators (matched case-insensitively)."""
    global _INDICATORS  # noqa: PLW0603
    _INDICATORS = tuple(i.lower() for i in indicators if i)


def configure_max_line_bytes(max_line_bytes: int) -> None:
    """Set the process-wide oversized-line threshold in bytes (0 disables)."""
    global _MAX_LINE_BYTES  # noqa: PLW0603
    _MAX_LINE_BYTES = max_line_bytes


def load_indicators(path: Path) -> tuple[str, ...]:
    """Read indicator substrings from a text file (one per line; ``#`` comments).

    Returns an empty tuple on a missing/unreadable file — never raises.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    out: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return tuple(out)


def screen(path: Path, text: str, *, indicators: tuple[str, ...] | None = None) -> str | None:
    """Return a short skip reason if ``text`` looks unsafe/pathological, else None.

    ``indicators`` overrides the process-wide list (mainly for tests); when
    omitted the configured :data:`_INDICATORS` are used.
    """
    inds = _INDICATORS if indicators is None else tuple(i.lower() for i in indicators if i)

    head = text[:_BINARY_SNIFF_CHARS]
    if "\x00" in head:
        return "binary content (null byte)"
    if head:
        ratio = head.count(_REPLACEMENT_CHAR) / len(head)
        if ratio > _MAX_REPLACEMENT_RATIO:
            return "binary or non-UTF-8 content"

    if _MAX_LINE_BYTES:
        for line in text.splitlines():
            if len(line) > _MAX_LINE_BYTES:
                return (
                    f"oversized line (> {_MAX_LINE_BYTES} bytes; "
                    "minified/obfuscated; raise --max-line-bytes)"
                )

    if inds:
        lowered = text.lower()
        for ind in inds:
            if ind in lowered:
                return f"matched configured indicator: {ind[:40]}"

    return None
