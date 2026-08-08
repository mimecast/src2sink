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
import re
import sys
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


# Suffixes a client library's repo conventionally carries. Used only to derive
# the *stem to search for* — never as the test itself. The test is whether the
# repo declares inbound endpoints, which is name-independent and catches estates
# that name their libraries differently (`OI-40`).
_CLIENT_SUFFIX_RX = re.compile(r"[-_](?:clients?|sdk)$", re.IGNORECASE)


def _service_for_client_repo(
    declaring_repo: str, endpoints: dict[str, set[str]]
) -> str | None:
    """Map a client-library repo to the service repo it fronts, or None.

    `resolve(coord)` names the repo that *declares* the artifact, and
    `_canonical_repo_id` maps that to the repo owning it. Both are correct. But
    when a client library is published from **its own repository** rather than as
    a submodule of the service, the repo owning the declaration *is* the library
    — so the binding points at the client instead of the service it calls
    (`OI-40`).

    `OI-33` fixed the *shape* of the identity; this is the *referent* being
    wrong, and no amount of path normalisation reaches it. The service is not
    named anywhere in the consumer's dependency declaration.

    A service has inbound endpoints; a client library does not. So the test is
    the endpoint count, and the name only supplies the stem to search for.
    Returns None rather than guessing — 31 of 42 in the observed fleet had no
    sibling because the service is not in the scanned set at all, and inventing
    one would manufacture the broken edges `OI-33` was about.
    """
    if "/" not in declaring_repo:
        return None
    if endpoints.get(declaring_repo):
        return None  # it receives calls, so it is a service; leave it alone
    stem = _CLIENT_SUFFIX_RX.sub("", declaring_repo.split("/")[-1])
    if not stem:
        return None
    hits = sorted(
        repo for repo, paths in endpoints.items()
        if paths and repo.split("/")[-1] in (stem, f"{stem}-service")
    )
    return hits[0] if len(hits) == 1 else None


def _key(target_repo: str, artifact: str) -> str:
    """Stable candidate key used for de-dup and for preserving reviewer edits."""
    return f"{target_repo}\t{artifact}"


def _canonical_repo_id(resolved: str, known: frozenset[str]) -> str | None:
    """Map a resolved coordinate path to the repo id that owns it.

    The identity index resolves a coordinate to the directory that *declares* it,
    which may be a build module inside a repo — `group/repo/some-client`. The
    demand-side pass names its target `group/repo`, from `repo_id()`. The two
    never met, so `discovery_method: "both"` — the strongest signal the design
    produces — could not occur even once (`OI-33`).

    Truncating to two segments would be wrong: `group/subgroup/repo` is a valid
    GitLab path and this estate contains them (`OI-34`). So match the longest
    known repo id instead. That handles in-repo modules and nested repos alike,
    and depends on neither path depth nor on `.git` being present — 65 of 746
    repos in the observed fleet have no `.git` at all.

    Returns None when nothing matches, which is a resolver failure worth
    surfacing rather than a repo id worth guessing.
    """
    if not resolved:
        return None
    parts = resolved.split("/")
    for n in range(len(parts), 0, -1):
        candidate = "/".join(parts[:n])
        if candidate in known:
            return candidate
    return None


