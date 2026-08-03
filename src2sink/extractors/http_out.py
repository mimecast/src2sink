"""Enrich http-out nodes with URL, host, and path extracted from surrounding source."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..graph_common import extract_urls_and_paths
from .symbols import build_symbol_table

if TYPE_CHECKING:
    from ..known_api_clients import ApiClientBinding

# Client call sites (pattern, language hint, purpose).
#
# Spring's RestTemplate/WebClient entries are anchored on the *method* name, not
# the class name, because real code calls them through a lowercase field or a
# constructor-injected wrapper (`restTemplate.exchange(...)`,
# `this.http.getForObject(...)`). Anchoring on `RestTemplate.` only ever matched
# static-style text and silently dropped every instance call site.
HTTP_OUT_CALL_RX: list[tuple[re.Pattern[str], str, str]] = [
    # Spring RestTemplate — `getForObject` / `exchange` / … are distinctive enough
    # to match on any receiver without pulling in unrelated `x.get(` calls.
    (re.compile(
        r"\b[A-Za-z_]\w*\s*\.\s*"
        r"(getForObject|getForEntity|postForObject|postForEntity|postForLocation"
        r"|patchForObject|putForObject|exchange)\s*\(",
    ), "java", "client-call"),
    # A receiver actually *named* restTemplate/webClient/httpClient/feignClient
    # (either case): here the plain verb methods are safe because the receiver is
    # unambiguous.
    (re.compile(
        r"\b(?:[Rr]est[Tt]emplate|[Ww]eb[Cc]lient|[Hh]ttp[Cc]lient"
        r"|[Ff]eign[Cc]lient|[Oo]k[Hh]ttp[Cc]lient)"
        r"\s*\.\s*(get|post|put|delete|patch|head|options|method|uri)\s*\(",
    ), "java", "client-call"),
    (re.compile(r"[Ww]eb[Cc]lient\.(create|builder)"), "java", "client-call"),
    (re.compile(r"[Ww]eb[Cc]lient\.[a-zA-Z]+\(\)\.(get|post|put|delete|patch)"), "java", "client-call"),
    (re.compile(r"OkHttpClient|\.newCall\s*\("), "java", "client-call"),
    (re.compile(r"HttpClient\.(newHttpClient|send|sendAsync)"), "java", "client-call"),
    (re.compile(r"HttpRequest\.newBuilder"), "java", "client-call"),
    (re.compile(r"URI\.create\s*\("), "java", "client-call"),
    # Spring declarative clients: the interface method *is* the outbound call.
    (re.compile(r"@(?:FeignClient|HttpExchange)\s*\("), "java", "client-call"),
    (re.compile(r"@(?:Get|Post|Put|Delete|Patch)Exchange\s*\(\s*[\"']([^\"']+)[\"']"), "java", "client-call"),
    (re.compile(r"requests\.(get|post|put|delete|patch)\s*\("), "python", "client-call"),
    (re.compile(r"httpx\.(get|post|put|delete|patch|AsyncClient)"), "python", "client-call"),
    (re.compile(r"aiohttp\.ClientSession"), "python", "client-call"),
    (re.compile(r"urllib\.request\.urlopen\s*\("), "python", "client-call"),
    (re.compile(r"http\.client\.HTTPConnection|urllib3"), "python", "client-call"),
    (re.compile(r"\bfetch\s*\("), "javascript", "client-call"),
    (re.compile(r"axios\.(get|post|put|delete|patch)"), "javascript", "client-call"),
    (re.compile(r"http\.NewRequest\s*\("), "go", "client-call"),
]

# File-level evidence that a module really is an HTTP client, used to gate the
# broad receiver patterns below. Without a guard, `self.post(` matches any
# Mapping-like helper; with it, the pattern only fires in files that also
# reference an HTTP stack, so custom wrappers are recovered without the noise.
# The library names below are only half the evidence: a wrapper that hides the
# HTTP client names none of them, which is why a whole class of caller was
# invisible (OI-2). The transport-agnostic tokens close that — a module still
# names statuses, media types and auth headers even when the library is
# somebody else's problem.
_PY_HTTP_FILE_RX = re.compile(
    r"\b(?:requests|httpx|aiohttp|urllib3|urlopen|HTTPConnection)\b"
    r"|base_url|raise_for_status|\bSession\s*\("
    r"|\b(?:status_code|Authorization|Bearer|content_type|application/json)\b",
)
_JAVA_HTTP_FILE_RX = re.compile(
    r"\b(?:RestTemplate|WebClient|OkHttpClient|HttpClient|HttpEntity|HttpHeaders"
    r"|ResponseEntity|HttpMethod|FeignClient|WebTarget"
    r"|MediaType|HttpStatus|Authorization|Bearer)\b",
)

# Call sites that are only trusted when the enclosing *file* also shows HTTP
# client evidence (pattern, language hint, purpose, file guard). These recover
# hand-rolled client wrappers — `self.post(url, ...)` in a Python service client,
# `client.post(SUBMIT_URL, ...)` behind a Java facade — which carry no
# recognisable library name at the call site at all.
HTTP_OUT_CONTEXT_CALL_RX: list[tuple[re.Pattern[str], str, str, re.Pattern[str]]] = [
    (
        re.compile(r"\bself\s*\.\s*(get|post|put|delete|patch)\s*\("),
        "python",
        "client-call",
        _PY_HTTP_FILE_RX,
    ),
    (
        # A `requests.Session()` / `httpx.Client()` held on a field or local.
        re.compile(
            r"\b(?:session|_session|client|_client|http|_http)\s*\.\s*"
            r"(get|post|put|delete|patch)\s*\(",
        ),
        "python",
        "client-call",
        _PY_HTTP_FILE_RX,
    ),
    (
        re.compile(r"\b\w*[Cc]lient\s*\.\s*(get|post|put|delete|patch|call|send|execute)\s*\("),
        "java",
        "client-call",
        _JAVA_HTTP_FILE_RX,
    ),
]


@dataclass(frozen=True)
class BindingCallPattern:
    """A client-class call-site pattern plus the binding that declared it.

    Carrying ``target_repo``/``paths`` on the pattern is what lets a call site
    like ``someApiClient.execute(payload)`` become a *cross-repo* edge: the
    consumer's source contains no URL, host, or service name, so the binding is
    the only thing that knows where the call lands.
    """

    pattern: re.Pattern[str]
    language: str
    purpose: str
    target_repo: str
    paths: tuple[str, ...]
    client: str


_BINDING_CLASS_RX: list[BindingCallPattern] = []


def configure_http_out_client_patterns(bindings: Iterable[ApiClientBinding]) -> None:
    """Build per-binding class-pattern regex entries from loaded ApiClientBindings."""
    global _BINDING_CLASS_RX  # noqa: PLW0603
    patterns: list[BindingCallPattern] = []
    for b in bindings:
        if not b.class_patterns:
            continue
        pat = "|".join(re.escape(p) for p in b.class_patterns if p)
        if not pat:
            continue
        patterns.append(BindingCallPattern(
            pattern=re.compile(pat),
            # Client libraries are published per language but consumed from
            # Java/Kotlin/Scala alike; the class name is the discriminator, so
            # this pattern is deliberately language-agnostic.
            language="any",
            purpose="api-client-consumer",
            target_repo=b.target_repo,
            paths=tuple(b.paths),
            client=b.maven_artifact,
        ))
    _BINDING_CLASS_RX = patterns


def get_binding_call_patterns() -> list[BindingCallPattern]:
    """Return the currently configured binding class-pattern call sites.

    Callers must go through this accessor rather than importing
    ``_BINDING_CLASS_RX`` by name: ``configure_http_out_client_patterns`` rebinds
    the module global, so a ``from ... import _BINDING_CLASS_RX`` snapshot taken
    at import time stays permanently empty and silently disables every
    ``class_patterns`` binding.
    """
    return _BINDING_CLASS_RX


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

# ---------------------------------------------------------------------------
# Per-file symbol resolution
#
# The ±3-line literal window only sees text, so a call site that reaches its
# endpoint through a named constant (`host + SUBMIT_PATH`), an enum member
# (`ApiPaths.SUBMIT_SYNC`), or an injected config value resolves to
# nothing. Building a cheap per-file map of identifier -> string literal, then
# substituting the identifiers referenced near the call, recovers the path
# without a full symbol table or type resolution.
# ---------------------------------------------------------------------------

# Identifiers referenced at a call site, optionally through a qualifier
# (`ApiPaths.SUBMIT_SYNC` -> `SUBMIT_SYNC`).
_IDENT_REF_RX = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{0,63})\b")
# Spring/`${...}` style externalised config placeholder.
_CONFIG_PLACEHOLDER_RX = re.compile(r"\$\{([A-Za-z0-9_.\-]{1,120})[^}\n]{0,80}\}")

# Only literals that could plausibly be an endpoint are worth remembering; this
# keeps the map small and stops unrelated literals leaking into call details.
_ENDPOINTISH_RX = re.compile(r"^(?:https?://|/[A-Za-z0-9_])")


def build_path_symbol_table(source: str) -> dict[str, str]:
    """Map identifier -> endpoint-like string literal declared in this file.

    Only URL/path-shaped literals are recorded, so the table stays small and
    substituting from it cannot invent a host or path that is not in the source.
    """
    return build_symbol_table(source, lambda value: bool(_ENDPOINTISH_RX.match(value)))


def _resolved_symbol_text(window: str, symbols: dict[str, str]) -> str:
    """Return the literals of every symbol from ``symbols`` referenced in ``window``."""
    if not symbols:
        return ""
    resolved: list[str] = []
    for m in _IDENT_REF_RX.finditer(window):
        value = symbols.get(m.group(1))
        if value is not None and value not in resolved:
            resolved.append(value)
    # Re-quote so extract_urls_and_paths' path-literal pattern sees them the way
    # it would an inline literal.
    return " ".join(f'"{v}"' for v in resolved)


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
    *,
    symbols: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an http-out detail dict (host, path, method, url) from the call context.

    ``symbols`` is the per-file identifier -> literal map from
    :func:`build_path_symbol_table`; identifiers referenced near the call are
    resolved through it so constant- and enum-mediated endpoints are captured.

    Inspects untrusted source text around the call; text is only parsed, never executed.
    """
    detail: dict[str, Any] = {
        "purpose": purpose,
        "raw": raw_call[:200],
    }
    window = _line_window(source, line_num, radius=3)
    blob = raw_call + "\n" + window
    resolved = _resolved_symbol_text(blob, symbols or {})
    if resolved:
        blob = blob + "\n" + resolved
        detail["resolved_symbols"] = resolved[:200]

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

    # A config-mediated base URL leaves only the property key at the call site
    # (e.g. `${some-service.base-url}`);
    # record it so the aggregators can try to resolve it to a service alias.
    cfg = _CONFIG_PLACEHOLDER_RX.search(blob)
    if cfg:
        detail["config_key"] = cfg.group(1)[:120]

    # Last resort for a call with no literal host at all: the surrounding code
    # usually still *names* the service it talks to — in a base-URL helper
    # (`get_some_service_base_url()`), a config key, or a field name. Resolving that
    # against the configured binding aliases keeps the cross-repo hop rather than
    # dropping the edge entirely.
    if "host" not in detail:
        from ..known_api_clients import binding_target_for_text

        hint = binding_target_for_text(blob)
        if hint:
            detail["target_repo"], alias = hint
            detail["target_repo_evidence"] = f"service alias {alias!r} in call context"
            # An alias appearing near the call is a strong hint, not a
            # declaration; only a class-pattern binding earns `high`.
            detail["target_repo_confidence"] = "medium"

    return detail
