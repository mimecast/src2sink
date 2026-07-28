"""Enrich http-out nodes with URL, host, and path extracted from surrounding source."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ..graph_common import extract_urls_and_paths

if TYPE_CHECKING:
    from ..known_api_clients import ApiClientBinding

# Client call sites (pattern, language hint, purpose)
HTTP_OUT_CALL_RX: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"RestTemplate\.(getForObject|postForObject|exchange|get|post|put|delete)"), "java", "client-call"),
    (re.compile(r"WebClient\.(create|builder)"), "java", "client-call"),
    (re.compile(r"WebClient\.[a-zA-Z]+\(\)\.(get|post|put|delete|patch)"), "java", "client-call"),
    (re.compile(r"OkHttpClient|\.newCall\s*\("), "java", "client-call"),
    (re.compile(r"HttpClient\.(newHttpClient|send|sendAsync)"), "java", "client-call"),
    (re.compile(r"HttpRequest\.newBuilder"), "java", "client-call"),
    (re.compile(r"URI\.create\s*\("), "java", "client-call"),
    (re.compile(r"requests\.(get|post|put|delete|patch)\s*\("), "python", "client-call"),
    (re.compile(r"httpx\.(get|post|put|delete|patch|AsyncClient)"), "python", "client-call"),
    (re.compile(r"aiohttp\.ClientSession"), "python", "client-call"),
    (re.compile(r"urllib\.request\.urlopen\s*\("), "python", "client-call"),
    (re.compile(r"http\.client\.HTTPConnection|urllib3"), "python", "client-call"),
    (re.compile(r"\bfetch\s*\("), "javascript", "client-call"),
    (re.compile(r"axios\.(get|post|put|delete|patch)"), "javascript", "client-call"),
    (re.compile(r"http\.NewRequest\s*\("), "go", "client-call"),
]

_BINDING_CLASS_RX: list[tuple[re.Pattern[str], str, str]] = []


def configure_http_out_client_patterns(bindings: Iterable[ApiClientBinding]) -> None:
    """Build per-binding class-pattern regex entries from loaded ApiClientBindings."""
    global _BINDING_CLASS_RX
    _BINDING_CLASS_RX = []
    for b in bindings:
        if b.class_patterns:
            pat = "|".join(re.escape(p) for p in b.class_patterns)
            _BINDING_CLASS_RX.append((re.compile(pat), "java", "api-client-consumer"))

# URL / path on same line or builder chain
INLINE_URL_RX = re.compile(
    r'(?:uri|url|baseUrl|basePath|endpoint|path)\s*[\(,=]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
HTTP_METHOD_RX = re.compile(
    r"\.(GET|POST|PUT|DELETE|PATCH)\s*\(|"
    r"\.(get|post|put|delete|patch)\s*\(|"
    r"@(Get|Post|Put|Delete|Patch)(?:Mapping)?",
    re.IGNORECASE,
)


def _line_window(source: str, line_num: int, radius: int = 2) -> str:
    """Return the source lines within ``radius`` of 1-based ``line_num``, newline-joined."""
    lines = source.splitlines()
    if not lines:
        return ""
    idx = max(0, min(line_num - 1, len(lines) - 1))
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    return "\n".join(lines[start:end])


def enrich_http_out_detail(
    source: str,
    line_num: int,
    raw_call: str,
    purpose: str,
) -> dict[str, Any]:
    """Build an http-out detail dict (host, path, method, url) from the call context.

    Inspects untrusted source text around the call; text is only parsed, never executed.
    """
    detail: dict[str, Any] = {
        "purpose": purpose,
        "raw": raw_call[:200],
    }
    window = _line_window(source, line_num, radius=3)
    blob = raw_call + "\n" + window

    hosts, paths = extract_urls_and_paths(blob)
    for m in INLINE_URL_RX.finditer(blob):
        val = m.group(1)
        if val.startswith("/"):
            paths.append(val)
        elif val.startswith("http"):
            hosts.append(val.split("://", 1)[-1].split("/")[0])

    if hosts:
        detail["host"] = hosts[0]
    if paths:
        # Prefer longest path (often includes version prefix)
        detail["path"] = max(paths, key=len)
        detail["paths"] = list(dict.fromkeys(paths))[:5]
    for m in HTTP_METHOD_RX.finditer(blob):
        detail["http_method"] = next(g for g in m.groups() if g is not None).upper()
        break

    full_urls = re.findall(r"https?://[^\s\"'<>]+", blob)
    if full_urls:
        detail["url"] = full_urls[0][:240]

    return detail
