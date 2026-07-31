"""Regex-based flow-node extraction (one concern per function)."""

from __future__ import annotations

import re
from typing import Any

from ..constants import WEAK_ALGOS
from .file_context import FileExtractionContext
from .http_out import (
    HTTP_OUT_CALL_RX,
    HTTP_OUT_CONTEXT_CALL_RX,
    build_path_symbol_table,
    enrich_http_out_detail,
    get_binding_call_patterns,
)
from .node_factory import make_node
from .patterns import (
    AUTH_RX,
    CRYPTO_RX,
    FILE_SINK_RX,
    HTTP_IN_RX,
    PII_LOG_RX,
    PII_STORAGE_RX,
    QUEUE_RX,
    SECRETS_MANAGER_RX,
    SQL_SOURCE_RX,
)
from ..known_api_clients import binding_for_import
from ..vocabulary import (
    DANGEROUS_PAYLOAD_FIELD_REGEX,
    TENANT_FIELD_REGEX,
    PII_FIELD_REGEX,
    RAW_SQL_PAYLOAD_FIELD_NAMES,
    field_axes,
)

# Paths/lines where dangerous-payload field names are usually not injection taint.
_REGEX_PATH_RX = re.compile(r"/regex/|Regex", re.IGNORECASE)
_HTML_RESOURCE_PATH_RX = re.compile(
    r"htmlPages|/html/|/templates/|AccountResource",
    re.IGNORECASE,
)
_HTML_SCRIPT_LINE_RX = re.compile(
    r'\.append\s*\(\s*["\']?<script|language\s*=\s*["\']javascript',
    re.IGNORECASE,
)
_RAW_SQL_FIELD_IN_FILE = re.compile(
    r"\b(" + "|".join(re.escape(f) for f in RAW_SQL_PAYLOAD_FIELD_NAMES) + r")\b",
    re.IGNORECASE,
)


def _http_language_bucket(language: str) -> str | None:
    """Map a language id to its HTTP_IN_RX pattern bucket, or None if unsupported."""
    if language in ("java", "kotlin"):
        return "java-kotlin"
    if language == "python":
        return "python"
    if language in ("javascript", "typescript", "tsx"):
        return "javascript"
    if language == "go":
        return "go"
    return None


def _skip_dangerous_payload_field(rel_path: str, line_text: str, field: str) -> bool:
    """True when a dangerous-payload field name is a benign false positive for the path/line.

    Operates on untrusted scanned source; only inspects text, never executes it.
    """
    fl = field.lower()
    if fl in ("expression", "query") and _REGEX_PATH_RX.search(rel_path):
        return True
    if fl == "script":
        if _HTML_RESOURCE_PATH_RX.search(rel_path):
            return True
        if _HTML_SCRIPT_LINE_RX.search(line_text):
            return True
    return False


def extract_http_inbound(ctx: FileExtractionContext) -> None:
    """Emit http-in source nodes for inbound endpoint declarations found by regex.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    bucket = _http_language_bucket(ctx.language)
    if not bucket or bucket not in HTTP_IN_RX:
        return
    for pat, framework in HTTP_IN_RX[bucket]:
        for m in pat.finditer(ctx.source):
            groups = m.groups()
            if framework == "spring" and len(groups) >= 2:
                method = groups[0].replace("Mapping", "").upper()
                path = groups[1]
            elif len(groups) >= 2:
                method, path = groups[0].upper(), groups[1]
            elif groups:
                method, path = "?", groups[0]
            else:
                method, path = "?", "?"
            node = make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="source",
                family="http-in",
                framework=framework,
                detail={"method": method, "path": path, "raw": m.group(0)[:140]},
                confidence="high",
            )
            ctx.nodes.append(node)
            ctx.http_sources.append(node)


def extract_sql_string_sources(ctx: FileExtractionContext) -> None:
    """Emit sql source nodes for string-concatenated query patterns.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for pat, kind in SQL_SOURCE_RX:
        for m in pat.finditer(ctx.source):
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="source",
                family="sql",
                detail={"subkind": "string-concat", "pattern": kind, "snippet": m.group(0)[:160]},
                confidence="medium",
            ))


