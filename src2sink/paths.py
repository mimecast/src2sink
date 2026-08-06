"""Search for a path from an entry point to a sink (`OI-17` step 4).

Step 3 resolved calls, so there is a call graph. This walks it, and it is the
step that turns reachability into evidence.

Reachability alone would report every endpoint as reaching every sink its service
can touch — technically true and useless. What makes a path a finding is that a
value *travels* along it: the entry point's parameters are tainted, an argument
carrying a tainted name taints the callee's corresponding parameter, and a hop
that carries nothing is not walked. A decoy `safe(String x)` calling a static
query is pruned rather than ranked lower.

Three decisions here come from `docs/plans/observe-then-classify.md` and
deliberately override the older wording in the issue:

**Depth is unbounded** (§5). Measured on a 2,000-service fleet, capping at three
hops finds 25% of what depth eight finds — `A → B → C → D → sink` is the common
case, not the exception. And it does not become a hairball: at depth twelve an
entry point still reaches under 4% of the fleet.

**Path confidence is the minimum hop, never a product** (§6). Multiplying
destroys exactly the deep paths that hold most of the value — eight `medium`
hops multiply to 0.058 — but hops are not independent coin flips. A chain of
eight individually resolved calls, each with a declared receiver type, is not
less trustworthy than two fuzzy string matches. So: take the minimum, record the
length separately, and **name the weakest link**. A reader can act on "8 hops,
weakest link is the `B→C` binding"; nobody can act on `0.058`.

**There is no confidence floor** (§7). The issue's step 4 asked for "a floor
below which nothing is emitted" and that was retracted: for an indicator, a floor
converts cheap false positives into expensive, invisible false negatives. Emit
broadly, rank honestly, never suppress on confidence alone. Taint pruning is a
different thing — it drops hops with no *evidence*, not hops with low confidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .graph_common import confidence_rank
from .resolve import ResolvedCall, resolve_calls
from .schema import FlowNode

# Node families that end a path. A path is only interesting if it arrives
# somewhere that does something with the value.
SINK_FAMILIES = frozenset({"sql", "script-exec", "http-out", "queue-pub", "data-store"})

# Detail fields that may carry the sink's own text, in the order they are worth
# consulting. A sink states its evidence in whichever of these its family uses.
_SINK_TEXT_FIELDS = ("raw", "snippet", "symbol")

_IDENTIFIER_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A bound on how many paths are explored, not on how deep they go. Depth is
# where the findings are (§5), so capping depth would be capping the answer —
# but a pathological call graph can still branch exponentially, and an
# unbounded search would hang rather than answer. Truncation is *recorded*
# rather than silent: a result that quietly stopped looking reads as "nothing
# more to find", which is the one thing an exclusion claim must never do.
MAX_PATHS_EXPLORED = 20_000


@dataclass(frozen=True)
class PathHop:
    """One resolved call on a path, and the argument that carried the value."""

    from_class: str
    from_method: str
    to_class: str
    to_method: str
    tier: str
    confidence: str
    file: str
    line: int
    argument: str

    def describe(self) -> str:
        """A one-line account a reader can check against the source."""
        return (
            f"{self.from_class}.{self.from_method} -> {self.to_class}.{self.to_method} "
            f"[{self.tier}/{self.confidence}] {self.file}:{self.line} "
            f"carries {self.argument!r}"
        )


@dataclass(frozen=True)
class TaintedPath:
    """An entry point, a sink, and the hops by which a value reaches one from the other."""

    entry: FlowNode
    sink: FlowNode
    hops: tuple[PathHop, ...]
    tainted_at_sink: tuple[str, ...]

    @property
    def length(self) -> int:
        """Hop count, recorded separately from confidence rather than folded into it."""
        return len(self.hops)

    @property
    def confidence(self) -> str:
        """The weakest hop's confidence — a minimum, never a product (§6).

        A path with no hops is the entry point's own method reaching a sink
        directly, which is the strongest case there is: nothing was resolved, so
        nothing could have been resolved wrongly.
        """
        if not self.hops:
            return "high"
        return min(self.hops, key=lambda h: confidence_rank(h.confidence)).confidence

    @property
    def weakest_link(self) -> PathHop | None:
        """The hop a reader should check first, or None for a direct reach."""
        if not self.hops:
            return None
        return min(self.hops, key=lambda h: confidence_rank(h.confidence))


def _identifiers(text: str) -> set[str]:
    """The identifiers appearing in an expression.

    Whole tokens rather than substrings: `filterChain` contains `filter` and is a
    different variable, so a substring test would carry taint into code that
    never received it.
    """
    return set(_IDENTIFIER_RX.findall(text or ""))


def _carries_taint(text: str, tainted: frozenset[str]) -> str | None:
    """The tainted name an expression mentions, or None if it mentions none."""
    hit = _identifiers(text) & tainted
    return sorted(hit)[0] if hit else None


def _scope_of(node: FlowNode) -> tuple[str, str]:
    """The (class, method) a node sits in, as a lookup key."""
    return (
        str(node.detail.get("enclosing_class") or ""),
        str(node.detail.get("enclosing_method") or ""),
    )


def _sink_text(sink: FlowNode) -> str:
    """Whatever text a sink states its own evidence in."""
    return " ".join(
        str(sink.detail.get(f) or "") for f in _SINK_TEXT_FIELDS
    )


@dataclass
class _Graph:
    """The call graph of one repo, indexed the way the search walks it."""

    declarations: dict[tuple[str, str], FlowNode]
    calls_from: dict[tuple[str, str], list[ResolvedCall]]
    sinks_in: dict[tuple[str, str], list[FlowNode]]


def _build_graph(observations: list[FlowNode], derived: list[FlowNode]) -> _Graph:
    """Index declarations, resolved calls and sinks by the method they sit in."""
    declarations: dict[tuple[str, str], FlowNode] = {}
    for obs in observations:
        if obs.family == "method-decl":
            key = (
                str(obs.detail.get("class") or ""),
                str(obs.detail.get("method") or ""),
            )
            declarations.setdefault(key, obs)

    calls_from: dict[tuple[str, str], list[ResolvedCall]] = {}
    for resolved in resolve_calls(observations):
        calls_from.setdefault(_scope_of(resolved.call), []).append(resolved)

    sinks_in: dict[tuple[str, str], list[FlowNode]] = {}
    for node in [*observations, *derived]:
        if node.family in SINK_FAMILIES and node.kind == "sink":
            sinks_in.setdefault(_scope_of(node), []).append(node)

    return _Graph(declarations=declarations, calls_from=calls_from, sinks_in=sinks_in)


def _target_scope(resolved: ResolvedCall) -> tuple[str, str]:
    """The (class, method) a resolved call arrives at."""
    return (
        str(resolved.target.detail.get("class") or ""),
        str(resolved.target.detail.get("method") or ""),
    )


def _tainted_params(resolved: ResolvedCall, tainted: frozenset[str]) -> tuple[frozenset[str], str]:
    """Which of the callee's parameters this call taints, and the argument that did it.

    Binding is positional: argument *i* binds parameter *i*. That is as far as a
    syntactic read goes, and it is the common shape — named and defaulted
    arguments are not modelled, so a call using them under-taints rather than
    over-taints.
    """
    arguments = resolved.call.detail.get("arguments") or []
    params = resolved.target.detail.get("params") or []
    carried: set[str] = set()
    evidence = ""
    for index, argument in enumerate(arguments):
        hit = _carries_taint(str(argument), tainted)
        if hit is None:
            continue
        evidence = evidence or str(argument)
        if index < len(params):
            carried.add(str(params[index]))
    return frozenset(carried), evidence


def _hop(resolved: ResolvedCall, scope: tuple[str, str], argument: str) -> PathHop:
    """Record one traversed call as a checkable line of evidence."""
    to_class, to_method = _target_scope(resolved)
    return PathHop(
        from_class=scope[0],
        from_method=scope[1],
        to_class=to_class,
        to_method=to_method,
        tier=resolved.tier,
        confidence=resolved.confidence,
        file=resolved.call.file,
        line=resolved.call.line,
        argument=argument,
    )


class _Search:
    """One depth-first walk from an entry point, carrying its own budget."""

    def __init__(self, graph: _Graph, entry: FlowNode) -> None:
        """Prepare a search rooted at ``entry``."""
        self.graph = graph
        self.entry = entry
        self.found: list[TaintedPath] = []
        self.explored = 0
        self.truncated = False

    def run(self, scope: tuple[str, str], tainted: frozenset[str]) -> None:
        """Walk from ``scope`` with ``tainted`` names live."""
        self._walk(scope, tainted, hops=(), on_path=frozenset({scope}))

    def _walk(
        self,
        scope: tuple[str, str],
        tainted: frozenset[str],
        hops: tuple[PathHop, ...],
        on_path: frozenset[tuple[str, str]],
    ) -> None:
        self.explored += 1
        if self.explored > MAX_PATHS_EXPLORED:
            self.truncated = True
            return

        for sink in self.graph.sinks_in.get(scope, []):
            hit = _carries_taint(_sink_text(sink), tainted)
            if hit is not None:
                self.found.append(TaintedPath(
                    entry=self.entry, sink=sink, hops=hops,
                    tainted_at_sink=(hit,),
                ))

        for resolved in self.graph.calls_from.get(scope, []):
            carried, argument = _tainted_params(resolved, tainted)
            if not argument:
                # The hop carries nothing. Pruned rather than ranked lower —
                # this is the difference between a finding and a list of
                # everything the service can touch.
                continue
            target = _target_scope(resolved)
            if target in on_path:
                # A cycle. The call graph is allowed to contain one; a path
                # through it twice is the same path, so stop rather than loop.
                continue
            self._walk(
                target,
                carried,
                hops=(*hops, _hop(resolved, scope, argument)),
                on_path=on_path | {target},
            )


def find_tainted_paths(
    observations: list[FlowNode],
    derived: list[FlowNode],
) -> tuple[list[TaintedPath], bool]:
    """Every path from an entry point to a sink that a value travels along.

    Returns the paths and whether the search was truncated. Truncation is
    returned rather than swallowed because a caller reporting "no path" has to
    be able to tell "we looked everywhere" from "we stopped looking".
    """
    graph = _build_graph(observations, derived)
    paths: list[TaintedPath] = []
    truncated = False

    for entry in derived:
        if entry.family != "entry-point":
            continue
        scope = _scope_of(entry)
        declaration = graph.declarations.get(scope)
        if declaration is None:
            continue
        tainted = frozenset(str(p) for p in (declaration.detail.get("params") or []))
        if not tainted:
            # Nothing enters by this door that a parameter names. A scheduled
            # job is the usual case, and `OI-21` already records that it is not
            # externally triggered.
            continue
        search = _Search(graph, entry)
        search.run(scope, tainted)
        paths.extend(search.found)
        truncated = truncated or search.truncated

    return _dedupe(paths), truncated


def _dedupe(paths: list[TaintedPath]) -> list[TaintedPath]:
    """Keep the shortest, then strongest, path to each (entry, sink) pair.

    Several routes can reach the same sink from the same door. Reporting each
    would inflate one finding into many, and the shortest is the one a reader
    should check first.
    """
    best: dict[tuple[str, str], TaintedPath] = {}
    for path in paths:
        key = (path.entry.id, path.sink.id)
        current = best.get(key)
        if current is None or (
            (path.length, -confidence_rank(path.confidence))
            < (current.length, -confidence_rank(current.confidence))
        ):
            best[key] = path
    return sorted(best.values(), key=lambda p: (p.entry.id, p.sink.id))


def path_details(paths: list[TaintedPath]) -> Iterator[dict[str, Any]]:
    """Render each path as the detail of a finding, evidence included."""
    for path in paths:
        weakest = path.weakest_link
        yield {
            "entry_mechanism": path.entry.detail.get("mechanism"),
            "entry_channel": path.entry.detail.get("channel"),
            "externally_triggered": path.entry.detail.get("externally_triggered"),
            "entry_class": path.entry.detail.get("enclosing_class"),
            "entry_method": path.entry.detail.get("enclosing_method"),
            "sink_family": path.sink.family,
            "sink_symbol": path.sink.detail.get("symbol"),
            "sink_file": path.sink.file,
            "sink_line": path.sink.line,
            "tainted_at_sink": list(path.tainted_at_sink),
            # Length is recorded beside confidence, not folded into it (§6).
            "hops": path.length,
            # The single thing a reader should check first.
            "weakest_link": weakest.describe() if weakest else None,
            "path": [hop.describe() for hop in path.hops],
        }
