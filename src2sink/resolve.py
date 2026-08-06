"""Resolve a call to the method it invokes, within one repo (`OI-17` step 3).

Steps 1 and 2 recorded the facts: every node knows its enclosing method, every
declaration is recorded with its parameters and span, and every type states its
field types, supertypes and whether it is an interface. Step 3 joins them —
`stockService.process(...)` becomes an edge to a specific `method-decl`.

This is a **derivation**. It reads observations and never the source, so
changing a resolution rule costs a re-derive over records rather than a fleet
rescan. That property is the whole point of
`docs/plans/observe-then-classify.md`, and resolution is exactly the kind of rule
that will be revised: every tier below is a judgement about how much a syntactic
read is worth.

Three tiers, and the tier is recorded on every edge because they are not
interchangeable evidence:

* **T1** — the receiver is a field whose declared type names a class in this
  repo. `private final StockService stockService` says what the call binds to,
  with no compiler and no inference. `high`.
* **T2** — that declared type is an *interface*, so the method it names has no
  body. Expanded to the classes implementing it. `medium` for a single
  implementation, and explicitly ambiguous — never confident — for more.
* **T3** — nothing types the receiver, but the method name is declared exactly
  once in the repo. `low`. Declared more than once, it is dropped: a guess
  between several candidates is not evidence.

**T2 is why the answer can never be "unreachable".** Constructor-injected
interface fields are the standard Spring shape, so a resolver that stopped at the
declared type would report a dead end for the majority of the fleet — and a
confident dead end is worse than no answer, because it reads as a clean result.

Not attempted: parameters and locals as T1 sources. `method-decl` records
parameter *names* and not their types, so `void f(StockService s) { s.process() }`
falls to T3. That is a real gap rather than a decision — recording parameter
types would cost another `DETECTION_VERSION` bump, and it is worth measuring how
often the shape occurs before paying for it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .extractors.node_factory import make_edge
from .schema import FlowEdge, FlowNode

# Receiver prefixes that name the enclosing instance rather than a collaborator.
# `this.dao.find()` and `self.dao.find()` are the same call as `dao.find()`.
_SELF_PREFIXES = ("this.", "self.")

_TIER_CONFIDENCE = {
    "T1": "high",
    "T2": "medium",
    "T3": "low",
}


@dataclass(frozen=True)
class ResolvedCall:
    """One call site bound to one declaration, and the reasoning that bound it."""

    call: FlowNode
    target: FlowNode
    tier: str
    evidence: str
    ambiguous: bool = False

    @property
    def confidence(self) -> str:
        """Tier confidence, degraded when the target was one of several candidates."""
        # An ambiguous T2 must never read as a confident answer: the resolver
        # found N implementations and cannot say which one runs.
        return "low" if self.ambiguous else _TIER_CONFIDENCE.get(self.tier, "low")


@dataclass
class SymbolTable:
    """What one repo declares, indexed the way resolution asks about it."""

    fields: dict[str, dict[str, str]] = field(default_factory=dict)
    is_interface: dict[str, bool] = field(default_factory=dict)
    implementations: dict[str, list[str]] = field(default_factory=dict)
    methods: dict[tuple[str, str], FlowNode] = field(default_factory=dict)
    by_name: dict[str, list[FlowNode]] = field(default_factory=lambda: defaultdict(list))

    def declared_type_of(self, owner: str, receiver: str) -> str | None:
        """The declared type of ``receiver`` as a field of ``owner``, if any."""
        return self.fields.get(owner, {}).get(receiver)

    def method_on(self, class_name: str, method: str) -> FlowNode | None:
        """The declaration of ``method`` on ``class_name``, if it declares one."""
        return self.methods.get((class_name, method))


def _normalise_receiver(receiver: str | None) -> str | None:
    """Strip a self-reference prefix so `this.dao` reads as the field `dao`."""
    if not receiver:
        return None
    for prefix in _SELF_PREFIXES:
        if receiver.startswith(prefix):
            receiver = receiver[len(prefix):]
    # Anything still qualified — `a.b.c` — is a chain this pass cannot follow,
    # and guessing at its last segment would invent a receiver nobody declared.
    return receiver if receiver and "." not in receiver else None


def _index_type(table: SymbolTable, detail: dict[str, Any]) -> None:
    """Record one type's fields, interface-ness, and what it implements."""
    name = detail.get("class")
    if not name:
        return
    table.fields[name] = dict(detail.get("fields") or {})
    table.is_interface[name] = bool(detail.get("is_interface"))
    for supertype in detail.get("supertypes") or []:
        table.implementations.setdefault(supertype, []).append(name)


