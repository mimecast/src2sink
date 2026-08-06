"""Shared helpers for v2 graph aggregators and trace."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from .known_api_clients import binding_alias_index
from .schema import SCHEMA_VERSION

# Hosts that are not useful service-graph targets (v1 parity).
SERVICE_GRAPH_NOISE_HOSTS = frozenset({
    # nosec B104 - a denylist of noise hostnames found in scanned source; nothing binds a socket.
    "localhost", "127.0.0.1", "0.0.0.0", "example.com", "example.org",
    "test.com", "foo.com", "test.local", "playwright.dev",
    "www.w3.org", "schemas.xmlsoap.org", "swagger.io",
    "www.google.com", "google.com", "motoapi.amazonaws.com",
})

HTTP_URL_RX = re.compile(
    r"https?://([A-Za-z0-9][A-Za-z0-9.\-]*(?::\d+)?)(?:/[^\s\"'<>]*)?",
    re.IGNORECASE,
)
PATH_LITERAL_RX = re.compile(
    r'["\'](/[A-Za-z0-9][A-Za-z0-9_./\-{}]*)["\']',
)
JDBC_URL_RX = re.compile(
    r"jdbc:([a-z0-9]+)://([^/\s\"';?]+)(?:/([A-Za-z0-9_\-]+))?",
    re.IGNORECASE,
)
MONGODB_URI_RX = re.compile(
    r"mongodb(?:\+srv)?://([^/\s\"';?]+)",
    re.IGNORECASE,
)
REDIS_URL_RX = re.compile(
    r"rediss?://([^/\s\"';?]+)",
    re.IGNORECASE,
)


def v2_record_paths(
    metabase_root: Path,
    *,
    json_paths: list[Path] | None = None,
) -> list[Path]:
    """The record files a metabase is made of, in a stable order.

    Split out because the index's freshness check needs the file list without
    reading any of them (`OI-15`).
    """
    return json_paths or sorted(metabase_root.glob("repos/*/*.json"))


def iter_v2_repo_records(
    metabase_root: Path,
    *,
    json_paths: list[Path] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield v2 repo records one at a time, skipping unreadable or mismatched files.

    The streaming form of :func:`load_v2_repo_records`. A caller that consumes
    each record and keeps only what it needs holds one record at a time instead
    of the fleet — which at 34 GB on disk is the difference between running and
    being killed (`OI-15`).
    """
    for jp in v2_record_paths(metabase_root, json_paths=json_paths):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema_version") != SCHEMA_VERSION:
            continue
        data["_json_path"] = str(jp)
        yield data


