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

from ..graph_common import load_v2_repo_records, repo_id

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


def _build_entry(
    cand: dict[str, Any],
    scanned: set[str],
    prev: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble a candidate entry, preserving a reviewer's non-pending decision."""
    consumers = sorted(cand.pop("consumers"))
    coordinate = cand.pop("coordinate")
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
    entry["confidence"] = _confidence(
        str(entry["target_repo"] or ""), scanned, bool(entry["paths"])
    )
    entry["evidence"] = evidence
    return entry


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
    from ..repo_utils import _build_component_identity_index
    from .library_source_map import _resolve_clone_path

    records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
    by_coord, by_name, by_full = _build_component_identity_index(repos_root)

    def resolve(coord: str) -> str | None:
        return _resolve_clone_path(coord, by_coord, by_name, by_full)

    scanned = {repo_id(d) for d in records}
    cands = _collect_candidates(records, resolve)
    prev = _load_discovered(metabase_root / DISCOVERED_FILE)

    entries = [
        _build_entry(cand, scanned, prev.get(key))
        for key, cand in sorted(cands.items())
    ]
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
    return len(entries)


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
