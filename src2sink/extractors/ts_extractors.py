"""Tree-sitter call-site extraction and raw-code-payload linking."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Tree

from .ast_walk import (
    extract_call_arguments,
    extract_call_receiver,
    iter_calls,
    iter_method_declarations,
    iter_type_declarations,
    line_number,
    node_text,
)
from ..constants import NOTE_PARSE_FAILED
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


# Names that are a sink in themselves. Observations are no longer limited to
# these — `OI-17` step 3 widened them to every call, because a call graph
# needs the hops between the ends, not only the ends.
SCRIPT_EXEC_NAMES = frozenset({"eval", "exec", "compile"})


def _parse_or_note(ctx: FileExtractionContext, ts_lang: str, src_bytes: bytes) -> Tree | None:
    """Parse the file, recording *why* on failure instead of returning empty.

    Three passes parsed the same file and each swallowed the failure the same
    way, so a file tree-sitter could not read took part in no path and the
    answer came back "nothing reaches a sink here" — at full confidence, from a
    foundation that had not been read. That is `OI-36` sitting underneath
    `OI-17`.

    The note is deduplicated because all three passes hit the same file and fail
    identically; one file that would not parse should read as one problem.
    """
    try:
        return parse_source(ts_lang, src_bytes)
    except (KeyError, OSError, ValueError) as exc:
        note = (
            f"{ctx.rel_path}: {ctx.language} {NOTE_PARSE_FAILED} "
            f"({type(exc).__name__}); no calls, declarations or types were "
            "extracted from this file, so it takes part in no path"
        )
        if note not in ctx.notes:
            ctx.notes.append(note)
        return None


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
    arguments: list[str],
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
            # What the call passes, as written. Resolution (`OI-17` step 3) does
            # not need it; the tainted-path search (step 4) does, and recording
            # it now rides the rescan that widening already costs.
            "arguments": arguments,
            "raw": call_text[:160],
        },
        confidence="high",
    ))


def _add_plain_call_observation(
    ctx: FileExtractionContext,
    name: str,
    line: int,
    receiver: str | None,
    arguments: list[str],
) -> None:
    """Record an ordinary call — the hops a path is made of (`OI-17` step 3).

    Deliberately leaner than :func:`_add_call_observation`. Widening to every
    call is what makes a call graph possible, and it is also the single largest
    thing this tool has ever added to a record: measured on a real repository,
    call sites are **75% of all nodes** at ~54 per file, against ~2 per file on
    the synthetic corpus the original estimate came from.

    So an ordinary call records only what resolving it needs — the name, the
    receiver, the arguments, and the scope stamped on every node — and drops the
    SQL-classification fields along with ``raw``. ``raw`` is the expensive one:
    up to 160 bytes of source text per call, which for a call that is not a sink
    says nothing that ``symbol``, ``receiver`` and ``arguments`` do not already
    say. That single omission is most of the difference between a 4.7x record
    and a 2.2x one.

    A sink-shaped call still gets the full observation, because there the raw
    text *is* the evidence a reader checks.
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
            "arguments": arguments,
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
    tree = _parse_or_note(ctx, ts_lang, src_bytes)
    if tree is None:
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
            arguments=extract_call_arguments(src_bytes, call_node, ctx.language),
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
    arguments: list[str],
    file_sql_evidence: bool,
    sql_symbols: dict[str, str],
) -> None:
    """Observe one call, then let each family classify it.

    Observation comes first and is unconditional — for **every** call, not only
    a sink-shaped name (`OI-17` step 3). The record keeps what was seen whether
    or not a family claims it, so a classifier can be corrected later without
    re-extracting (`OI-26`, and the catalogue work in `OI-20`).

    Widening is what makes a call graph possible at all. `stockService.process()`
    is the middle of every layered path and was recorded nowhere, because the
    filter only kept names that looked like sinks — so the tool could see both
    ends of a path and never the part that joins them.
    """
    library_hint = any(hint in call_text for hint in SQL_EXECUTION_CALL_HINTS)
    if name in SQL_SINK_NAMES or library_hint or name in SCRIPT_EXEC_NAMES:
        _add_call_observation(
            ctx, name, line, call_text, receiver,
            file_sql_evidence=file_sql_evidence,
            library_hint=library_hint,
            parameterised=sql_parameterisation(call_text, ctx.source, sql_symbols),
            arguments=arguments,
        )
    else:
        _add_plain_call_observation(ctx, name, line, receiver, arguments)
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



