"""Discover candidate ``api-clients.json`` bindings from the metabase (plan B11).

``api-clients.json`` maps a first-party HTTP *client library* to the backend
*service* it talks to (see ``docs/api-clients-json.md``). Those edges cannot be
inferred from consumer source alone, so the file is normally authored by hand.

This module drafts **candidates** — never authoritative entries — by mining data
the scan already produced:

* a consumer repo's ``dependencies_internal`` naming a ``*-client`` / ``*-sdk``
  artifact identifies it as a *consumer* of that client library;
* the component-identity index (``repo_utils._build_component_identity_index``)
  resolves that artifact's coordinate to the *publishing* repo — the candidate
  ``target_repo``;
* the target repo's ``http-in`` nodes supply candidate ``paths``.

Because it runs in the aggregation phase (whole fleet in memory) it needs no new
extractor and is unaffected by the worker-init binding-load ordering. Candidates
are written to ``metabase/api-clients.discovered.json`` with a ``confidence`` and
an ``evidence`` block. A human sets each candidate's ``status`` to ``accepted``
or ``rejected``; :func:`promote_api_clients` then merges only the accepted ones
into the authoritative (gitignored) file. Nothing is ever auto-merged — an
unverified binding poisons the taint graph with phantom cross-service edges.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..graph_common import load_v2_repo_records, match_path_in_inbound_index, repo_id
from ..repo_utils import _build_component_identity_index
from .library_source_map import _resolve_clone_path
from .service_call_index import build_inbound_index

DISCOVERED_FILE = "api-clients.discovered.json"

# Human decisions on a candidate, preserved verbatim across regeneration.
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"

# Fields a reviewer may tune; preserved once they mark a candidate non-pending.
_TUNABLE_FIELDS = (
    "target_repo",
    "maven_artifact",
    "import_prefix",
    "paths",
    "payload_fields",
    "service_aliases",
    "class_patterns",
)

# Artifact-id suffixes that strongly signal a published HTTP client library.
_CLIENT_SUFFIXES = (
    "-client",
    "-api-client",
    "-rest-client",
    "-client-java",
    "-java-client",
    "-sdk",
    "-sdk-java",
)


def _looks_like_client_artifact(artifact: str) -> bool:
    """True if an artifact id looks like a published HTTP client library."""
    a = (artifact or "").lower()
    return any(a.endswith(suffix) for suffix in _CLIENT_SUFFIXES)


def _http_in_paths_by_repo(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Map each repo id to the set of ``http-in`` endpoint paths it declares."""
    out: dict[str, set[str]] = defaultdict(set)
    for data in records:
        rid = repo_id(data)
        for node in data.get("nodes", []):
            if node.get("family") != "http-in":
                continue
            path = (node.get("detail") or {}).get("path")
            if isinstance(path, str) and path:
                out[rid].add(path)
    return out


def _key(target_repo: str, artifact: str) -> str:
    """Stable candidate key used for de-dup and for preserving reviewer edits."""
    return f"{target_repo}\t{artifact}"


