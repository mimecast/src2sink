"""Turn recorded observations into findings, without reading any source.

Extraction records *what was seen*: a call, its receiver, whether that receiver
reads as a database, whether the file showed SQL evidence. This module decides
*what that means*.

The split exists so that a correction is cheap. While classification ran inside
extraction, changing what a library means changed which nodes existed, so every
change to a rule needed the fleet re-parsed — and a defect like `OI-26` could
only be fixed by re-scanning every repository. Derivation reads records, so the
same correction is a pass over data already on disk: no repositories checked
out, no parsing, no `DETECTION_VERSION` bump.

Everything here is a pure function of the observation nodes handed to it. If
something in this module ever needs the source text, the observation record was
missing a field and *that* is the bug — see
``tests/test_derive_pass.py::test_derivation_reproduces_the_findings_from_observations_alone``,
which enforces it by throwing the source away.

See docs/plans/observe-then-classify.md §3.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .extractors.node_factory import make_edge, make_node
from .extractors.patterns import (
    SQL_EXECUTION_SINK_NAMES,
    SQL_SINK_NAMES,
    receiver_is_another_boundary,
)
from .paths import find_tainted_paths, path_details
from .resolve import call_edges
from .schema import FlowEdge, FlowNode

# Produced *here* rather than by extraction. Keyed on (family, kind) rather than
# family alone, because `sql` is both: a `sql`/`sink` is derived from a call
# observation, while a `sql`/`source` is a string-concatenation finding the regex
# pass observes directly. Stripping by family would delete the latter and
# derivation would not rebuild it.
# What *interpreted* the observations, as distinct from what recorded them.
#
# Bumping this invalidates derived nodes and nothing else: the observations are
# still good, so a repo is re-derived from its existing record rather than
# re-parsed. Bump DETECTION_VERSION instead when what is *observed* changes,
# which is the more expensive event.
#
# It lives here rather than in schema.py deliberately. schema.py is a *detection*
# input, so a bump there would change the detection fingerprint and force the
# fleet rescan this separation exists to avoid.
DERIVATION_VERSION = 6

DERIVED_FAMILIES = frozenset({
    ("sql", "sink"),
    ("raw-code-payload", "source"),
    ("entry-point", "source"),
    ("tainted-path", "finding"),
})

# Families that are pure observation: recorded by extraction, never findings in
# themselves, and read by the derivations below.
OBSERVATION_FAMILIES = frozenset({"call-site", "sql-field-marker", "entry-marker"})


def is_derived(node: FlowNode) -> bool:
    """True if a node is produced by derivation rather than observed by extraction.

    The inverse selects the input to :func:`derive_from_observations`, so this is
    what makes a record re-derivable: strip the derived nodes and what remains is
    exactly what the scan saw.
    """
    return (node.family, node.kind) in DERIVED_FAMILIES


def _has_sql_evidence(detail: dict[str, Any], *, hint: bool) -> bool:
    """Whether anything vouches for a sink-named call actually being SQL.

    The three signals are not interchangeable, which is the whole of `OI-26`.
    They were OR'd together, so the weakest decided once satisfied — and the
    weakest is file-scoped, meaning one real query anywhere in a file admitted
    every sink-named call in it. ``httpClient.execute(r)`` became a SQL execution
    sink because a JDBC query sat in the same class, and execution sinks feed
    :func:`link_raw_code_payloads`, so it could fabricate the injection endpoint
    `OI-7` exists to prevent.

    Ordered by how *local* the evidence is:

    * a **library hint** names the SQL API in the call text itself, so it is
      self-evidencing and settles the question outright;
    * a **database receiver** is evidence about this call;
    * **file evidence** is a fact about other code in the same file, so it is the
      weakest. It rescues a call whose receiver is *unknown* —
      ``runner.execute(STATEMENT)`` — where there is nothing local to judge. It
      does **not** rescue a receiver recognised as some other boundary, because
      that is negative local evidence and a fact about the neighbours cannot
      overturn it.

    The distinction that matters is between *unknown* and *known to be something
    else*. Collapsing the two either reinstates `OI-26` or withdraws the
    unknown-receiver recall that file evidence exists for.
    """
    if hint or detail.get("receiver_is_database", False):
        return True
    if receiver_is_another_boundary(detail.get("receiver")):
        return False
    return bool(detail.get("file_sql_evidence", False))


def sql_verdict(detail: dict[str, Any]) -> bool | None:
    """Decide whether one observation is a SQL sink, and whether it executes.

    Returns ``None`` when the call is not SQL at all, otherwise whether it is an
    *execution* sink rather than an ORM-style one. The single place a SQL
    classification is made.
    """
    name = detail["symbol"]
    # `.get`, because `OI-17` step 3 widened observation to every call and an
    # ordinary call carries none of the SQL fields — recording them for the ~75%
    # of nodes that are now plain call sites would cost far more than it tells.
    # Absent reads as "no SQL evidence", which is the truthful default: nothing
    # vouched for this call being SQL because nothing looked.
    hint = detail.get("library_hint", False)
    if not (name in SQL_SINK_NAMES or hint):
        return None
    if not _has_sql_evidence(detail, hint=hint):
        return None
    return name in SQL_EXECUTION_SINK_NAMES or hint


def classify_sql(observations: list[FlowNode]) -> list[FlowNode]:
    """Emit `sql` sink nodes for the call observations that classify as SQL."""
    out: list[FlowNode] = []
    for obs in observations:
        if obs.family != "call-site":
            continue
        d = obs.detail
        is_execution = sql_verdict(d)
        if is_execution is None:
            continue
        out.append(make_node(
            repo=obs.repo,
            file=obs.file,
            line=obs.line,
            language=obs.language,
            kind="sink",
            family="sql",
            detail=_with_scope({
                "symbol": d["symbol"],
                "receiver": d.get("receiver") or "",
                "execution": is_execution,
                "parameterised": d.get("parameterised", ""),
                "raw": d.get("raw", ""),
            }, obs),
            confidence="high" if is_execution else "medium",
        ))
    return out


def _payload_node(ep: FlowNode, sink: FlowNode, marker: FlowNode) -> FlowNode:
    """Build the raw-code-payload node for one (endpoint, sink, marker) triple."""
    return make_node(
        repo=ep.repo,
        file=ep.file,
        line=marker.line,
        language=ep.language,
        kind="source",
        family="raw-code-payload",
        detail=_with_scope({
            "field_line": marker.line,
            "endpoint_path": ep.detail.get("path"),
            "sink_symbol": sink.detail.get("symbol"),
            "sink_line": sink.line,
        }, ep),
        data_class="raw-sql-payload",
        confidence="high",
    )


def _distinct_triples(
    endpoints: list[FlowNode], sinks: list[FlowNode], markers: list[FlowNode]
) -> Iterator[tuple[FlowNode, FlowNode, FlowNode]]:
    """Yield each (endpoint, sink, marker) combination once.

    Deduplicated on the values that reach the finding — path, marker line, sink
    symbol — rather than on node identity, so two endpoints declaring the same
    path do not produce the same finding twice.
    """
    seen: set[tuple[str | None, int, str | None]] = set()
    for ep in endpoints:
        for sink in sinks:
            for marker in markers:
                key = (ep.detail.get("path"), marker.line, sink.detail.get("symbol"))
                if key in seen:
                    continue
                seen.add(key)
                yield ep, sink, marker


def _link_one_file(
    file_nodes: list[FlowNode],
) -> tuple[list[FlowNode], list[FlowEdge]]:
    """Correlate endpoints, SQL-field markers and execution sinks within one file."""
    endpoints = [n for n in file_nodes if n.family == "http-in"]
    markers = [n for n in file_nodes if n.family == "sql-field-marker"]
    sinks = [
        n for n in file_nodes
        if n.family == "sql" and n.kind == "sink" and n.detail.get("execution")
    ]
    if not (endpoints and markers and sinks):
        return [], []

    nodes: list[FlowNode] = []
    edges: list[FlowEdge] = []
    for ep, sink, marker in _distinct_triples(endpoints, sinks, markers):
        nodes.append(_payload_node(ep, sink, marker))
        edges.append(make_edge(
            ep, sink,
            kind="intra-file",
            evidence="endpoint accepts a raw SQL-shaped field executed in this file",
            confidence="medium",
        ))
    return nodes, edges


def link_raw_code_payloads(
    observations: list[FlowNode], sql_sinks: list[FlowNode]
) -> tuple[list[FlowNode], list[FlowEdge]]:
    """Tag an endpoint in a file that also holds a SQL-shaped field and an execution sink.

    Correlation is per *file*, which is all the evidence available: these three
    things appearing together is suggestive, not proof that the endpoint reaches
    the sink. `OI-17` is what would establish that.
    """
    by_file: dict[str, list[FlowNode]] = {}
    for n in [*observations, *sql_sinks]:
        by_file.setdefault(n.file, []).append(n)

    nodes: list[FlowNode] = []
    edges: list[FlowEdge] = []
    for file_nodes in by_file.values():
        file_new_nodes, file_new_edges = _link_one_file(file_nodes)
        nodes.extend(file_new_nodes)
        edges.extend(file_new_edges)
    return nodes, edges


def classify_entry_points(observations: list[FlowNode]) -> list[FlowNode]:
    """Emit one `entry-point` node per way untrusted input can enter this service.

    A *derivation*, so the definition of a front door can change without
    re-parsing the fleet — and it will change, because every framework is another
    mechanism (`OI-21`).

    Three sources, all already observed:

    * ``http-in`` — an HTTP endpoint, the only mechanism that used to count;
    * ``queue-sub`` with ``direction: consume`` — extracted since 1.x for the
      queue graph, and never treated as a way in, which is the case that would
      have made `OI-17` answer "no path" for a whole class of service;
    * ``entry-marker`` — gRPC, GraphQL, scheduled work, CLI and file watches.

    ``externally_triggered`` separates a door someone outside can open from one
    only the clock opens. A scheduled job is reachable, but carries no untrusted
    input by that route, and a reachability answer that cannot tell them apart
    will overstate what an attacker controls.
    """
    out: list[FlowNode] = []
    # A marker announces a *mechanism*, not a distinct door: a gRPC service
    # carries both `@GrpcService` and `extends ...ImplBase`, and one service is
    # one way in. HTTP and queue entries are keyed on their channel instead,
    # because a path and a topic really are distinct doors.
    seen_markers: set[tuple[str, str]] = set()
    for obs in observations:
        detail = _entry_detail(obs)
        if detail is None:
            continue
        if obs.family == "entry-marker":
            key = (obs.file, detail["mechanism"])
            if key in seen_markers:
                continue
            seen_markers.add(key)
        out.append(make_node(
            repo=obs.repo,
            file=obs.file,
            line=obs.line,
            language=obs.language,
            kind="source",
            family="entry-point",
            detail=_with_scope(detail, obs),
            confidence=obs.confidence,
        ))
    return out


def _with_scope(detail: dict[str, Any], obs: FlowNode) -> dict[str, Any]:
    """Carry the observation's enclosing method onto the node derived from it.

    Derivation also runs standalone over a stored record, where there is no
    extraction context to consult — so scope has to travel with the observation
    rather than being reassigned afterwards (`OI-17`).
    """
    for key in ("enclosing_class", "enclosing_method"):
        if obs.detail.get(key) is not None:
            detail[key] = obs.detail[key]
    return detail


def _entry_detail(obs: FlowNode) -> dict[str, Any] | None:
    """Return the entry-point detail for one observation, or None if it is not one."""
    if obs.family == "http-in":
        return {
            "mechanism": "http",
            "channel": obs.detail.get("path") or "?",
            "externally_triggered": True,
            "method": obs.detail.get("method"),
        }
    if obs.family == "queue-sub" and obs.detail.get("direction") == "consume":
        return {
            "mechanism": "queue",
            "channel": obs.detail.get("topic") or "?",
            "externally_triggered": True,
            "system": obs.detail.get("system"),
        }
    if obs.family == "entry-marker":
        return {
            "mechanism": obs.detail["mechanism"],
            "channel": obs.detail.get("raw") or "?",
            "externally_triggered": obs.detail["externally_triggered"],
        }
    return None


def derive_from_observations(
    observations: list[FlowNode],
) -> tuple[list[FlowNode], list[FlowEdge]]:
    """Derive every finding implied by a set of observations.

    Pure: the same observations always give the same findings, so re-running over
    a record that already carries derived nodes is safe — pass only the
    observations and replace, rather than appending to what is there.
    """
    sql_sinks = classify_sql(observations)
    payload_nodes, payload_edges = link_raw_code_payloads(observations, sql_sinks)
    entry_points = classify_entry_points(observations)
    # Resolution is a derivation for the same reason classification is: every
    # tier is a judgement about what a syntactic read is worth, and those get
    # revised. Here that costs a re-derive rather than a fleet rescan.
    resolved = call_edges(observations)
    # Step 4 runs last because it consumes what the steps above produce: the
    # entry points are its sources, the sinks its targets, and the resolved
    # calls the graph between them.
    derived = [*sql_sinks, *payload_nodes, *entry_points]
    tainted = classify_tainted_paths(observations, derived)
    return [*derived, *tainted], [*payload_edges, *resolved]

# probe


def classify_tainted_paths(
    observations: list[FlowNode], derived: list[FlowNode]
) -> list[FlowNode]:
    """Emit a `tainted-path` finding for each entry point that reaches a sink.

    The claim the tool exists to make, and the first one it has ever been able to
    state: not "this service has an endpoint and also a sink somewhere", but
    "a value from this door arrives at this sink, by these hops, and here is the
    weakest link in that chain".
    """
    paths, truncated = find_tainted_paths(observations, derived)
    out: list[FlowNode] = []
    for path, detail in zip(paths, path_details(paths), strict=True):
        if truncated:
            # Never silently. A caller weighing "no path exists" has to know the
            # search stopped early, because that is the expensive error.
            detail = {**detail, "search_truncated": True}
        out.append(make_node(
            repo=path.entry.repo,
            file=path.sink.file,
            line=path.sink.line,
            language=path.entry.language,
            kind="finding",
            family="tainted-path",
            detail=detail,
            confidence=path.confidence,
        ))
    return out
