#!/usr/bin/env python3
"""Endpoint-anchored bidirectional flow trace (Phase 2).

Examples:
  uv run src2sink-trace --metabase-root metabase \\
    --target acme/sql-runner-api

  uv run src2sink-trace --metabase-root metabase \\
    --target acme/sql-runner-api --path /query --scan-repos repos
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .aggregators.payload_producers import build_producer_indices
from .aggregators.service_calls import CallEdge, collect_service_edges
from .aggregators.traces_index import write_traces_index
from .graph_common import (
    confidence_rank,
    extract_urls_and_paths,
    host_matches_repo,
    iter_nodes,
    load_one_v2_repo_record,
    load_v2_repo_records,
    normalize_path_template,
    path_filter_matches,
    repo_id,
    repo_name_aliases,
    store_key_from_node,
    v2_record_paths,
)
from .index_store import FleetIndex, OutboundNode, open_index, outbound_nodes_of
from .internal_groups import (
    add_internal_groups_arguments,
    apply_internal_groups_from_args,
)
from .known_api_clients import ApiClientConfigError, configure_from_path, get_bindings
from .renderers.markdown import md_table
from .sanitize import UNTRUSTED_CONTENT_NOTICE, redact_literals


@dataclass
class UpstreamHit:
    """One repo found calling the trace target, and why we believe it does.

    ``kind`` names the evidence path (graph edge, raw source literal, producer
    index) and ``ref`` locates it, so a reader can check the claim rather than
    take it.
    """

    source_repo: str
    kind: str
    confidence: str
    evidence: str
    ref: str = ""


@dataclass
class TraceReport:
    """Everything a trace found for one target service, ready to render.

    Facts about the target itself (inbound endpoints, raw payloads, SQL sinks,
    data stores) plus the upstream callers that reach it.
    """

    target_repo: str
    path_filter: str | None
    inbound: list[dict[str, Any]] = field(default_factory=list)
    raw_payloads: list[dict[str, Any]] = field(default_factory=list)
    sql_sinks: list[dict[str, Any]] = field(default_factory=list)
    stores: list[dict[str, Any]] = field(default_factory=list)
    upstream: list[UpstreamHit] = field(default_factory=list)


def _target_record(
    records: list[dict[str, Any]],
    target: str,
) -> dict[str, Any] | None:
    """Find the v2 repo record matching a target repo id or name."""
    for data in records:
        if repo_id(data) == target or data["name"] == target:
            return data
    return None


def _path_matches(candidate: str, path_filter: str | None, want: str | None) -> bool:
    """True if a candidate path satisfies the (optional) path filter.

    Uses the *filter* predicate, not the routing one: `--path /v1` means "show me
    everything under /v1", which `path_templates_match` deliberately no longer
    answers (OI-1 / finding F2).
    """
    if not want:
        return True
    if path_filter_matches(candidate, path_filter):
        return True
    return normalize_path_template(candidate) == want


def _collect_endpoint_fact(
    report: TraceReport, node: dict[str, Any], detail: dict[str, Any],
    path_filter: str | None, want: str | None,
) -> None:
    """Append an inbound-endpoint or raw-payload fact to the report if this node matches."""
    family = node.get("family", "")
    if family == "http-in" and _path_matches(detail.get("path", ""), path_filter, want):
        report.inbound.append({
            "path": detail.get("path", ""),
            "method": detail.get("method"),
            "file": node.get("file"),
            "line": node.get("line"),
            "framework": node.get("framework"),
        })
    elif family == "raw-code-payload" and _path_matches(
        detail.get("endpoint_path", ""), path_filter, want
    ):
        report.raw_payloads.append({
            "endpoint": detail.get("endpoint_path", ""),
            "field_line": detail.get("field_line"),
            "sink_symbol": detail.get("sink_symbol"),
            "sink_line": detail.get("sink_line"),
            "file": node.get("file"),
        })


def _collect_sink_fact(
    report: TraceReport, node: dict[str, Any], detail: dict[str, Any]
) -> None:
    """Append a SQL-sink or data-store fact to the report if this node matches."""
    family = node.get("family", "")
    kind = node.get("kind")
    if family == "sql" and kind == "sink" and detail.get("execution", True):
        report.sql_sinks.append({
            "symbol": detail.get("symbol"),
            "file": node.get("file"),
            "line": node.get("line"),
            "raw": (detail.get("raw") or "")[:120],
        })
    elif family == "data-store" and kind == "store":
        key = store_key_from_node(node)
        if key:
            report.stores.append({
                "store_key": key,
                "file": node.get("file"),
                "line": node.get("line"),
            })


def _collect_target_facts(
    data: dict[str, Any],
    path_filter: str | None,
) -> TraceReport:
    """Build a TraceReport of endpoint/sink/store facts for the target repo's own nodes."""
    report = TraceReport(target_repo=repo_id(data), path_filter=path_filter)
    want = normalize_path_template(path_filter) if path_filter else None
    for node in iter_nodes(data):
        detail = node.get("detail") or {}
        _collect_endpoint_fact(report, node, detail, path_filter, want)
        _collect_sink_fact(report, node, detail)
    return report