def extract_file_sinks(ctx: FileExtractionContext) -> None:
    """Emit file sink nodes for filesystem write/read operations.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for pat, sub in FILE_SINK_RX:
        for m in pat.finditer(ctx.source):
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="sink",
                family="file",
                detail={"operation": sub, "raw": m.group(0)[:120]},
                confidence="high",
            ))


def extract_api_client_imports(ctx: FileExtractionContext) -> None:
    """Emit api-client-consumer propagator nodes for known cross-repo client imports.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for line_no, line_text in enumerate(ctx.source.splitlines(), 1):
        binding = binding_for_import(line_text)
        if not binding:
            continue
        ctx.nodes.append(make_node(
            repo=ctx.repo_id,
            file=ctx.rel_path,
            line=line_no,
            language=ctx.language,
            kind="propagator",
            family="api-client-consumer",
            detail={
                "client": binding.maven_artifact,
                "target_repo": binding.target_repo,
                "import": line_text.strip()[:120],
                "paths": list(binding.paths),
            },
            data_class="raw-sql-payload",
            confidence="high",
        ))


# Which extractor languages each call-site language hint applies to. The hint
# names an *ecosystem*, not a file extension: a Spring pattern is equally valid
# in Kotlin or Scala, and a browser `fetch(` in TSX. The previous membership test
# (`lang_hint not in ("java", "javascript", "python", "go")`) was always true for
# every hint in the table, so the filter never excluded anything and e.g. Python
# `requests.` patterns were run against Java sources.
_CALL_RX_LANGUAGES: dict[str, frozenset[str]] = {
    "java": frozenset({"java", "kotlin", "scala"}),
    "python": frozenset({"python"}),
    "javascript": frozenset({"javascript", "typescript", "tsx"}),
    "go": frozenset({"go"}),
}


def _call_rx_applies(lang_hint: str, language: str) -> bool:
    """True when a call-site pattern's language hint covers this file's language."""
    if lang_hint == "any":
        return True
    return language in _CALL_RX_LANGUAGES.get(lang_hint, frozenset({lang_hint}))


