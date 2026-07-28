"""Shared helpers for v2 graph aggregators and trace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from .schema import SCHEMA_VERSION

# Hosts that are not useful service-graph targets (v1 parity).
SERVICE_GRAPH_NOISE_HOSTS = frozenset({
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


def load_v2_repo_records(
    metabase_root: Path,
    *,
    json_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """Load v2 metabase repo records, skipping unreadable or mismatched-schema files."""
    paths = json_paths or sorted(metabase_root.glob("repos/*/*.json"))
    records: list[dict[str, Any]] = []
    for jp in paths:
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema_version") != SCHEMA_VERSION:
            continue
        data["_json_path"] = str(jp)
        records.append(data)
    return records


def repo_id(data: dict[str, Any]) -> str:
    """Return the ``group/name`` repo id for a record."""
    return f"{data['group']}/{data['name']}"


def iter_nodes(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield each node of a record tagged with its owning repo id."""
    rid = repo_id(data)
    for node in data.get("nodes", []):
        yield {**node, "repo": rid}


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


def path_templates_match(outbound: str, inbound: str) -> str | None:
    """Return confidence label if templates align, else None."""
    o = normalize_path_template(outbound)
    i = normalize_path_template(inbound)
    if not o or not i:
        return None
    if o == i:
        return "high"
    if o.startswith(i + "/") or i.startswith(o + "/"):
        return "medium"
    # segment overlap: /api/v1/queries vs /queries
    o_parts = [s for s in o.split("/") if s]
    i_parts = [s for s in i.split("/") if s]
    if len(o_parts) >= 2 and len(i_parts) >= 1 and o_parts[-len(i_parts):] == i_parts:
        return "low"
    return None


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
    """Map service-name aliases (lowercase) to ``group/name`` repo ids."""
    mapping: dict[str, str] = {}
    for data in records:
        rid = repo_id(data)
        mapping[data["name"].lower()] = rid
        for alias in repo_name_aliases(data["name"]):
            mapping[alias] = rid
    return mapping


def resolve_repo_for_host(host: str, alias_to_repo: dict[str, str]) -> str | None:
    """Best-effort repo id from an outbound hostname."""
    if host in SERVICE_GRAPH_NOISE_HOSTS:
        return None
    tgt = alias_to_repo.get(host.split(".")[0])
    if tgt:
        return tgt
    for _alias, rid in alias_to_repo.items():
        if host_matches_repo(host, rid):
            return rid
    return None


def match_path_in_inbound_index(
    path: str,
    inbound: dict[str, list[tuple[Any, ...]]],
    *,
    inbound_path_col: int = 1,
) -> tuple[list[tuple[Any, ...]], str]:
    """Match outbound path to indexed inbound rows; returns (rows, confidence)."""
    norm = normalize_path_template(path)
    targets = inbound.get(norm, [])
    conf = "high"
    if targets:
        return targets, conf
    for _in_norm, rows in inbound.items():
        if not rows:
            continue
        row = rows[0]
        candidate = row[inbound_path_col] if len(row) > inbound_path_col else ""
        matched = path_templates_match(path, str(candidate))
        if matched:
            return rows, matched
    return [], conf


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