def _find_upstream_from_graph(
    edges: list[CallEdge],
    target: str,
    path_filter: str | None,
) -> list[UpstreamHit]:
    """Filter the fleet service-call graph down to edges arriving at ``target``.

    Takes the edge list rather than the records it was built from: the graph is
    fleet-wide and target-independent, so building it here made a batch of N
    traces rebuild the whole thing N times (OI-14).
    """
    hits: list[UpstreamHit] = []
    want = normalize_path_template(path_filter) if path_filter else None

    for edge in edges:
        if edge.target_repo != target:
            continue
        if want and edge.target_path != "*":
            if not path_filter_matches(edge.target_path, path_filter):
                if normalize_path_template(edge.target_path) != want:
                    continue
        hits.append(UpstreamHit(
            source_repo=edge.source_repo,
            kind="http-out-graph",
            confidence=edge.confidence,
            evidence=edge.evidence,
            ref=", ".join(edge.refs),
        ))
    return hits


def _path_hits_target(paths: list[str], path_filter: str | None, path_terms: set[str]) -> bool:
    """True if any extracted path matches the path filter or a known target path term."""
    for p in paths:
        if path_filter:
            if path_filter_matches(p, path_filter) or p in path_terms:
                return True
        elif path_terms and any(
            p.rstrip("/").endswith(pt.split("{")[0].rstrip("/")) for pt in path_terms
        ):
            return True
    return False


def _raw_references_target(
    raw: str, target: str, path_filter: str | None, path_terms: set[str], aliases: set[str]
) -> bool:
    """True if an http-out raw literal appears to call ``target``."""
    hosts, paths = extract_urls_and_paths(raw)
    if _path_hits_target(paths, path_filter, path_terms):
        return True
    if any(host_matches_repo(h, target) for h in hosts):
        return True
    return any(alias in raw.lower() for alias in aliases)


def _target_match_terms(
    target: str,
    path_filter: str | None,
) -> tuple[set[str], set[str]]:
    """The path terms and name aliases that identify ``target`` in a raw literal.

    Computed once per trace rather than per node: over a fleet this loop runs
    for every outbound call site in every repo.
    """
    _, name = target.split("/", 1)
    aliases = repo_name_aliases(name)
    binding = next((b for b in get_bindings() if b.target_repo == target), None)
    path_terms: set[str] = set(binding.paths) if binding else set()
    if path_filter:
        path_terms.add(path_filter)
        path_terms.add(normalize_path_template(path_filter))
    return path_terms, aliases


def _hit_for_outbound_node(
    node: OutboundNode,
    target: str,
    path_filter: str | None,
    path_terms: set[str],
    aliases: set[str],
) -> UpstreamHit | None:
    """Classify one outbound node as evidence of a call to ``target``, or not."""
    ref = f"{node.file}:{node.line}"
    # A binding-resolved node names its target outright: it is the one kind of
    # caller whose source contains no literal to scan for.
    if node.target_repo == target:
        return UpstreamHit(
            source_repo=node.source_repo,
            kind="api-client-binding",
            confidence="high",
            evidence=str(
                node.target_repo_evidence
                or node.import_name
                or node.client
                or "api-client binding",
            )[:140],
            ref=ref,
        )
    if node.raw and _raw_references_target(
        node.raw, target, path_filter, path_terms, aliases
    ):
        return UpstreamHit(
            source_repo=node.source_repo,
            kind="http-out-raw",
            confidence="medium",
            evidence=node.raw[:140],
            ref=ref,
        )
    return None


