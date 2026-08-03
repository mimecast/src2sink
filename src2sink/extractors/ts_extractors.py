"""Tree-sitter call-site extraction and raw-code-payload linking."""

from __future__ import annotations

from .ast_walk import extract_call_receiver, iter_calls, line_number, node_text
from .base import parse_source, supported_languages
from .file_context import FileExtractionContext
from .node_factory import make_edge, make_node
from .patterns import (
    SQL_EXECUTION_CALL_HINTS,
    SQL_EXECUTION_SINK_NAMES,
    SQL_SINK_NAMES,
    file_has_sql_evidence,
    sql_symbol_table,
    receiver_is_database,
    sql_parameterisation,
)


def _maybe_add_sql_sink(
    ctx: FileExtractionContext,
    name: str,
    line: int,
    call_text: str,
    receiver: str | None,
    *,
    file_sql_evidence: bool,
    sql_symbols: dict[str, str],
) -> None:
    """Append a sql sink node if the call is evidenced as database work.

    ``execute``, ``query`` and ``update`` are ordinary method names, so a name-only
    test catalogued ``httpClient.execute(request)`` and ``messageDigest.update(data)``
    as SQL execution sinks — and, because an execution sink feeds
    :func:`link_raw_code_payload_endpoints`, let an HTTP proxy with a field named
    ``sql`` fabricate a ``raw-code-payload`` finding (OI-7).

    A name match therefore needs one positive signal: a database receiver, an
    explicit library hint in the call text, or file-level SQL evidence.
    ``file_sql_evidence`` is computed once per file by the caller.

    Inspects untrusted call text; text is only matched, never executed.
    """
    has_hint = any(hint in call_text for hint in SQL_EXECUTION_CALL_HINTS)
    if not (name in SQL_SINK_NAMES or has_hint):
        return
    # A library hint names the SQL API outright, so it is self-evidencing; a bare
    # verb needs the receiver or the file to vouch for it.
    if not (has_hint or receiver_is_database(receiver) or file_sql_evidence):
        return

    is_execution = name in SQL_EXECUTION_SINK_NAMES or has_hint
    node = make_node(
        repo=ctx.repo_id,
        file=ctx.rel_path,
        line=line,
        language=ctx.language,
        kind="sink",
        family="sql",
        detail={
            "symbol": name,
            "receiver": receiver or "",
            "execution": is_execution,
            "parameterised": sql_parameterisation(call_text, ctx.source, sql_symbols),
            "raw": call_text[:160],
        },
        confidence="high" if is_execution else "medium",
    )
    ctx.nodes.append(node)
    if is_execution:
        ctx.sql_execution_sinks.append(node)


def _maybe_add_script_exec(ctx: FileExtractionContext, name: str, line: int, call_text: str) -> None:
    """Append a script-exec sink node for eval/exec/compile calls.

    Inspects untrusted call text; text is only matched, never executed.
    """
    if name in ("eval", "exec", "compile"):
        ctx.nodes.append(make_node(
            repo=ctx.repo_id,
            file=ctx.rel_path,
            line=line,
            language=ctx.language,
            kind="sink",
            family="script-exec",
            detail={"symbol": name, "raw": call_text[:120]},
            data_class="raw-script-expression",
            confidence="high",
        ))


def extract_tree_sitter_calls(ctx: FileExtractionContext) -> None:
    """SQL execution sinks and script-exec calls from the AST."""
    ts_lang = ctx.language if ctx.language in supported_languages() else None
    if ts_lang == "kotlin" and "kotlin" not in supported_languages():
        ts_lang = None
    if not ts_lang:
        return

    src_bytes = ctx.source.encode("utf-8")
    try:
        tree = parse_source(ts_lang, src_bytes)
    except (KeyError, OSError, ValueError):
        return

    # Computed once per file, not once per call: the answer cannot vary within a
    # file and the scan is over the whole source.
    file_sql_evidence = file_has_sql_evidence(ctx.source)
    # Resolves a base query held in a constant, so a clause concatenated onto it
    # is seen as construction rather than as a verbatim statement (OI-11).
    sql_symbols = sql_symbol_table(ctx.source)

    for call_node, name in iter_calls(src_bytes, tree.root_node, ctx.language):
        line = line_number(src_bytes, call_node)
        call_text = node_text(src_bytes, call_node)
        receiver = extract_call_receiver(src_bytes, call_node, ctx.language)
        _maybe_add_sql_sink(
            ctx, name, line, call_text, receiver,
            file_sql_evidence=file_sql_evidence, sql_symbols=sql_symbols,
        )
        _maybe_add_script_exec(ctx, name, line, call_text)


def link_raw_code_payload_endpoints(ctx: FileExtractionContext) -> None:
    """When a file has http-in + sql field name + JDBC execution, tag the endpoint."""
    if not (ctx.raw_sql_field_lines and ctx.http_sources and ctx.sql_execution_sinks):
        return

    seen: set[tuple[str | None, int, str | None]] = set()
    for ep in ctx.http_sources:
        for sink in ctx.sql_execution_sinks:
            for field_line in ctx.raw_sql_field_lines:
                key = (ep.detail.get("path"), field_line, sink.detail.get("symbol"))
                if key in seen:
                    continue
                seen.add(key)
                ctx.nodes.append(make_node(
                    repo=ctx.repo_id,
                    file=ctx.rel_path,
                    line=field_line,
                    language=ctx.language,
                    kind="source",
                    family="raw-code-payload",
                    detail={
                        "field_line": field_line,
                        "endpoint_path": ep.detail.get("path"),
                        "sink_symbol": sink.detail.get("symbol"),
                        "sink_line": sink.line,
                    },
                    data_class="raw-sql-payload",
                    confidence="high",
                ))
                ctx.edges.append(make_edge(
                    ep,
                    sink,
                    kind="intra-file",
                    evidence=(
                        f"sql payload field (line {field_line}) on {ep.detail.get('path')}"
                        f" → {sink.detail.get('symbol')} (line {sink.line})"
                    ),
                    confidence="medium",
                ))