def extract_http_outbound(ctx: FileExtractionContext) -> None:
    """Emit http-out sink nodes for outbound HTTP client calls, enriched with URL/path.

    Runs three tiers of call-site patterns: unconditional library patterns,
    context-gated patterns that need file-level HTTP evidence before a broad
    receiver match is trusted, and configured api-client `class_patterns` (which
    additionally stamp the binding's ``target_repo`` onto the node, since the
    consumer's own source names no host or path at all).

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    symbols = build_path_symbol_table(ctx.source)

    def emit(pat: re.Pattern[str], purpose: str, binding: Any = None) -> None:
        """Emit one http-out node per match of ``pat``, enriched from its context."""
        for m in pat.finditer(ctx.source):
            line = ctx.line_number(m.start())
            raw = m.group(0)
            detail = enrich_http_out_detail(
                ctx.source, line, raw, purpose, symbols=symbols,
            )
            if binding is not None:
                # The binding is authoritative for where this call lands, so it
                # overrides any alias guessed from the call context.
                detail["target_repo"] = binding.target_repo
                detail["target_repo_evidence"] = f"api-client class {binding.client}"
                detail["target_repo_confidence"] = "high"
                detail["client"] = binding.client
                detail["client_paths"] = list(binding.paths)
            conf = (
                "high"
                if detail.get("url") or detail.get("path") or binding is not None
                else "medium"
            )
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=line,
                language=ctx.language,
                kind="sink",
                family="http-out",
                detail=detail,
                confidence=conf,
            ))

    for pat, lang_hint, purpose in HTTP_OUT_CALL_RX:
        if _call_rx_applies(lang_hint, ctx.language):
            emit(pat, purpose)

    for pat, lang_hint, purpose, file_guard in HTTP_OUT_CONTEXT_CALL_RX:
        if _call_rx_applies(lang_hint, ctx.language) and file_guard.search(ctx.source):
            emit(pat, purpose)

    for bp in get_binding_call_patterns():
        if _call_rx_applies(bp.language, ctx.language):
            emit(bp.pattern, bp.purpose, binding=bp)


# Endpoint-ish constant names, used to accept a single-segment path that would
# otherwise be too generic to be worth a node.
_ENDPOINT_CONST_NAME_RX = re.compile(
    r"PATH|URL|URI|ENDPOINT|ROUTE|_API|API_|RESOURCE",
    re.IGNORECASE,
)
# `NAME = "/path"` and Java/Kotlin enum members `NAME("/path")`. Both string runs
# are length-bounded to keep the patterns linear on hostile input.
PATH_CONST_RX = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]{0,63})\s*"
    r"(?::\s*[A-Za-z_][A-Za-z0-9_<>\[\].,? ]{0,63})?"
    r"\s*(?::?=)\s*[\"'](/[A-Za-z0-9_][A-Za-z0-9_./\-{}]{0,200})[\"']"
)
PATH_ENUM_RX = re.compile(
    r"\b([A-Z][A-Z0-9_]{1,63})\s*\(\s*[\"'](/[A-Za-z0-9_][A-Za-z0-9_./\-{}]{0,200})[\"']"
)
# A trailing filename-with-extension means a resource/file path, not a route.
_FILE_PATH_RX = re.compile(r"/[^/]*\.[A-Za-z0-9]{1,6}$")
_MAX_PATH_CONSTANTS_PER_FILE = 100


def _is_route_like_constant(name: str, path: str) -> bool:
    """True when a `NAME = "/..."` constant plausibly names an HTTP route."""
    if _FILE_PATH_RX.search(path):
        return False
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    # Multi-segment paths stand on their own; a single segment (`/queries`) is
    # only kept when the constant is explicitly named like an endpoint, so
    # generic one-word literals do not each become a node.
    return len(segments) >= 2 or bool(_ENDPOINT_CONST_NAME_RX.search(name))


def extract_path_constants(ctx: FileExtractionContext) -> None:
    """Emit path-constant reference nodes for route-like string constants and enum values.

    A call site that builds its URL from a named constant or enum member
    (``host + PATH_QUERY``, ``ApiPaths.SUBMIT_SYNC``) carries no literal
    the ±3-line window can see. In-file uses are resolved directly by
    ``build_path_symbol_table``; this pass covers the cross-file case by making
    the declaration itself a node, so the aggregators' path/URL reference scan
    can match it to an inbound route in another repo.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    seen: set[str] = set()
    for rx in (PATH_CONST_RX, PATH_ENUM_RX):
        for m in rx.finditer(ctx.source):
            if len(seen) >= _MAX_PATH_CONSTANTS_PER_FILE:
                return
            name, path = m.group(1), m.group(2)
            if path in seen or not _is_route_like_constant(name, path):
                continue
            seen.add(path)
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="reference",
                family="path-constant",
                detail={"path": path, "symbol": name, "raw": m.group(0)[:120]},
                confidence="medium",
            ))


def extract_queue_io(ctx: FileExtractionContext) -> None:
    """Emit queue-pub/queue-sub nodes for message-queue produce/consume calls.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for pat, direction, system in QUEUE_RX:
        for m in pat.finditer(ctx.source):
            topic = m.group(1) if m.groups() else "?"
            family = "queue-pub" if direction == "produce" else "queue-sub"
            kind = "sink" if direction == "produce" else "source"
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind=kind,
                family=family,
                detail={"system": system, "topic": topic, "direction": direction},
                confidence="high",
            ))


def extract_crypto_and_auth(ctx: FileExtractionContext) -> None:
    """Emit crypto-algorithm, crypto-key-source, and auth nodes from regex matches.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for pat, sub in CRYPTO_RX:
        for m in pat.finditer(ctx.source):
            algo = m.group(1) if m.groups() else ""
            weak = algo.upper() in WEAK_ALGOS if algo else False
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="sink",
                family="crypto-algorithm",
                detail={"algorithm": algo, "subkind": sub, "weak": weak},
                confidence="high",
            ))

    for pat, provider in SECRETS_MANAGER_RX:
        for m in pat.finditer(ctx.source):
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="propagator",
                family="crypto-key-source",
                detail={"provider": provider, "raw": m.group(0)[:120]},
                confidence="high",
            ))

    for pat, label in AUTH_RX:
        for m in pat.finditer(ctx.source):
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="propagator",
                family="auth",
                detail={"pattern": label, "raw": m.group(0)[:100]},
                confidence="medium",
            ))


