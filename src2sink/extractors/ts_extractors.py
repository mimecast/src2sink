"""Tree-sitter call-site extraction and raw-code-payload linking."""

from __future__ import annotations

from typing import Any

from .ast_walk import extract_call_receiver, iter_calls, line_number, node_text
from .base import parse_source, supported_languages
from .file_context import FileExtractionContext
from .node_factory import make_edge, make_node
from .patterns import (
    SQL_EXECUTION_CALL_HINTS,
    SQL_EXECUTION_SINK_NAMES,
    SQL_SINK_NAMES,
    file_has_sql_evidence,
    iter_bound_payload_fields,
    sql_symbol_table,
    receiver_is_another_boundary,
    receiver_is_database,
    sql_parameterisation,
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


def _sql_verdict(detail: dict[str, Any]) -> bool | None:
    """Decide whether one observation is a SQL sink, and whether it executes.

    Returns ``None`` when the call is not SQL at all, otherwise whether it is an
    *execution* sink rather than an ORM-style one.

    The single place a SQL classification is made. Fixing `OI-26` — file-scoped
    evidence overriding a receiver known not to be a database — is a change to
    this function, applied to observations already on disk.
    """
    name, hint = detail["symbol"], detail["library_hint"]
    if not (name in SQL_SINK_NAMES or hint):
        return None
    if not _has_sql_evidence(detail, hint=hint):
        return None
    return name in SQL_EXECUTION_SINK_NAMES or hint


def _has_sql_evidence(detail: dict[str, Any], *, hint: bool) -> bool:
    """Whether anything vouches for a sink-named call actually being SQL.

    The three signals are not interchangeable, which is the whole of `OI-26`.
    They were OR'd together, so the weakest decided once satisfied — and the
    weakest is file-scoped, meaning one real query anywhere in a file admitted
    every sink-named call in it. ``httpClient.execute(r)`` became a SQL execution
    sink because a JDBC query sat in the same class, and execution sinks feed
    :func:`link_raw_code_payload_endpoints`, so it could fabricate the injection
    endpoint `OI-7` exists to prevent.

    Ordered by how *local* the evidence is:

    * a **library hint** names the SQL API in the call text itself, so it is
      self-evidencing and settles the question outright;
    * a **database receiver** is evidence about this call;
    * **file evidence** is a fact about other code in the same file, so it is
      the weakest. It rescues a call whose receiver is *unknown* —
      ``runner.execute(STATEMENT)`` — where there is nothing local to judge. It
      does **not** rescue a receiver we recognise as some other boundary, because
      that is negative local evidence and a fact about the neighbours cannot
      overturn it.

    The distinction that matters is between *unknown* and *known to be something
    else*. Collapsing the two either reinstates `OI-26` or withdraws the
    unknown-receiver recall that file evidence exists for.
    """
    if hint or detail["receiver_is_database"]:
        return True
    if receiver_is_another_boundary(detail["receiver"]):
        return False
    return bool(detail["file_sql_evidence"])


def classify_sql_from_observations(ctx: FileExtractionContext) -> None:
    """Emit `sql` sink nodes by classifying the call observations already recorded.

    Reads ``call-site`` observations and nothing else — not the source, not the
    AST. That is the point: a classification that depends only on stored data can
    be corrected and re-run without re-extracting the fleet, which is what makes
    `OI-26` and the catalogue work in `OI-20` cheap rather than a rescan.

    The rules are unchanged from when this ran inline. ``execute``, ``query`` and
    ``update`` are ordinary method names, so a name-only test catalogued
    ``httpClient.execute(request)`` as SQL — and, because an execution sink feeds
    :func:`link_raw_code_payload_endpoints`, let an HTTP proxy with a field named
    ``sql`` fabricate a ``raw-code-payload`` finding (`OI-7`). A name match
    therefore needs one positive signal: a database receiver, a library hint in
    the call text, or file-level SQL evidence.

    That last term is file-scoped and too coarse to settle a call-level question
    on its own; `OI-26` records the consequence. Fixing it is now a change to
    this function over existing observations.
    """
    for obs in [n for n in ctx.nodes if n.family == "call-site"]:
        d = obs.detail
        is_execution = _sql_verdict(d)
        if is_execution is None:
            continue
        node = make_node(
            repo=obs.repo,
            file=obs.file,
            line=obs.line,
            language=obs.language,
            kind="sink",
            family="sql",
            detail={
                "symbol": d["symbol"],
                "receiver": d["receiver"] or "",
                "execution": is_execution,
                "parameterised": d["parameterised"],
                "raw": d["raw"],
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

    # Classify only once every call in the file has been observed, so the
    # classifier sees the complete record rather than a prefix of it.
    classify_sql_from_observations(ctx)


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
