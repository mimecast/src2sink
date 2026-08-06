"""Tree-sitter call-site extraction and raw-code-payload linking."""

from __future__ import annotations

from .ast_walk import extract_call_receiver, iter_calls, line_number, node_text
from .base import parse_source, supported_languages
from .file_context import FileExtractionContext
from .node_factory import make_node
from .patterns import (
    SQL_EXECUTION_CALL_HINTS,
    SQL_SINK_NAMES,
    file_has_sql_evidence,
    iter_bound_payload_fields,
    receiver_is_database,
    sql_parameterisation,
    sql_symbol_table,
)


# The call names any family currently examines. Observations are emitted for
# these and nothing else, so volume stays bounded by sink-shaped names rather
# than by every call in the file.
SCRIPT_EXEC_NAMES = frozenset({"eval", "exec", "compile"})


def _add_call_observation(
    ctx: FileExtractionContext,
    name: str,
    line: int,
    call_text: str,
    receiver: str | None,
    *,
    file_sql_evidence: bool,
    library_hint: bool,
    parameterised: str,
) -> None:
    """Record that a call was examined, and what could be told about it.

    An *observation*, not a finding: ``kind`` is ``reference`` and it claims
    nothing about danger. It exists so that classification can happen downstream
    — a classifier reading these never has to re-read the source, so changing
    what a library *means* becomes a re-aggregation instead of a fleet rescan
    (see docs/plans/observe-then-classify.md §3).

    ``file_sql_evidence`` is recorded rather than acted on. It is a file-scoped
    fact, too coarse to decide a call-level question by itself — which is exactly
    the defect ``OI-26`` describes — so a classifier gets to weigh it against the
    receiver instead of being handed a verdict.

    Records untrusted call text; text is only matched, never executed.
    """
    ctx.nodes.append(make_node(
        repo=ctx.repo_id,
        file=ctx.rel_path,
        line=line,
        language=ctx.language,
        kind="reference",
        family="call-site",
        detail={
            "symbol": name,
            "receiver": receiver,
            "receiver_is_database": receiver_is_database(receiver),
            "library_hint": library_hint,
            "file_sql_evidence": file_sql_evidence,
            # Observed, not classified: `sql_parameterisation` falls back to
            # scanning the whole file when the call text carries no literal of
            # its own, so the posture can only be computed here, where the source
            # is in hand. It says what the statement *looks like*, not whether it
            # is dangerous — which keeps it an observation.
            "parameterised": parameterised,
            "raw": call_text[:160],
        },
        confidence="high",
    ))


def _maybe_add_script_exec(ctx: FileExtractionContext, name: str, line: int, call_text: str) -> None:
    """Append a script-exec sink node for eval/exec/compile calls.

    Inspects untrusted call text; text is only matched, never executed.
    """
    if name in SCRIPT_EXEC_NAMES:
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
        _examine_call(
            ctx, name,
            line=line_number(src_bytes, call_node),
            call_text=node_text(src_bytes, call_node),
            receiver=extract_call_receiver(src_bytes, call_node, ctx.language),
            file_sql_evidence=file_sql_evidence,
            sql_symbols=sql_symbols,
        )


def _examine_call(
    ctx: FileExtractionContext,
    name: str,
    *,
    line: int,
    call_text: str,
    receiver: str | None,
    file_sql_evidence: bool,
    sql_symbols: dict[str, str],
) -> None:
    """Observe one call, then let each family classify it.

    Observation comes first and is unconditional for a sink-shaped name: the
    record keeps what was seen whether or not a family claims it, so a classifier
    can be corrected later without re-extracting (`OI-26`, and the catalogue work
    in `OI-20`). The family dispatch below is the *current* classification, still
    inline; moving it downstream is the next step of
    docs/plans/observe-then-classify.md §3.
    """
    library_hint = any(hint in call_text for hint in SQL_EXECUTION_CALL_HINTS)
    if name in SQL_SINK_NAMES or library_hint or name in SCRIPT_EXEC_NAMES:
        _add_call_observation(
            ctx, name, line, call_text, receiver,
            file_sql_evidence=file_sql_evidence,
            library_hint=library_hint,
            parameterised=sql_parameterisation(call_text, ctx.source, sql_symbols),
        )
    _maybe_add_script_exec(ctx, name, line, call_text)


def link_sql_payload_out(ctx: FileExtractionContext) -> None:
    """Tag an outbound request that carries SQL in its body (sql-payload-out).

    The mirror of :func:`link_raw_code_payload_endpoints`, which is structurally
    *inbound* — it requires an ``http-in`` node, so it only ever fires on the
    service that receives SQL. A repo that *sends* SQL to another service had no
    family at all: not a local ``sql`` sink, since nothing executes here, and not
    an ordinary ``http-out``, since the payload is executable code at the far end
    (OI-9).

    Requires both halves in the same file — a payload field being bound, and an
    outbound call to carry it. A data class declaring a ``sql`` field sends
    nothing, and an outbound call with no such field is ordinary traffic.
    """
    if not ctx.http_out_sinks:
        return

    for pos, field, by_binding in iter_bound_payload_fields(ctx.source):
        line = ctx.line_number(pos)
        # Attribute to the nearest outbound call at or after the binding: a
        # payload is populated before the request that carries it.
        following = [n for n in ctx.http_out_sinks if n.line >= line]
        carrier = min(following or ctx.http_out_sinks, key=lambda n: abs(n.line - line))
        detail = {
            "field_name": field,
            "http_out_line": carrier.line,
            "path": carrier.detail.get("path", ""),
            "target_repo": carrier.detail.get("target_repo", ""),
            "client": carrier.detail.get("client", ""),
            "evidence": (
                "binding payload_fields" if by_binding else "payload field vocabulary"
            ),
        }
        ctx.nodes.append(make_node(
            repo=ctx.repo_id,
            file=ctx.rel_path,
            line=line,
            language=ctx.language,
            kind="sink",
            family="sql-payload-out",
            detail=detail,
            data_class="raw-sql-payload",
            # A binding declaring the field is a statement that this service
            # takes it as executable input; the generic vocabulary is a guess.
            confidence="high" if by_binding else "medium",
        ))