def _load_discovered(path: Path) -> dict[str, dict[str, Any]]:
    """Return prior candidates keyed by (target_repo, artifact), or {} if absent."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for c in data.get("candidates", []):
        if isinstance(c, dict) and "maven_artifact" in c:
            out[_key(c.get("target_repo", ""), c["maven_artifact"])] = c
    return out


def _confidence(target_repo: str, scanned: set[str], has_paths: bool) -> str:
    """Rate a candidate: resolved-and-in-fleet-with-paths is strongest."""
    if target_repo and target_repo in scanned:
        return "high" if has_paths else "medium"
    if target_repo:
        return "medium"  # resolved to a path not present as a scanned repo
    return "low"  # artifact looks like a client but coordinate did not resolve


def _collect_candidates(
    records: list[dict[str, Any]],
    resolve: Any,
) -> dict[str, dict[str, Any]]:
    """Mine consumer ``dependencies_internal`` into per-(target, artifact) candidates."""
    paths_by_repo = _http_in_paths_by_repo(records)
    cands: dict[str, dict[str, Any]] = {}
    for data in records:
        consumer = repo_id(data)
        for dep in data.get("dependencies_internal", []):
            aid = dep.get("artifactId", "")
            gid = dep.get("groupId", "")
            if not _looks_like_client_artifact(aid):
                continue
            coord = f"{gid}:{aid}" if gid else aid
            target = resolve(coord) or ""
            key = _key(target, aid)
            cand = cands.setdefault(
                key,
                {
                    "target_repo": target,
                    "maven_artifact": aid,
                    "import_prefix": gid,  # best-effort; reviewer tightens
                    "paths": sorted(paths_by_repo.get(target, ())),
                    "payload_fields": ["sql"],  # documented default assumption
                    "service_aliases": [target.split("/")[-1]] if target else [],
                    "class_patterns": [],
                    "coordinate": coord,
                    "consumers": set(),
                },
            )
            cand["consumers"].add(consumer)
    return cands


# A proposed class_pattern is matched as a plain substring in an unguarded,
# language-agnostic tier, so one appearing across the fleet manufactures phantom
# edges everywhere rather than merely adding noise.
MAX_PATTERN_REPOS = 3


def _is_binding_stamped(detail: dict[str, Any]) -> bool:
    """True when a binding, not an observation, put the target on this node.

    Demand-side discovery resolves targets against routes and aliases that
    promoted bindings already influence. Re-ingesting an edge a binding created
    as fresh evidence *for that binding* inflates confidence on every run, so the
    provenance already recorded in ``target_repo_evidence`` is used to exclude it.
    """
    evidence = detail.get("target_repo_evidence")
    return isinstance(evidence, str) and evidence.startswith("api-client class")


def _enclosing_class(file_path: str) -> str:
    """Best-effort class name for a call site, from its file name.

    The aggregation phase has the metabase, not the sources, so the enclosing
    class is taken from the file stem. That is exact for Java and Kotlin, where
    the public type must match the file name, and a reasonable proposal
    elsewhere — the reviewer confirms it either way.
    """
    return Path(file_path).stem if file_path else ""


def _repos_containing_class(records: list[dict[str, Any]], class_name: str) -> set[str]:
    """Repos with a file of this name, as a proxy for corpus-wide occurrence."""
    hits: set[str] = set()
    for data in records:
        rid = repo_id(data)
        for node in data.get("nodes", []):
            if _enclosing_class(str(node.get("file", ""))) == class_name:
                hits.add(rid)
                break
    return hits


def _demand_side_observations(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map target repo -> observed call sites that resolve to it.

    Mines the direction supply-side discovery cannot reach: a call site landing
    on a known service in a repo that declares no client library for it. A repo
    that hand-rolls HTTP has no ``*-client`` dependency, so no amount of
    dependency parsing finds it (OI-4).
    """

    inbound = build_inbound_index(records)
    memo: dict[str, tuple[list[Any], str]] = {}
    observed: dict[str, dict[str, Any]] = {}

    for data in records:
        consumer = repo_id(data)
        for node in data.get("nodes", []):
            if node.get("family") != "http-out":
                continue
            detail = node.get("detail") or {}
            if _is_binding_stamped(detail):
                continue
            path = detail.get("path")
            if not isinstance(path, str) or not path:
                continue
            rows, _conf = match_path_in_inbound_index(path, inbound, memo=memo)
            for target, target_path, _method, _ref in rows:
                if target == consumer:
                    continue
                entry = observed.setdefault(
                    target, {"consumers": set(), "classes": set(), "paths": set()}
                )
                entry["consumers"].add(consumer)
                entry["paths"].add(target_path)
                cls = _enclosing_class(str(node.get("file", "")))
                if cls:
                    entry["classes"].add(cls)
    return observed