def _upstream_from_outbound_nodes(
    nodes: Iterable[OutboundNode],
    target: str,
    path_filter: str | None,
) -> list[UpstreamHit]:
    """Find upstream callers among outbound nodes referencing the target.

    Takes flattened nodes rather than records so the same implementation serves
    both sources: a live scan over loaded records, and a streamed query against
    the persisted index (`OI-15`). One body means the index cannot answer
    differently from the code that built it — the drift the issue's "identical
    edges" test exists to rule out.
    """
    path_terms, aliases = _target_match_terms(target, path_filter)
    hits: list[UpstreamHit] = []
    for node in nodes:
        if node.source_repo == target:
            continue
        hit = _hit_for_outbound_node(node, target, path_filter, path_terms, aliases)
        if hit is not None:
            hits.append(hit)
    return hits


def _find_upstream_from_nodes(
    records: list[dict[str, Any]],
    target: str,
    path_filter: str | None,
) -> list[UpstreamHit]:
    """Find upstream callers by scanning http-out nodes across all repos for raw literals referencing the target."""
    return _upstream_from_outbound_nodes(
        (
            node
            for data in records
            for node in outbound_nodes_of(data, repo_id(data))
        ),
        target,
        path_filter,
    )


_SCAN_SUFFIXES = frozenset(
    {".java", ".kt", ".py", ".js", ".ts", ".go", ".yaml", ".yml", ".properties"}
)
_SCAN_SKIP_PARTS = frozenset({"node_modules", ".git", "target", "build"})


def _scan_needles(
    target: str, path_filter: str | None
) -> tuple[list[str], re.Pattern[str] | None]:
    """Build the literal-scan needle list and a compiled quoted-string regex."""
    _, name = target.split("/", 1)
    binding = next((b for b in get_bindings() if b.target_repo == target), None)
    needles: list[str] = [name]
    if binding:
        needles.extend(binding.service_aliases)
        needles.extend(p for p in binding.paths if not p.startswith("{"))
    if path_filter:
        needles.append(path_filter)
    if not needles:
        return needles, None
    alias_parts = "|".join(re.escape(n) for n in needles)
    # Bound the two quote-free runs (SAST finding 6): open-ended [^"\']* on both
    # sides of the alias is a backtracking smell. A 512-char window each side more
    # than covers a real quoted literal (evidence is truncated to 160 anyway) and
    # keeps the match cost linear regardless of input.
    scan_rx = re.compile(
        rf'["\']([^"\']{{0,512}}(?:{alias_parts})[^"\']{{0,512}})["\']', re.IGNORECASE
    )
    return needles, scan_rx


def _iter_consumer_files(
    repos_root: Path, target_dir: Path
) -> Iterator[tuple[str, Path, Path]]:
    """Yield (src_id, sub_dir, file) for scannable files in non-target repos."""
    for repo_dir in repos_root.iterdir():
        if not repo_dir.is_dir():
            continue
        for sub in repo_dir.iterdir():
            if not sub.is_dir() or sub == target_dir:
                continue
            src_id = f"{repo_dir.name}/{sub.name}"
            for path in sub.rglob("*"):
                if path.suffix.lower() not in _SCAN_SUFFIXES:
                    continue
                if any(p in path.parts for p in _SCAN_SKIP_PARTS):
                    continue
                yield src_id, sub, path


def _literal_hits_in_file(
    src_id: str,
    sub: Path,
    path: Path,
    needles: list[str],
    scan_rx: re.Pattern[str] | None,
) -> list[UpstreamHit]:
    """Scan untrusted source text of one file for quoted-string literals referencing the target.

    Matches are only recorded, never evaluated.
    """
    try:
        if path.stat().st_size > 512_000:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[UpstreamHit] = []
    for needle in needles:
        if needle.lower() not in text.lower():
            continue
        if scan_rx:
            for m in scan_rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(UpstreamHit(
                    source_repo=src_id,
                    kind="source-literal",
                    confidence="medium",
                    # Redact PII literals: this evidence is a raw quoted string read
                    # straight off an untrusted source file (SAST finding 2), so —
                    # unlike detail["raw"] above — it never passed through the
                    # build-time redaction in summary_to_dict.
                    evidence=redact_literals(m.group(1))[:160],
                    ref=f"{path.relative_to(sub)}:{line}",
                ))
        break
    return hits


