"""Registry of first-party HTTP API client libraries → target service endpoints."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class ApiClientConfigError(RuntimeError):
    """Raised when an ``--api-clients`` path yields no usable bindings.

    Silently continuing with zero bindings disables every cross-repo client
    detection path (import nodes, class-pattern call sites, producer index) while
    still reporting a successful run, so ~all client-library callers vanish with
    no negative signal. See ADR-011 in docs/architecture.md.
    """


@dataclass(frozen=True)
class ApiClientBinding:
    """Maps a published client coordinate / import prefix to a target service."""

    target_repo: str
    maven_artifact: str
    import_prefix: str
    paths: tuple[str, ...]
    payload_fields: tuple[str, ...] = ("sql",)
    service_aliases: tuple[str, ...] = ()
    class_patterns: tuple[str, ...] = ()


_BINDINGS: tuple[ApiClientBinding, ...] = ()


def configure_api_client_bindings(bindings: tuple[ApiClientBinding, ...]) -> None:
    """Replace the module-level bindings used by get_bindings()."""
    global _BINDINGS
    _BINDINGS = bindings


def get_bindings() -> tuple[ApiClientBinding, ...]:
    """Return the currently configured API client bindings."""
    return _BINDINGS


def load_api_client_bindings(
    path: Path, *, warn: bool = False
) -> tuple[ApiClientBinding, ...]:
    """Load ApiClientBinding entries from a JSON file.

    Returns an empty tuple on a missing or invalid file — never raises. When
    ``warn`` is true (use only for the single top-level load, not per-worker),
    a misconfiguration is surfaced as a WARNING and a successful load logs its
    count. Log messages carry only the filename and exception *type* — never the
    file's contents or full path — so a sensitive config never leaks to CI logs
    (see threat-model I-2 / I-3).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if warn:
            logger.warning(
                "api-clients file %r could not be read (%s); 0 bindings loaded",
                path.name,
                type(exc).__name__,
            )
        return ()
    if not isinstance(data, dict) or not isinstance(data.get("bindings"), list):
        if warn:
            logger.warning(
                "api-clients file %r has no 'bindings' list; 0 bindings loaded",
                path.name,
            )
        return ()
    raw_bindings = data["bindings"]
    result: list[ApiClientBinding] = []
    for entry in raw_bindings:
        if not isinstance(entry, dict):
            continue
        try:
            result.append(ApiClientBinding(
                target_repo=str(entry.get("target_repo", "")),
                maven_artifact=str(entry.get("maven_artifact", "")),
                import_prefix=str(entry.get("import_prefix", "")),
                paths=tuple(entry.get("paths", [])),
                payload_fields=tuple(entry.get("payload_fields", ("sql",))),
                service_aliases=tuple(entry.get("service_aliases", [])),
                class_patterns=tuple(entry.get("class_patterns", [])),
            ))
        except (TypeError, ValueError):
            continue
    if warn:
        logger.info("loaded %d api-client binding(s) from %r", len(result), path.name)
    return tuple(result)


def configure_from_path(
    path: str | Path,
    *,
    warn: bool = False,
    allow_empty: bool = False,
) -> tuple[ApiClientBinding, ...]:
    """Load bindings from ``path`` and configure every consumer that needs them.

    Single entry point for the ``--api-clients`` flag so no CLI can configure the
    binding registry while forgetting the http-out class patterns (or vice versa).

    Raises:
        ApiClientConfigError: if the file yields no bindings and ``allow_empty``
            is false — a misconfigured file must not look like a clean run.
    """
    from .extractors.http_out import configure_http_out_client_patterns

    p = Path(path)
    bindings = load_api_client_bindings(p, warn=warn)
    if not bindings and not allow_empty:
        raise ApiClientConfigError(
            f"api-clients file {p.name!r} loaded 0 bindings (missing, unreadable, "
            "or malformed). Cross-repo API-client detection would be silently "
            "disabled. Fix the file, or pass --allow-empty-api-clients to accept "
            "the reduced coverage."
        )
    configure_api_client_bindings(bindings)
    configure_http_out_client_patterns(bindings)
    return bindings


def _alias_variants(alias: str) -> set[str]:
    """Return lowercase spelling variants of a service alias.

    A binding alias is a DNS-ish service name (``some-thing-service``) but the
    same service is referred to in code as ``some_thing_service``,
    ``somethingservice`` or, with the ``-service`` suffix dropped, ``some_thing``
    (as in ``get_some_thing_base_url()``). Matching every variant is what lets a
    base-URL helper or config key resolve to the target repo.
    """
    base = alias.strip().lower()
    if not base:
        return set()
    variants = {base}
    stems = {base}
    if base.endswith("-service"):
        stems.add(base[: -len("-service")])
    for stem in stems:
        variants.update({stem, stem.replace("-", "_"), stem.replace("-", "")})
    return {v for v in variants if len(v) >= 4}


def binding_alias_index() -> dict[str, str]:
    """Map every configured binding's service-alias variants to its target repo id."""
    mapping: dict[str, str] = {}
    for binding in _BINDINGS:
        if not binding.target_repo:
            continue
        for alias in binding.service_aliases:
            for variant in _alias_variants(alias):
                mapping.setdefault(variant, binding.target_repo)
    return mapping


# (bindings identity, combined alias regex, alias -> target repo). Rebuilt only
# when configure_api_client_bindings swaps the registry, so the per-call-site
# lookup below stays a single regex search rather than one per alias.
_ALIAS_MATCHER_CACHE: tuple[
    tuple[ApiClientBinding, ...], re.Pattern[str] | None, dict[str, str]
] | None = None


def _alias_matcher() -> tuple[re.Pattern[str] | None, dict[str, str]]:
    """Return the cached (combined alias regex, alias -> target repo) pair."""
    global _ALIAS_MATCHER_CACHE  # noqa: PLW0603
    cached = _ALIAS_MATCHER_CACHE
    if cached is not None and cached[0] is _BINDINGS:
        return cached[1], cached[2]
    index = binding_alias_index()
    rx: re.Pattern[str] | None = None
    if index:
        alt = "|".join(re.escape(v) for v in sorted(index, key=len, reverse=True))
        rx = re.compile(rf"(?<![a-z0-9])({alt})(?![a-z0-9])")
    _ALIAS_MATCHER_CACHE = (_BINDINGS, rx, index)
    return rx, index


def binding_target_for_text(text: str) -> tuple[str, str] | None:
    """Return (target_repo, matched_alias) if a binding's service alias appears in ``text``.

    Used to resolve the *service* behind an otherwise-opaque call site — a
    base-URL helper name (``get_some_service_base_url``) or an injected config key —
    to a target repo.
    """
    if not text:
        return None
    rx, index = _alias_matcher()
    if rx is None:
        return None
    m = rx.search(text.lower())
    if not m:
        return None
    return index[m.group(1)], m.group(1)


def binding_for_import(line: str) -> ApiClientBinding | None:
    """Return the configured binding whose import prefix appears in an import line, if any."""
    stripped = line.strip()
    if not stripped.startswith("import ") and "import " not in stripped:
        return None
    for binding in _BINDINGS:
        if binding.import_prefix in stripped:
            return binding
    return None


def binding_for_coordinate(group_id: str, artifact_id: str) -> ApiClientBinding | None:
    """Return the configured binding whose Maven artifact matches this coordinate, if any."""
    coord = f"{group_id}:{artifact_id}"
    for binding in _BINDINGS:
        if binding.maven_artifact in artifact_id or binding.maven_artifact in coord:
            return binding
    return None
