"""Registry of first-party HTTP API client libraries → target service endpoints."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


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