def _scan_repos_for_literals(
    repos_root: Path,
    target: str,
    path_filter: str | None,
) -> list[UpstreamHit]:
    """Scan cloned consumer repos on disk for source literals that reference the target repo."""
    if not repos_root.is_dir():
        return []
    needles, scan_rx = _scan_needles(target, path_filter)
    group, repo_name = target.split("/", 1)
    target_dir = repos_root / group / repo_name
    if not target_dir.is_dir():
        return []
    hits: list[UpstreamHit] = []
    for src_id, sub, path in _iter_consumer_files(repos_root, target_dir):
        hits.extend(_literal_hits_in_file(src_id, sub, path, needles, scan_rx))
    return hits


def _resolve_fleet_derivations(
    metabase_root: Path,
    repos_root: Path | None,
    records: list[dict[str, Any]],
    service_edges: list[CallEdge] | None,
    producer_indices: list[Any] | None,
) -> tuple[list[CallEdge], list[Any]]:
    """Build whichever fleet-wide derivations a caller did not supply.

    Both describe the fleet, not the trace target, so a caller tracing more than
    one target builds them once and passes them down. Computing them per target
    is what made batch tracing quadratic in fleet size (OI-14) — the
    service-call graph especially, which dominates the cost of a trace.

    Called only after the target is known to exist, so an unknown target fails
    without paying for either build.
    """
    if service_edges is None:
        service_edges, _unmatched = collect_service_edges(records)
    if producer_indices is None:
        producer_indices = build_producer_indices(metabase_root, repos_root=repos_root)
    return service_edges, producer_indices


def _assemble_upstream(
    report: TraceReport,
    graph_hits: Iterable[UpstreamHit],
    node_hits: Iterable[UpstreamHit],
    producer_hits: Iterable[UpstreamHit],
    *,
    repos_root: Path | None,
    scan_repos: bool,
    path_filter: str | None,
) -> None:
    """Merge caller hits from every source into ``report``, first source winning.

    Precedence is the point, and it is why this is one function rather than
    inlined at each call site: the graph edge is the most specific claim, a raw
    literal the least, and the indexed and loading paths must resolve a conflict
    the same way or the two would disagree about the same fleet.

    Within a source, the *strongest* evidence wins rather than the last seen
    (`OI-29`). `collect_service_edges` emits several edges for one caller — one
    per route it might be addressing — so "last wins" reported whichever the
    collector happened to yield last, and a `high` edge was routinely overwritten
    by a `low` one for the same caller. This matches what `payload_producers`
    already does when merging its own hits.
    """
    upstream: dict[tuple[str, str], UpstreamHit] = {}

    def offer(key: tuple[str, str], hit: UpstreamHit) -> None:
        """Keep ``hit`` only if nothing stronger is already recorded for ``key``."""
        prev = upstream.get(key)
        if prev is None or confidence_rank(hit.confidence) > confidence_rank(prev.confidence):
            upstream[key] = hit

    for hit in graph_hits:
        offer((hit.source_repo, hit.kind), hit)
    for hit in node_hits:
        offer((hit.source_repo, hit.kind), hit)

    if scan_repos and repos_root:
        for hit in _scan_repos_for_literals(repos_root, report.target_repo, path_filter):
            offer((hit.source_repo, hit.kind), hit)

    for hit in producer_hits:
        offer((hit.source_repo, f"producer-index:{hit.kind}"), hit)

    report.upstream = sorted(
        upstream.values(),
        key=lambda h: (h.confidence, h.source_repo),
    )


def _producer_hits_from_indices(
    indices: list[Any],
    target_repo: str,
    path_filter: str | None,
) -> Iterator[UpstreamHit]:
    """Yield producer-index hits for one target, in the loading path's shape."""
    for index in indices:
        if index.binding.target_repo != target_repo:
            continue
        for phit in index.hits:
            if _producer_path_matches(phit.path, path_filter):
                yield UpstreamHit(
                    source_repo=phit.source_repo,
                    kind=phit.kind,
                    confidence=phit.confidence,
                    evidence=phit.evidence,
                    ref=phit.ref,
                )


def _producer_path_matches(path: str, path_filter: str | None) -> bool:
    """Whether a producer hit's path survives ``path_filter``."""
    if not path_filter or path in ("*", ""):
        return True
    if path_filter in path:
        return True
    return path_filter_matches(path, path_filter)