def _index_method(table: SymbolTable, obs: FlowNode, detail: dict[str, Any]) -> None:
    """Record one method declaration, by owner and by bare name."""
    owner, method = detail.get("class"), detail.get("method")
    if not method:
        return
    if owner:
        # First declaration wins. Overloads share a name and this pass has no
        # signature to tell them apart, so binding to the first is as good an
        # answer as it can honestly give.
        table.methods.setdefault((owner, method), obs)
    table.by_name[method].append(obs)


def build_symbol_table(observations: list[FlowNode]) -> SymbolTable:
    """Index the `type-decl` and `method-decl` observations of one repo."""
    table = SymbolTable()
    for obs in observations:
        if obs.family == "type-decl":
            _index_type(table, obs.detail)
        elif obs.family == "method-decl":
            _index_method(table, obs, obs.detail)
    return table


def _resolve_via_declared_type(
    call: FlowNode, table: SymbolTable, owner: str, receiver: str, symbol: str,
) -> list[ResolvedCall]:
    """T1, and T2 when the declared type turns out to be an interface."""
    declared = table.declared_type_of(owner, receiver)
    if not declared:
        return []

    if not table.is_interface.get(declared, False):
        target = table.method_on(declared, symbol)
        if target is None:
            return []
        return [ResolvedCall(
            call=call, target=target, tier="T1",
            evidence=f"{receiver} declared {declared} on {owner}",
        )]

    # The declared type is an interface, so its own method has no body. The call
    # runs an implementation, and which one is a runtime fact — so every
    # candidate is reported and more than one is marked ambiguous rather than
    # picked between.
    impls = [
        impl for impl in sorted(table.implementations.get(declared, []))
        if table.method_on(impl, symbol) is not None
    ]
    if not impls:
        return []
    ambiguous = len(impls) > 1
    return [
        ResolvedCall(
            call=call,
            target=table.method_on(impl, symbol),  # type: ignore[arg-type]
            tier="T2",
            evidence=(
                f"{receiver} declared {declared} (interface) on {owner}; "
                + (f"{len(impls)} implementations: {', '.join(impls)}"
                   if ambiguous else f"implemented by {impl}")
            ),
            ambiguous=ambiguous,
        )
        for impl in impls
    ]


def _resolve_via_unique_name(
    call: FlowNode, table: SymbolTable, symbol: str,
) -> list[ResolvedCall]:
    """T3 — the name is declared exactly once in the repo, so it can only mean that."""
    candidates = table.by_name.get(symbol) or []
    if len(candidates) != 1:
        return []
    target = candidates[0]
    return [ResolvedCall(
        call=call, target=target, tier="T3",
        evidence=f"{symbol} declared once in repo, on {target.detail.get('class') or '(module)'}",
    )]


def resolve_call(call: FlowNode, table: SymbolTable) -> list[ResolvedCall]:
    """Resolve one call site to the declarations it may invoke, best tier first."""
    detail = call.detail
    symbol = detail.get("symbol")
    if not symbol:
        return []
    owner = detail.get("enclosing_class")
    receiver = _normalise_receiver(detail.get("receiver"))

    if owner and receiver:
        resolved = _resolve_via_declared_type(call, table, owner, receiver, symbol)
        if resolved:
            return resolved
    return _resolve_via_unique_name(call, table, symbol)


def resolve_calls(observations: list[FlowNode]) -> list[ResolvedCall]:
    """Resolve every call observation in one repo against what the repo declares."""
    table = build_symbol_table(observations)
    out: list[ResolvedCall] = []
    for obs in observations:
        if obs.family != "call-site":
            continue
        for resolved in resolve_call(obs, table):
            # A method that calls itself is a real edge; a call resolving to the
            # declaration it sits inside is not — it is the same node twice, and
            # a traversal would loop on it without ever leaving.
            if resolved.target.id != obs.id:
                out.append(resolved)
    return out


def call_edges(observations: list[FlowNode]) -> list[FlowEdge]:
    """The resolved calls of one repo as `intra-repo` edges.

    `FlowEdge` has advertised an `intra-repo` kind since the schema was written
    and nothing emitted one — cross-repo relationships lived in a separate
    `CallEdge` type and within-repo links were never made at all. These are the
    first.
    """
    return [
        make_edge(
            r.call, r.target,
            kind="intra-repo",
            evidence=f"[{r.tier}] {r.evidence}",
            confidence=r.confidence,
        )
        for r in resolve_calls(observations)
    ]