def load_v2_repo_records(
    metabase_root: Path,
    *,
    json_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """Load v2 metabase repo records, skipping unreadable or mismatched-schema files.

    Holds the whole fleet. Retained because aggregation genuinely needs several
    passes over it, but a reader that touches one repo should use
    :func:`iter_v2_repo_records` or the persisted index instead.
    """
    return list(iter_v2_repo_records(metabase_root, json_paths=json_paths))


def load_one_v2_repo_record(json_path: Path) -> dict[str, Any] | None:
    """Read a single repo record, or None if it is unreadable or the wrong schema.

    What a trace needs once the index has told it which file to open.
    """
    try:
        data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    data["_json_path"] = str(json_path)
    return data


def repo_id(data: dict[str, Any]) -> str:
    """Return the ``group/name`` repo id for a record."""
    return f"{data['group']}/{data['name']}"


def iter_nodes(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield each node of a record tagged with its owning repo id."""
    rid = repo_id(data)
    for node in data.get("nodes", []):
        yield {**node, "repo": rid}


# These two helpers are pure, tiny, and called with the *same* few thousand
# route strings millions of times over a fleet-sized run — collecting the
# service-call graph for 400 repos spent most of its 38s here (OI-14). The cache
# is bounded because the keys are paths read out of scanned repositories: an
# unbounded cache would let a hostile or merely enormous fleet grow it without
# limit. 64k entries comfortably holds a large fleet's distinct routes while
# capping the cache at a few tens of MB.
_PATH_CACHE_MAX = 65_536


@lru_cache(maxsize=_PATH_CACHE_MAX)
def normalize_path_template(path: str) -> str:
    """Collapse path params to a single form for matching."""
    if not path or path == "?":
        return ""
    p = path.strip()
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"\{[^}]+\}", "{}", p)
    p = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", "{}", p)
    p = re.sub(r"/+", "/", p)
    return p.rstrip("/") or "/"


_VERSION_SEGMENT_RX = re.compile(r"^v\d+$", re.I)
_GENERIC_SEGMENTS = frozenset({"api", "rest", "internal", "public", "service", "services"})
# `normalize_path_template` collapses every path parameter to this. It names no
# destination at all — `/{id}` and `/{name}` are the same shape and nothing else,
# so matching on it is a coincidence of syntax (OI-25).
_PLACEHOLDER_SEGMENT = "{}"

# Segments naming an *operation* rather than a destination. Deliberately NOT
# removed by `_significant_segments`: `/v1/query` reduces to ("query",) and is a
# real route of a real query service, so dropping these would delete legitimate
# endpoints. Instead a match resting *only* on them is capped at `low` — two
# services both exposing `/search` is weak evidence, not none (OI-25).
_OPERATION_SEGMENTS = frozenset({
    "create", "update", "delete", "remove", "add", "save", "edit", "put", "patch",
    "get", "fetch", "read", "list", "all", "search", "find", "query", "count",
})


@lru_cache(maxsize=_PATH_CACHE_MAX)
def _significant_segments(path: str) -> tuple[str, ...]:
    """Return the segments of a normalised path that actually name a destination.

    `/v1` and `/api` are not destinations — they say which edition of an API you
    are addressing, not what you are addressing. Dropping them is what stops a
    repo exposing a bare `/v1` from matching every `/v1/...` path in the fleet.
    A collapsed path parameter (`{}`) is dropped for the same reason, one step
    further: it names nothing whatsoever, so `/{id}` and `/{name}` share only a
    shape (OI-25).

    Operation verbs are *not* dropped — see :data:`_OPERATION_SEGMENTS` for why
    removing them would delete real routes. They are handled by grading instead.

    Returns a tuple, not a list: the result is cached and therefore shared by
    every caller asking about the same path, so it must not be mutable.
    """
    return tuple(
        s for s in path.split("/")
        if s
        and s != _PLACEHOLDER_SEGMENT
        and not _VERSION_SEGMENT_RX.match(s)
        and s.lower() not in _GENERIC_SEGMENTS
    )


def _names_only_an_operation(segments: tuple[str, ...]) -> bool:
    """True if every significant segment names an operation rather than a thing."""
    return bool(segments) and all(s.lower() in _OPERATION_SEGMENTS for s in segments)


def path_templates_match(outbound: str, inbound: str) -> str | None:
    """Return a confidence label if two route templates denote the same endpoint.

    Confidence reflects how much *meaning* matched, not which structural rule
    fired. 1.1.0 graded by rule — any prefix relation scored `medium` and any
    suffix relation `low` — so a bare `/v1` route beat the service that actually
    exposed `/stock`, and `match_path_in_inbound_index` then discarded the correct
    candidate rather than ranking it second (OI-1).

    The relation is symmetric: a caller hitting a child route and a service
    declaring the parent are the same evidence whichever way round they arrive.

    This is a *routing* predicate. For "show me everything under this prefix",
    which is a different question, use :func:`path_filter_matches`.
    """
    o = normalize_path_template(outbound)
    i = normalize_path_template(inbound)
    if not o or not i:
        return None

    op = _significant_segments(o)
    ip = _significant_segments(i)
    # A side that reduces to nothing names a version, a layer or a placeholder,
    # not a route. This is checked *before* the equality shortcut: two repos both
    # exposing a bare `/v1` are identical strings and still name nothing, and
    # returning early on equality is how that scored `high` (OI-24).
    if not op or not ip:
        return None

    label = _structural_match(o, i, op, ip)
    if label is not None and _names_only_an_operation(op) and _names_only_an_operation(ip):
        # Everything that matched was a verb: `/search` against `/search` says
        # both sides do a search, not that either calls the other (OI-25).
        return "low"
    return label


def _structural_match(
    o: str, i: str, op: tuple[str, ...], ip: tuple[str, ...]
) -> str | None:
    """Grade two normalised paths by how much of their meaning coincides."""
    if o == i:
        return "high"
    if op == ip:
        return "medium"

    shorter, longer = (op, ip) if len(op) < len(ip) else (ip, op)
    if longer[: len(shorter)] == shorter:
        # One is a child route of the other: /stock/dispatch against /stock.
        return "medium"
    if longer[-len(shorter):] == shorter:
        # Only the tail is shared: /orders/{}/lines against /lines. Weak — the
        # common segment may name a sub-resource that many services expose.
        return "low"
    return None


def path_filter_matches(candidate: str, path_filter: str | None) -> bool:
    """True if ``candidate`` satisfies a user-supplied ``--path`` filter.

    Filtering and routing ask different questions. "Show me everything under
    `/v1`" is a legitimate filter, while `/v1` denotes no endpoint for routing
    purposes, so :func:`path_templates_match` returns None for it. Keeping one
    predicate for both would have silently emptied `trace --path /v1` when OI-1
    was fixed; this preserves the looser, prefix-tolerant behaviour filters need.
    """
    if not path_filter:
        return True
    c = normalize_path_template(candidate)
    f = normalize_path_template(path_filter)
    if not c or not f:
        return False
    if c == f:
        return True
    if c.startswith(f + "/") or f.startswith(c + "/"):
        return True
    c_parts = [s for s in c.split("/") if s]
    f_parts = [s for s in f.split("/") if s]
    return len(c_parts) >= 2 and len(f_parts) >= 1 and c_parts[-len(f_parts):] == f_parts


def extract_urls_and_paths(raw: str) -> tuple[list[str], list[str]]:
    """Extract (hosts, paths) from free text, dropping noise hosts."""
    hosts: list[str] = []
    paths: list[str] = []
    for m in HTTP_URL_RX.finditer(raw):
        host = m.group(1).lower().split(":")[0]
        if host and host not in SERVICE_GRAPH_NOISE_HOSTS:
            hosts.append(host)
        full = m.group(0)
        slash = full.find("/", full.find("://") + 3)
        if slash >= 0:
            path = full[slash:].split("?")[0].split("#")[0]
            if path and path != "/":
                paths.append(path)
    for m in PATH_LITERAL_RX.finditer(raw):
        paths.append(m.group(1))
    return hosts, paths


def repo_name_aliases(name: str) -> set[str]:
    """Return lowercase name variants (hyphen/underscore, ``-service`` stripped)."""
    base = name.lower()
    aliases = {base, base.replace("-", "_"), base.replace("-", "")}
    if base.endswith("-service"):
        aliases.add(base[: -len("-service")])
    return aliases


def host_matches_repo(host: str, target_repo: str) -> bool:
    """Return True if a hostname contains any alias of the target repo's name."""
    host_l = host.lower()
    _, name = target_repo.split("/", 1)
    for alias in repo_name_aliases(name):
        if alias in host_l:
            return True
    return False


def build_repo_alias_index(records: list[dict[str, Any]]) -> dict[str, str]:
    """Map service-name aliases (lowercase) to ``group/name`` repo ids.

    Repo-derived aliases come first; configured api-client binding
    ``service_aliases`` then fill any name they do not already cover, so a
    DNS/service name that differs from the repo's short name (the usual case for
    an internal service) still resolves to a repo. Binding aliases never override
    a real repo name — a repo record is the stronger evidence.
    """

    mapping: dict[str, str] = {}
    for data in records:
        rid = repo_id(data)
        mapping[data["name"].lower()] = rid
        for alias in repo_name_aliases(data["name"]):
            mapping[alias] = rid
    for alias, rid in binding_alias_index().items():
        mapping.setdefault(alias, rid)
    return mapping


def resolve_repo_for_host(host: str, alias_to_repo: dict[str, str]) -> str | None:
    """Best-effort repo id from an outbound hostname."""
    if host in SERVICE_GRAPH_NOISE_HOSTS:
        return None
    host_l = host.lower()
    # Try the full host first (an alias may itself be dotted), then the first
    # label — `some-service.some-namespace.svc.cluster.local` -> the service.
    for candidate in (host_l, host_l.split(".")[0]):
        tgt = alias_to_repo.get(candidate)
        if tgt:
            return tgt
    for _alias, rid in alias_to_repo.items():
        if host_matches_repo(host, rid):
            return rid
    return None


_MATCH_CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def confidence_rank(confidence: str) -> int:
    """Map a confidence label to a sortable rank; unknown labels rank lowest.

    The single definition of "stronger evidence", because the project compares
    confidences in several places and had three copies of this map. An unknown
    label ranking 0 is deliberate: a confidence nobody recognises should lose to
    one that is understood, not silently outrank it.
    """
    return _MATCH_CONF_RANK.get(confidence, 0)


def _names_a_destination(norm: str) -> bool:
    """True if a normalised path names something a caller could be addressing.

    `/v1`, `/api` and `/{}` do not: they are a version, a layer and a shape.
    Applied at the index lookup as well as in `path_templates_match`, because the
    dict lookup there is a fast path that never consults the predicate — `OI-24`
    fixed the callee and the edges came from the caller (`OI-28`).
    """
    return bool(norm) and bool(_significant_segments(norm))


def match_path_in_inbound_index(
    path: str,
    inbound: dict[str, list[tuple[Any, ...]]],
    *,
    inbound_path_col: int = 1,
    memo: dict[str, tuple[list[tuple[Any, ...]], str]] | None = None,
) -> tuple[list[tuple[Any, ...]], str]:
    """Match outbound path to indexed inbound rows; returns (rows, confidence).

    An exact normalised-template hit wins outright. Otherwise every indexed route
    is scored and the best group is returned, ranked first by confidence and then
    by **specificity** — a route whose significant segments equal the query's beats
    one that merely contains them, so `/v1/stock/dispatch` resolves to the service
    declaring `/stock/dispatch` rather than also reaching the one declaring
    `/stock`. Only candidates tying on both are returned together, and they are
    ordered deterministically so the output does not depend on how the index was
    built.

    1.1.0 ranked by confidence alone, which resolved equal-confidence ties
    arbitrarily; before that it took the first fuzzy match in dict-iteration order.

    ``memo`` optionally caches results by normalised path across calls sharing one
    ``inbound`` index — the fuzzy pass is O(routes), so memoising matters once
    there are many nodes to resolve.
    """
    norm = normalize_path_template(path)
    # A path that reduces to no significant segments names a version, a layer or
    # a placeholder — not a route. Checked here rather than only in
    # `path_templates_match`, because the dict lookup below is a fast path that
    # never consults the predicate: `OI-24` fixed the callee and the edges came
    # from the caller (OI-28). The fuzzy pass would reject the same path anyway.
    if not _names_a_destination(norm):
        return [], "high"
    if memo is not None and norm in memo:
        return memo[norm]

    targets = inbound.get(norm, [])
    if targets:
        result: tuple[list[tuple[Any, ...]], str] = (list(targets), "high")
    else:
        query_sig = _significant_segments(norm)
        # (confidence rank, specificity) -> the label and rows scoring it.
        scored: list[tuple[tuple[int, int], str, list[tuple[Any, ...]]]] = []
        for _in_norm, rows in inbound.items():
            if not rows:
                continue
            row = rows[0]
            candidate = row[inbound_path_col] if len(row) > inbound_path_col else ""
            matched = path_templates_match(path, str(candidate))
            if not matched:
                continue
            cand_sig = _significant_segments(normalize_path_template(str(candidate)))
            score = (
                _MATCH_CONF_RANK[matched],
                # Prefer the nearest relative. Distance 0 means the same
                # significant route, which is why no separate equality term is
                # needed: two lists that match by prefix or suffix and have the
                # same length are necessarily equal.
                -abs(len(cand_sig) - len(query_sig)),
            )
            scored.append((score, matched, rows))

        if scored:
            best = max(score for score, _label, _rows in scored)
            winners = [(label, rows) for score, label, rows in scored if score == best]
            result = (
                sorted(row for _label, rows in winners for row in rows),
                winners[0][0],
            )
        else:
            result = ([], "high")

    if memo is not None:
        memo[norm] = result
    return result


def store_key_from_node(node: dict[str, Any]) -> str | None:
    """Return a canonical datastore key (jdbc/mongodb/redis/s3) for a store node, or None."""
    detail = node.get("detail") or {}
    vendor = detail.get("vendor", "?")
    if vendor == "jdbc":
        url = detail.get("url", "")
        m = JDBC_URL_RX.search(url)
        if m:
            return f"jdbc:{m.group(1).lower()}://{m.group(2).lower()}/{m.group(3) or ''}"
        return f"jdbc:unknown:{url[:80]}"
    if vendor == "mongodb":
        url = detail.get("url", "")
        m = MONGODB_URI_RX.search(url)
        host = m.group(1).lower() if m else url[:80]
        return f"mongodb://{host}"
    if vendor == "redis":
        url = detail.get("url", "")
        m = REDIS_URL_RX.search(url)
        host = m.group(1).lower() if m else url[:80]
        return f"redis://{host}"
    if vendor == "s3":
        bucket = detail.get("bucket", "")
        return f"s3://{bucket}" if bucket else None
    return None