def _trace_from_index(
    index: FleetIndex,
    target: str,
    *,
    path_filter: str | None,
    repos_root: Path | None,
    scan_repos: bool,
) -> TraceReport:
    """Answer a trace from the persisted index, holding no fleet-wide structure.

    The whole of `OI-15` in one function: one record read, three keyed queries,
    and every result streamed. Peak memory is a function of what arrives at the
    target, not of how large the fleet is.
    """
    record_path = index.record_path(target)
    data = load_one_v2_repo_record(record_path) if record_path else None
    if data is None:
        raise SystemExit(f"Target repo not found in v2 metabase: {target}")

    report = _collect_target_facts(data, path_filter)
    edges = [
        CallEdge(
            source_repo=source_repo,
            target_repo=target,
            target_path=target_path,
            confidence=confidence,
            evidence=evidence,
            refs=refs,
        )
        for source_repo, target_path, confidence, evidence, refs
        in index.call_edges_into(target)
    ]
    _assemble_upstream(
        report,
        _find_upstream_from_graph(edges, report.target_repo, path_filter),
        _upstream_from_outbound_nodes(
            index.outbound_nodes(exclude_repo=target), report.target_repo, path_filter,
        ),
        (
            UpstreamHit(
                source_repo=row.source_repo,
                kind=row.kind,
                confidence=row.confidence,
                evidence=row.evidence,
                ref=row.ref,
            )
            for row in index.producer_hits_for(target)
            if _producer_path_matches(row.path, path_filter)
        ),
        repos_root=repos_root,
        scan_repos=scan_repos,
        path_filter=path_filter,
    )
    return report


def run_trace(
    metabase_root: Path,
    target: str,
    *,
    path_filter: str | None = None,
    repos_root: Path | None = None,
    scan_repos: bool = False,
    records: list[dict[str, Any]] | None = None,
    producer_indices: list[Any] | None = None,
    service_edges: list[CallEdge] | None = None,
    use_index: bool = True,
) -> TraceReport:
    """Run a full endpoint-anchored trace: target facts plus upstream callers from all sources.

    ``records``, ``producer_indices`` and ``service_edges`` are fleet-wide and
    independent of ``target``; see :func:`_resolve_fleet_derivations`.

    With none of them supplied and a fresh index present, the trace is answered
    from the index and the fleet is never loaded (`OI-15`). A caller that passes
    its own fleet data has already paid for it, so it is used as given.
    """
    if use_index and records is None and producer_indices is None and service_edges is None:
        index = open_index(metabase_root, v2_record_paths(metabase_root))
        if index is not None:
            with index:
                return _trace_from_index(
                    index, target,
                    path_filter=path_filter,
                    repos_root=repos_root,
                    scan_repos=scan_repos,
                )

    records = load_v2_repo_records(metabase_root) if records is None else records
    data = _target_record(records, target)
    if data is None:
        raise SystemExit(f"Target repo not found in v2 metabase: {target}")

    service_edges, indices = _resolve_fleet_derivations(
        metabase_root, repos_root, records, service_edges, producer_indices,
    )

    report = _collect_target_facts(data, path_filter)
    _assemble_upstream(
        report,
        _find_upstream_from_graph(service_edges, report.target_repo, path_filter),
        _find_upstream_from_nodes(records, report.target_repo, path_filter),
        _producer_hits_from_indices(indices, report.target_repo, path_filter),
        repos_root=repos_root,
        scan_repos=scan_repos,
        path_filter=path_filter,
    )
    return report