def _load_discovered(
    path: Path, known: frozenset[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Return prior candidates keyed by (target_repo, artifact), or {} if absent.

    ``known`` lets a candidate stored under a pre-`OI-33` module path also be
    found under its canonical repo id, so reviewer decisions survive the fix.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for c in data.get("candidates", []):
        if not isinstance(c, dict) or "maven_artifact" not in c:
            continue
        stored = str(c.get("target_repo", ""))
        artifact = c["maven_artifact"]
        out[_key(stored, artifact)] = c
        # A candidate reviewed before `OI-33` was keyed on the *module* path.
        # Index it under its canonical repo id too, or the fix would silently
        # discard every accept/reject a reviewer has already made.
        canonical = _canonical_repo_id(stored, known) if known else None
        if canonical and canonical != stored:
            out.setdefault(_key(canonical, artifact), c)
    return out


def _confidence(target_repo: str, scanned: set[str], has_paths: bool) -> str:
    """Rate a candidate: resolved-and-in-fleet-with-paths is strongest."""
    if target_repo and target_repo in scanned:
        return "high" if has_paths else "medium"
    if target_repo:
        return "medium"  # resolved to a path not present as a scanned repo
    return "low"  # artifact looks like a client but coordinate did not resolve


def _resolve_target(
    resolve: Any, coord: str, known: frozenset[str], endpoints: dict[str, set[str]],
) -> tuple[str, str, str]:
    """Resolve a coordinate to (declaring path, service repo, client repo if corrected).

    Two corrections in sequence, both needed and neither sufficient alone:
    `_canonical_repo_id` fixes the identity's *shape* (`OI-33`), and
    `_service_for_client_repo` fixes its *referent* when the declaring repo turns
    out to be the client library rather than the service (`OI-40`).

    The client repo is returned rather than discarded, because a substitution a
    reviewer cannot see is one they cannot check.
    """
    resolved = resolve(coord) or ""
    target = _canonical_repo_id(resolved, known) or ""
    corrected = _service_for_client_repo(target, endpoints)
    if corrected:
        return resolved, corrected, target
    return resolved, target, ""


def _collect_candidates(
    records: list[dict[str, Any]],
    resolve: Any,
) -> dict[str, dict[str, Any]]:
    """Mine consumer ``dependencies_internal`` into per-(target, artifact) candidates.

    ``target_repo`` is normalised to a repo id the metabase knows (`OI-33`). The
    resolver returns the directory that *declares* a coordinate, which for a
    multi-module build is deeper than the repo — and the demand-side pass names
    the same service by its repo id, so without this the two can never agree.
    """
    paths_by_repo = _http_in_paths_by_repo(records)
    known = frozenset(repo_id(d) for d in records)
    cands: dict[str, dict[str, Any]] = {}
    for data in records:
        consumer = repo_id(data)
        for dep in data.get("dependencies_internal", []):
            aid = dep.get("artifactId", "")
            gid = dep.get("groupId", "")
            if not _looks_like_client_artifact(aid):
                continue
            coord = f"{gid}:{aid}" if gid else aid
            # The declaring directory, then the repo that owns it. Both are kept:
            # the module path is strictly more information and `OI-34` may want it.
            resolved, target, client_repo = _resolve_target(
                resolve, coord, known, paths_by_repo,
            )
            key = _key(target, aid)
            cand = cands.setdefault(
                key,
                {
                    "target_repo": target,
                    "target_module": resolved,
                    "client_repo": client_repo,
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


def _repos_by_class(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Map each class name to the repos containing a file of that name.

    Built in **one** pass and queried by key. It replaces a per-class rescan of
    the whole fleet: the caller iterates targets, and each target's classes, and
    asked the corpus about every one — so the cost was
    ``targets x classes x records x nodes``, and on a synthetic fleet where all
    four scale together the node visits grew ~15x for every doubling of the
    repository count.

    Nothing about the answer needed that. The question is corpus-wide and
    target-independent: which repos hold a file called `StockClient`. Asked once
    per class instead of once per (target, class), and answered from an index
    instead of a scan.
    """
    by_class: dict[str, set[str]] = defaultdict(set)
    for data in records:
        rid = repo_id(data)
        for node in data.get("nodes", []):
            cls = _enclosing_class(str(node.get("file", "")))
            if cls:
                by_class[cls].add(rid)
    return by_class


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


def _unresolved_warning(target_repo: str, coordinate: str, module: str) -> str | None:
    """Explain an unresolvable coordinate, or None when it resolved.

    An empty `target_repo` used to arrive as an ordinary candidate with nothing
    saying why — 8 of them in the first fleet run. Promoting one creates an edge
    pointing at nothing, so it must not read like a merely weak candidate
    (`OI-33`, and the class in `OI-36`).
    """
    if target_repo:
        return None
    where = f"; declared at {module!r}" if module else "; not found in the checkout"
    return (
        f"coordinate {coordinate or '(none)'} did not resolve to a known repo"
        f"{where} — the target cannot be trusted, and promoting this would "
        "create an edge to nothing"
    )


def _target_warnings(
    target_repo: str, coordinate: str, module: str, client_repo: str,
) -> list[str]:
    """Everything worth saying about how this candidate's target was arrived at."""
    out: list[str] = []
    unresolved = _unresolved_warning(target_repo, coordinate, module)
    if unresolved:
        out.append(unresolved)
    if client_repo:
        out.append(
            f"target corrected from {client_repo!r}, which declares the client "
            "library and no inbound endpoints, to the service it appears to "
            "front — check the substitution before accepting (OI-40)"
        )
    return out


def _endpointless_warning(target_repo: str, has_paths: bool, scanned: set[str]) -> str | None:
    """Flag a target that receives no calls, without dropping it.

    A binding's target is by definition a service that receives calls, so a
    target with no detected inbound endpoint cannot fulfil that. But **zero
    endpoints is also what a detection gap looks like** — `OI-17` left an entire
    language half of a fleet returning none — and the two causes are
    indistinguishable from the outside.

    So this warns and never filters. Dropping these would turn `OI-17`-class
    blindness into an invisible data-quality filter, which is `OI-36` with the
    tool doing it to itself.
    """
    if not target_repo or has_paths or target_repo not in scanned:
        return None
    return (
        f"{target_repo} declares no inbound endpoints: it may be a client "
        "library whose service is outside the scanned fleet, or a service whose "
        "endpoints were not detected — the two look identical from here"
    )


def _entry_fields(
    cand: dict[str, Any], prev: dict[str, Any] | None
) -> dict[str, Any]:
    """The tunable fields and status, preserving a reviewer's decision.

    A reviewer who set accepted/rejected has tuned the binding, so their edits
    and status are kept and only evidence and confidence are refreshed.
    """
    if prev and prev.get("status") in (STATUS_ACCEPTED, STATUS_REJECTED):
        entry = {k: prev.get(k, cand.get(k)) for k in _TUNABLE_FIELDS}
        entry["status"] = prev["status"]
        return entry
    entry = {k: cand[k] for k in _TUNABLE_FIELDS}
    entry["status"] = STATUS_PENDING
    return entry


def _build_entry(
    cand: dict[str, Any],
    scanned: set[str],
    prev: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble a candidate entry, preserving a reviewer's non-pending decision."""
    consumers = sorted(cand.pop("consumers"))
    coordinate = cand.pop("coordinate")
    warnings = list(cand.pop("warnings", []))
    module = cand.pop("target_module", "")
    client_repo = cand.pop("client_repo", "")
    warnings += _target_warnings(
        str(cand.get("target_repo", "")), coordinate, module, client_repo,
    )
    has_paths = bool(cand["paths"])
    evidence = {
        "coordinate": coordinate,
        "consumers": consumers,
        "resolved": bool(cand["target_repo"]),
        "declared_at": module,
        "client_repo": client_repo,
        "paths_from_target_scan": has_paths and cand["target_repo"] in scanned,
    }
    entry = _entry_fields(cand, prev)
    # How the candidate was found. `both` is materially stronger than either
    # alone: a declared dependency and an observed call site are independent
    # lines of evidence. `call-site` sorts lowest, since it rests on the path
    # matching that OI-1 showed can be wrong.
    entry["discovery_method"] = cand.get("discovery_method", "dependency")
    if warnings:
        entry["warnings"] = warnings
    endpointless = _endpointless_warning(
        str(entry["target_repo"] or ""), bool(entry["paths"]), scanned,
    )
    if endpointless:
        existing: list[str] = list(entry.get("warnings") or [])
        entry["warnings"] = [*existing, endpointless]
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
    # One pass over the fleet, before the loop rather than inside it.
    repos_by_class = _repos_by_class(records)

    for target, seen in sorted(observed.items()):
        classes = sorted(seen["classes"])
        warnings = []
        for cls in classes:
            repos = repos_by_class.get(cls, set())
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
    prev = _load_discovered(metabase_root / DISCOVERED_FILE, frozenset(scanned))

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
        """Resolve a coordinate to the clone path that *declares* it, or None.

        Not the repo id — this docstring used to claim it was, and that claim is
        how `OI-33` went unnoticed: `_resolve_clone_path` returns the declaring
        directory, which for a multi-module build sits inside the repo. The
        caller normalises via `_canonical_repo_id`.
        """
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


def _load_bindings_file(path: Path) -> dict[str, Any]:
    """Return the whole authoritative file, or an empty document if unreadable.

    The *whole* document, not just its bindings. `promote` used to rewrite the
    file as `{"bindings": [...]}` and drop every other top-level key — including
    the `_comment` carrying the "never commit — internal topology" handling
    notice on a gitignored, sensitivity-marked file. The marker disappeared
    silently and the file still looked correct (`OI-42`).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # Absent is ordinary — the first promote creates it.
        if path.exists():
            print(f"WARNING: could not read {path}; promoting into an empty file",
                  file=sys.stderr)
        return {}
    except json.JSONDecodeError as exc:
        # Malformed is not ordinary. Silently treating it as empty would discard
        # every existing binding on the next write, which is exactly why
        # `--allow-empty-api-clients` exists for the *load* path (`OI-36`).
        print(f"WARNING: {path} is not valid JSON ({exc}); its existing bindings "
              "will not be preserved by this promote", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _load_bindings(path: Path) -> list[dict[str, Any]]:
    """Return the authoritative bindings list, or [] if the file is missing/invalid."""
    bindings = _load_bindings_file(path).get("bindings")
    return [b for b in bindings if isinstance(b, dict)] if isinstance(bindings, list) else []


def _promotion_rejections(
    candidate: dict[str, Any],
    known_repos: frozenset[str],
    endpoints: dict[str, set[str]],
) -> list[str]:
    """Why this candidate must not be promoted, or an empty list.

    `promote` merged on trust and validated nothing, so every check was the
    reviewer's to perform by hand. Two of the five documented gates are pure
    computation the tool already does at discovery time — 50 of 191 candidates
    in the first fleet review failed one of them (`OI-42`).

    The reviewer keeps the judgement calls. This takes the arithmetic.
    """
    target = str(candidate.get("target_repo") or "")
    artifact = candidate.get("maven_artifact") or "(none)"

    if not target:
        return [f"{artifact}: target_repo is empty — it resolved to no repo at all"]
    if target not in known_repos:
        return [
            f"{artifact}: target_repo {target!r} is not a repo in this metabase; "
            "promoting it would create an edge to a node that does not exist"
        ]
    service = _service_for_client_repo(target, endpoints)
    if service:
        return [
            f"{artifact}: target_repo {target!r} declares no inbound endpoints and "
            f"looks like a client library fronting {service!r} — a binding's "
            "target is the service that receives the calls, not the library"
        ]
    return []


def _screen_for_promotion(
    metabase_root: Path, accepted: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the candidates that pass the mechanical gates, naming the rest."""
    records = load_v2_repo_records(metabase_root)
    known_repos = frozenset(repo_id(d) for d in records)
    endpoints = _http_in_paths_by_repo(records)

    mergeable: list[dict[str, Any]] = []
    for cand in accepted:
        reasons = _promotion_rejections(cand, known_repos, endpoints)
        for reason in reasons:
            print(f"REFUSED: {reason}", file=sys.stderr)
        if not reasons:
            mergeable.append(cand)
    return mergeable


def _merge_bindings(
    bindings: list[dict[str, Any]],
    index: dict[tuple[Any, Any], list[dict[str, Any]]],
    mergeable: list[dict[str, Any]],
) -> None:
    """Merge candidates into ``bindings`` in place, refreshing every duplicate."""
    for c in mergeable:
        binding = {k: c[k] for k in _TUNABLE_FIELDS if k in c}
        key = (binding.get("target_repo"), binding.get("maven_artifact"))
        if key in index:
            for existing in index[key]:
                existing.update(binding)
        else:
            bindings.append(binding)
            index[key].append(binding)


def promote_api_clients(metabase_root: Path, target_path: Path) -> int:
    """Merge ``accepted`` candidates into the authoritative ``api-clients.json``.

    Only candidates whose ``status`` a reviewer set to ``accepted`` are merged
    (by ``(target_repo, maven_artifact)``): a new coordinate is appended, an
    existing one is updated in place. Idempotent — re-running merges the same set
    without creating duplicates. Returns the number of accepted candidates merged.

    Candidates failing a mechanically-checkable gate are **refused and named**
    rather than merged, and the file's other top-level keys survive the rewrite
    (`OI-42`).
    """
    candidates = _load_discovered(metabase_root / DISCOVERED_FILE).values()
    accepted = [c for c in candidates if c.get("status") == STATUS_ACCEPTED]
    if not accepted:
        return 0

    mergeable = _screen_for_promotion(metabase_root, accepted)

    document = _load_bindings_file(target_path)
    bindings = _load_bindings(target_path)
    # Every copy of a key, not just the last. Indexing a list that may hold
    # duplicates into a dict keeps only the final one, so earlier copies were
    # left at their old values — still present, still loaded, now inconsistent.
    index: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for b in bindings:
        index[(b.get("target_repo"), b.get("maven_artifact"))].append(b)
    _merge_bindings(bindings, index, mergeable)

    # The whole document, so `_comment` — which carries the "never commit"
    # handling notice on a gitignored, sensitivity-marked file — survives.
    document["bindings"] = bindings
    target_path.write_text(
        json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8",
    )
    return len(mergeable)