def extract_method_declarations(ctx: FileExtractionContext) -> None:
    """Record each callable this file declares, with its class, params and span.

    The unit a source-to-sink path is made of. Nothing was extracted for them,
    which is why a node could say `file:line` and nothing about which door it sat
    behind (`OI-17`).

    An observation: it records what the file contains, never that anything is
    wrong.
    """
    ts_lang = ctx.language if ctx.language in supported_languages() else None
    if not ts_lang:
        return
    src_bytes = ctx.source.encode("utf-8")
    tree = _parse_or_note(ctx, ts_lang, src_bytes)
    if tree is None:
        return

    for owner, name, params, start, end, self_name in iter_method_declarations(
        src_bytes, tree.root_node, ctx.language
    ):
        ctx.nodes.append(make_node(
            repo=ctx.repo_id,
            file=ctx.rel_path,
            line=start,
            language=ctx.language,
            kind="reference",
            family="method-decl",
            detail={
                "class": owner,
                "method": name,
                "params": params,
                "start_line": start,
                "end_line": end,
                # Only Go sets this, and only when the method has a receiver, so
                # the key is absent everywhere else rather than carrying a null
                # on every method declaration in the fleet.
                **({"self_name": self_name} if self_name else {}),
            },
            confidence="high",
        ))


def extract_type_declarations(ctx: FileExtractionContext) -> None:
    """Record each type this file declares, with its field types and supertypes.

    The facts a call is resolved against (`OI-17`). A field's declared type is
    what makes `stockService.process()` resolvable offline; the supertypes are
    what let a call on an interface reach the implementations that have a body,
    which is the difference between a weak answer and a dead end.

    An observation: it records what the file declares, never that anything is
    wrong.
    """
    ts_lang = ctx.language if ctx.language in supported_languages() else None
    if not ts_lang:
        return
    src_bytes = ctx.source.encode("utf-8")
    tree = _parse_or_note(ctx, ts_lang, src_bytes)
    if tree is None:
        return

    for name, fields, supertypes, is_interface, line in iter_type_declarations(
        src_bytes, tree.root_node, ctx.language
    ):
        ctx.nodes.append(make_node(
            repo=ctx.repo_id,
            file=ctx.rel_path,
            line=line,
            language=ctx.language,
            kind="reference",
            family="type-decl",
            detail={
                "class": name,
                "fields": fields,
                "supertypes": supertypes,
                "is_interface": is_interface,
            },
            confidence="high",
        ))


def assign_enclosing_methods(ctx: FileExtractionContext) -> None:
    """Stamp every node with the method and class it sits inside.

    Assigned by span containment, innermost first, so a nested function keeps its
    own findings rather than handing them to its parent. A node inside no method
    — a field, a class-level constant — is left unstamped rather than attached to
    whichever method happens to follow it, because a guessed scope is worse than
    an absent one.
    """
    spans = sorted(
        (
            (n.detail["start_line"], n.detail["end_line"], n.detail["class"], n.detail["method"])
            for n in ctx.nodes
            if n.family == "method-decl"
        ),
        key=lambda s: s[1] - s[0],
    )
    if not spans:
        return
    for node in ctx.nodes:
        if node.family == "method-decl":
            continue
        for start, end, owner, method in spans:
            if start <= node.line <= end:
                node.detail["enclosing_class"] = owner
                node.detail["enclosing_method"] = method
                break