def render_trace_markdown(report: TraceReport) -> str:
    """Render a TraceReport as a Markdown document with an untrusted-content notice."""
    lines: list[str] = [
        f"# Flow trace: {report.target_repo}",
        "\n" + UNTRUSTED_CONTENT_NOTICE,
    ]
    if report.path_filter:
        lines.append(f"\n_Path filter: `{report.path_filter}`_\n")
    else:
        lines.append("\n")

    lines.append("## Inbound endpoints\n\n")
    if report.inbound:
        lines.append(md_table(
            ["Path", "Method", "Framework", "Location"],
            [
                [
                    r["path"],
                    r.get("method") or "?",
                    r.get("framework") or "",
                    f"{r['file']}:{r['line']}",
                ]
                for r in report.inbound[:50]
            ],
        ))
    else:
        lines.append("_None matched._\n")

    lines.append("\n## Raw SQL / code payload (same file)\n\n")
    if report.raw_payloads:
        lines.append(md_table(
            ["Endpoint", "Field line", "Sink", "File"],
            [
                [
                    r["endpoint"],
                    str(r.get("field_line", "")),
                    r.get("sink_symbol", ""),
                    r.get("file", ""),
                ]
                for r in report.raw_payloads[:30]
            ],
        ))
    else:
        lines.append("_None — endpoint may not use strict sql/dql field names._\n")

    lines.append("\n## SQL execution sinks (target repo)\n\n")
    if report.sql_sinks:
        lines.append(md_table(
            ["Symbol", "Location", "Call (trimmed)"],
            [
                [r["symbol"], f"{r['file']}:{r['line']}", r.get("raw", "")]
                for r in report.sql_sinks[:30]
            ],
        ))
    else:
        lines.append("_None._\n")

    lines.append("\n## Config data stores (target repo)\n\n")
    if report.stores:
        lines.append(md_table(
            ["Store", "Location"],
            [[r["store_key"], f"{r['file']}:{r['line']}"] for r in report.stores[:20]],
        ))
    else:
        lines.append("_None in committed config scan._\n")

    lines.append("\n## Upstream callers\n\n")
    if report.upstream:
        lines.append(md_table(
            ["Source repo", "Kind", "Confidence", "Evidence", "Ref"],
            [
                [h.source_repo, h.kind, h.confidence, h.evidence[:100], h.ref]
                for h in report.upstream[:100]
            ],
        ))
    else:
        lines.append(
            "_No upstream edges found. Re-run with `--scan-repos repos` to "
            "search source literals._\n",
        )

    lines.append(
        "\n---\n_Phase 2 trace: graph edges + http-out heuristics + optional "
        "repo literal scan. Deep inter-procedural analysis is out of scope._\n",
    )
    return "\n".join(lines)


def _is_in_traces_dir(output: Path, metabase_root: Path) -> bool:
    """Whether an output path lands in the metabase's indexed traces directory."""
    try:
        output.resolve().relative_to((metabase_root / "graphs" / "traces").resolve())
    except (ValueError, OSError):
        return False
    return True


def main() -> int:
    """CLI entry point: parse args, run the trace, and print or write the markdown report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metabase-root", required=True)
    parser.add_argument(
        "--target",
        required=True,
        help="Repo id (group/name) or directory name",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Filter to inbound path (e.g. /queries)",
    )
    parser.add_argument(
        "--scan-repos",
        nargs="?",
        const="repos",
        default=None,
        metavar="REPOS_ROOT",
        help="Also scan cloned repos for path/service literals",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write markdown report to this path",
    )
    parser.add_argument("--api-clients", default=None, help="Path to api-clients.json")
    parser.add_argument(
        "--allow-empty-api-clients",
        action="store_true",
        help="Continue when --api-clients loads 0 bindings (otherwise a hard "
        "error, since it silently disables cross-repo API-client detection).",
    )
    add_internal_groups_arguments(parser)
    args = parser.parse_args()

    metabase_root = Path(args.metabase_root).resolve()
    repos_root = Path(args.scan_repos).resolve() if args.scan_repos else None

    apply_internal_groups_from_args(args)

    if args.api_clients:
        try:
            configure_from_path(
                args.api_clients,
                warn=True,
                allow_empty=args.allow_empty_api_clients,
            )
        except ApiClientConfigError as exc:
            raise SystemExit(f"ERROR: {exc}") from exc

    report = run_trace(
        metabase_root,
        args.target,
        path_filter=args.path,
        repos_root=repos_root,
        scan_repos=repos_root is not None,
    )
    md = render_trace_markdown(report)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"Wrote {out}")
        # Refresh the index only when this trace joined the indexed set. The
        # index states catalogue coverage, so a re-traced endpoint must not leave
        # a figure describing a set it is no longer part of (`OI-38`) — but a
        # trace written somewhere else has not changed that set, and rewriting
        # the metabase would be a surprising side effect of `--output`.
        if _is_in_traces_dir(out, metabase_root):
            print(f"Reindexed {write_traces_index(metabase_root)} trace(s)")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