def extract_pii_field_declarations(ctx: FileExtractionContext) -> None:
    """Emit pii-field source nodes for field declarations matching the PII vocabulary.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for m in PII_FIELD_REGEX.finditer(ctx.source):
        line_text = ctx.line_text_at(m.start())
        if line_text.lstrip().startswith(("//", "#", "*", "/*")):
            continue
        field = m.group(1)
        pii_c, mime_c = field_axes(field)
        ctx.nodes.append(make_node(
            repo=ctx.repo_id,
            file=ctx.rel_path,
            line=ctx.line_number(m.start()),
            language=ctx.language,
            kind="source",
            family="pii-field",
            detail={"field_name": field},
            pii_classification=pii_c if pii_c != "unknown" else None,
            data_class=mime_c,
            confidence="medium",
        ))


def extract_data_class_field_declarations(ctx: FileExtractionContext) -> None:
    """Emit data-class-field source nodes for tenant/dangerous-payload field names.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for regex in (TENANT_FIELD_REGEX, DANGEROUS_PAYLOAD_FIELD_REGEX):
        for m in regex.finditer(ctx.source):
            field = m.group(1)
            _, mime_c = field_axes(field)
            if not mime_c:
                continue
            line_text = ctx.line_text_at(m.start())
            if mime_c == "dangerous-payload" and _skip_dangerous_payload_field(
                ctx.rel_path, line_text, field,
            ):
                continue
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="source",
                family="data-class-field",
                detail={"field_name": field},
                data_class=mime_c,
                confidence="medium",
            ))


def extract_raw_sql_field_markers(ctx: FileExtractionContext) -> None:
    """Record line numbers of raw-SQL payload field names for later cross-pass linking.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    for m in _RAW_SQL_FIELD_IN_FILE.finditer(ctx.source):
        ctx.raw_sql_field_lines.append(ctx.line_number(m.start()))


def extract_pii_sinks(ctx: FileExtractionContext) -> None:
    """Emit pii-log and pii-storage sink nodes where PII tokens appear near the sink.

    Scans untrusted source text; matches are only recorded, never evaluated.
    """
    # Logger calls: only emitted when a PII vocabulary token is near the call.
    for m in PII_LOG_RX.finditer(ctx.source):
        window = ctx.source[max(0, m.start() - 200) : m.end() + 400]
        pii_hit = PII_FIELD_REGEX.search(window)
        if not pii_hit:
            continue
        field = pii_hit.group(1)
        pii_c, mime_c = field_axes(field)
        ctx.nodes.append(make_node(
            repo=ctx.repo_id,
            file=ctx.rel_path,
            line=ctx.line_number(m.start()),
            language=ctx.language,
            kind="sink",
            family="pii-log",
            detail={"field_name": field, "raw": m.group(0)[:80]},
            pii_classification=pii_c if pii_c != "unknown" else None,
            data_class=mime_c,
            confidence="high",
        ))

    # Persistence / S3 / email SDK: field_name null when no PII token in ±120 chars.
    for pat, sub in PII_STORAGE_RX:
        for m in pat.finditer(ctx.source):
            window = ctx.source[max(0, m.start() - 120) : m.end() + 120]
            pii_hit = PII_FIELD_REGEX.search(window)
            field = pii_hit.group(1) if pii_hit else None
            pii_c, mime_c = field_axes(field) if field else (None, None)
            ctx.nodes.append(make_node(
                repo=ctx.repo_id,
                file=ctx.rel_path,
                line=ctx.line_number(m.start()),
                language=ctx.language,
                kind="sink",
                family="pii-storage",
                detail={"subkind": sub, "field_name": field},
                pii_classification=pii_c,
                data_class=mime_c,
                confidence="medium" if field else "low",
            ))