def _build_entry(
    cand: dict[str, Any],
    scanned: set[str],
    prev: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble a candidate entry, preserving a reviewer's non-pending decision."""
    consumers = sorted(cand.pop("consumers"))
    coordinate = cand.pop("coordinate")
    warnings = cand.pop("warnings", [])
    has_paths = bool(cand["paths"])
    evidence = {
        "coordinate": coordinate,
        "consumers": consumers,
        "resolved": bool(cand["target_repo"]),
        "paths_from_target_scan": has_paths and cand["target_repo"] in scanned,
    }
    # A reviewer who set status to accepted/rejected has tuned the binding; keep
    # their edits and status, only refreshing evidence + confidence.
    if prev and prev.get("status") in (STATUS_ACCEPTED, STATUS_REJECTED):
        entry = {k: prev.get(k, cand.get(k)) for k in _TUNABLE_FIELDS}
        entry["status"] = prev["status"]
    else:
        entry = {k: cand[k] for k in _TUNABLE_FIELDS}
        entry["status"] = STATUS_PENDING
    # How the candidate was found. `both` is materially stronger than either
    # alone: a declared dependency and an observed call site are independent
    # lines of evidence. `call-site` sorts lowest, since it rests on the path
    # matching that OI-1 showed can be wrong.
    entry["discovery_method"] = cand.get("discovery_method", "dependency")
    if warnings:
        entry["warnings"] = warnings
    entry["confidence"] = _confidence(
        str(entry["target_repo"] or ""), scanned, bool(entry["paths"])
    )
    entry["evidence"] = evidence
    return entry


def _apply_demand_side(
    cands: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
) -> None:
    """Enrich supply-side candidates with observed call sites, or create new ones.

    Runs *after* the supply-side pass so it can do a keyed lookup rather than a
    merge: the two directions produce different fields for the same candidate
    (``maven_artifact`` and ``import_prefix`` only exist supply-side;
    ``class_patterns`` only demand-side), and a demand-side-only candidate has no
    artifact id to key on. Sequencing is for correctness, not speed — both passes
    run in the aggregation phase with the fleet already in memory.
    """
    observed = _demand_side_observations(records)
    by_target = {c["target_repo"]: (k, c) for k, c in cands.items() if c["target_repo"]}

    for target, seen in sorted(observed.items()):
        classes = sorted(seen["classes"])
        warnings = []
        for cls in classes:
            repos = _repos_containing_class(records, cls)
            if len(repos) > MAX_PATTERN_REPOS:
                warnings.append(
                    f"class_pattern {cls!r} appears in {len(repos)} repos; too "
                    "generic to be safe — narrow it before accepting"
                )

        existing = by_target.get(target)
        if existing is not None:
            _key, cand = existing
            cand["class_patterns"] = sorted({*cand["class_patterns"], *classes})
            cand["paths"] = sorted({*cand["paths"], *seen["paths"]})
            cand["consumers"] |= seen["consumers"]
            cand["discovery_method"] = "both"
            if warnings:
                cand["warnings"] = warnings
            continue

        # No dependency declares this hop — the hand-rolled case supply-side
        # discovery cannot reach at all.
        cands[_key_demand(target)] = {
            "target_repo": target,
            "maven_artifact": "",
            "import_prefix": "",
            "paths": sorted(seen["paths"]),
            "payload_fields": ["sql"],
            "service_aliases": [target.split("/")[-1]],
            "class_patterns": classes,
            "coordinate": "",
            "consumers": set(seen["consumers"]),
            "discovery_method": "call-site",
            **({"warnings": warnings} if warnings else {}),
        }


def _key_demand(target_repo: str) -> str:
    """Candidate key for a call-site-only hop, which has no artifact to key on."""
    return _key(target_repo, "")


def discover_api_clients_from_records(
    metabase_root: Path,
    records: list[dict[str, Any]],
    resolve: Any,
) -> int:
    """Run both discovery passes over in-memory records; returns the candidate count."""
    scanned = {repo_id(d) for d in records}
    cands = _collect_candidates(records, resolve)
    for cand in cands.values():
        cand.setdefault("discovery_method", "dependency")
    _apply_demand_side(cands, records)
    prev = _load_discovered(metabase_root / DISCOVERED_FILE)

    entries = [
        _build_entry(cand, scanned, prev.get(key))
        for key, cand in sorted(cands.items())
    ]
    _write_discovered(metabase_root, entries)
    return len(entries)


def discover_api_clients(
    metabase_root: Path,
    repo_jsons: list[Path],
    repos_root: Path,
) -> int:
    """Write ``metabase/api-clients.discovered.json`` and return the candidate count.

    Regenerates every candidate from the current metabase, preserving the
    ``status`` and tuned fields of any candidate a reviewer has already accepted
    or rejected. Never touches the authoritative ``api-clients.json``.
    """

    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    by_coord, by_name, by_full = _build_component_identity_index(repos_root)

    def resolve(coord: str) -> str | None:
        """Resolve a maven coordinate to the repo id that publishes it, or None."""
        return _resolve_clone_path(coord, by_coord, by_name, by_full)

    return discover_api_clients_from_records(metabase_root, records, resolve)


def _write_discovered(metabase_root: Path, entries: list[dict[str, Any]]) -> None:
    """Persist the candidate file."""
    out = {
        "_comment": (
            "CANDIDATE api-client bindings discovered by src2sink-build "
            "--discover-api-clients. NOT authoritative. Review each entry, set "
            "'status' to 'accepted' or 'rejected' (tightening import_prefix/paths/"
            "payload_fields as needed), then run --promote-api-clients to merge the "
            "accepted ones into api-clients.json. Same sensitivity as that file."
        ),
        "candidates": entries,
    }
    (metabase_root / DISCOVERED_FILE).write_text(
        json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def _load_bindings(path: Path) -> list[dict[str, Any]]:
    """Return the authoritative bindings list, or [] if the file is missing/invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    bindings = data.get("bindings")
    return [b for b in bindings if isinstance(b, dict)] if isinstance(bindings, list) else []


def promote_api_clients(metabase_root: Path, target_path: Path) -> int:
    """Merge ``accepted`` candidates into the authoritative ``api-clients.json``.

    Only candidates whose ``status`` a reviewer set to ``accepted`` are merged
    (by ``(target_repo, maven_artifact)``): a new coordinate is appended, an
    existing one is updated in place. Idempotent — re-running merges the same set
    without creating duplicates. Returns the number of accepted candidates merged.
    """
    candidates = _load_discovered(metabase_root / DISCOVERED_FILE).values()
    accepted = [c for c in candidates if c.get("status") == STATUS_ACCEPTED]
    if not accepted:
        return 0

    bindings = _load_bindings(target_path)
    index = {(b.get("target_repo"), b.get("maven_artifact")): b for b in bindings}
    for c in accepted:
        binding = {k: c[k] for k in _TUNABLE_FIELDS if k in c}
        key = (binding.get("target_repo"), binding.get("maven_artifact"))
        if key in index:
            index[key].update(binding)
        else:
            bindings.append(binding)
            index[key] = binding

    target_path.write_text(
        json.dumps({"bindings": bindings}, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return len(accepted)
