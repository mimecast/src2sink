"""Config-file extractor — JDBC/NoSQL URLs, security keys, crypto-from-config."""

from __future__ import annotations

import re

from .node_factory import make_node
from ..schema import FlowEdge, FlowNode

# v1 security-sensitive keys (properties / YAML flattened keys)
# Flat properties / env style (v1 parity)
SECURITY_CONFIG_KEYS = re.compile(
    r"^\s*(security\.enabled|micronaut\.security\.enabled|"
    r"enable\.serverauth|csrf\.disabled|csrf\.enabled|"
    r"server\.ssl\.enabled|tls\.enabled|verify[-_.]?ssl|"
    r"insecure[_-]?skip[_-]?verify|"
    r"cors\.allowed[\.-]origins|cors\.allow[\.-]?credentials|"
    r"management\.endpoints\.web\.exposure\.include|"
    r"spring\.security\.user\.password|"
    r"jwt\.secret|jwt\.signing[\.-]?key|"
    r"oauth2?\.client[\.-]?secret|"
    r"actuator\.endpoints|"
    r"debug\s*[:=]\s*true)",
    re.IGNORECASE | re.MULTILINE,
)

# YAML / Helm nested keys (same semantics as dotted properties)
YAML_SECURITY_KEYS = re.compile(
    r"^\s*(?:"
    r"password|secret|signing[-_]?algorithm|client[-_]?secret|"
    r"csrf\.(?:enabled|disabled)|"
    r"allowed[-_]?origins|allow[-_]?credentials"
    r")\s*:",
    re.IGNORECASE | re.MULTILINE,
)

JDBC_URL_RX = re.compile(
    r"jdbc:(?:postgresql|mysql|mariadb|oracle|sqlserver|h2|sqlite)[^\"'\s]{8,}",
    re.IGNORECASE,
)

MONGODB_URI_RX = re.compile(
    r"mongodb(?:\+srv)?://[^\"'\s]{8,}",
    re.IGNORECASE,
)

REDIS_URL_RX = re.compile(
    r"rediss?://[^\"'\s]{4,}",
    re.IGNORECASE,
)

S3_BUCKET_RX = re.compile(
    r"(?:s3://([a-z0-9.\-]{3,63})|bucket[-_.]?name\s*[=:]\s*['\"]?([a-z0-9.\-]{3,63}))",
    re.IGNORECASE,
)

CRYPTO_CONFIG_RX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"server\.ssl\.ciphers?\s*[=:]", re.I), "tls-cipher-suite"),
    (re.compile(r"encryption\.algorithm\s*[=:]", re.I), "encryption-algorithm"),
    (re.compile(r"crypto\.algorithm\s*[=:]", re.I), "encryption-algorithm"),
    (re.compile(r"jwt\.signing[-_.]?algorithm\s*[=:]", re.I), "jwt-algorithm"),
    (re.compile(r"spring\.ssl\.bundle", re.I), "tls-bundle"),
    # YAML / Helm nested keys
    (re.compile(r"^\s*ciphers\s*:", re.I | re.M), "tls-cipher-suite"),
    (re.compile(r"^\s*signing[-_]?algorithm\s*:", re.I | re.M), "jwt-algorithm"),
]

SECRET_REF_RX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"aws\.secretsmanager|secretsmanager:", re.I), "aws-secrets-manager"),
    (re.compile(r"vault[:\.]|hashicorp\.vault", re.I), "hashicorp-vault"),
    (re.compile(r"azure\.keyvault|keyvault\.vault", re.I), "azure-key-vault"),
    (re.compile(r"secretmanager\.googleapis|gcp\.secret", re.I), "gcp-secret-manager"),
    (re.compile(r"kms\.key|aws\.kms|alias/", re.I), "aws-kms"),
]

DATASOURCE_KEY_RX = re.compile(
    r"(?:spring\.datasource\.url|datasource\.url|"
    r"quarkus\.datasource\.jdbc\.url|"
    r"micronaut\.datasources\.[^.\s]+\.url)"
    r"\s*[=:]\s*['\"]?([^\"'\n]+)",
    re.IGNORECASE,
)


def _line_of(text: str, pos: int) -> int:
    """Return the 1-based line number of byte offset ``pos`` in ``text``."""
    return text.count("\n", 0, pos) + 1


def _line_slice(text: str, pos: int, end: int) -> str:
    """Return the trimmed full source line spanning ``pos``..``end`` (max 200 chars)."""
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line_start = text.rfind("\n", 0, pos) + 1
    return text[line_start:line_end].strip()[:200]


def extract_from_config(
    *,
    repo_id: str,
    rel_path: str,
    source: str,
) -> tuple[list[FlowNode], list[FlowEdge]]:
    """Extract config-file flow nodes (security keys, datastore URLs, crypto, secrets).

    Scans untrusted config text via regex; matches are only recorded, never evaluated.
    Returns the accumulated nodes and (currently empty) edges.
    """
    nodes: list[FlowNode] = []
    edges: list[FlowEdge] = []

    for m in SECURITY_CONFIG_KEYS.finditer(source):
        key = m.group(1)
        nodes.append(make_node(
            repo=repo_id,
            file=rel_path,
            line=_line_of(source, m.start()),
            language="config",
            kind="propagator",
            family="config-security",
            detail={"key": key, "value_line": _line_slice(source, m.start(), m.end())},
            confidence="high",
        ))

    for m in YAML_SECURITY_KEYS.finditer(source):
        key = m.group(0).strip().split(":")[0].strip()
        nodes.append(make_node(
            repo=repo_id,
            file=rel_path,
            line=_line_of(source, m.start()),
            language="config",
            kind="propagator",
            family="config-security",
            detail={"key": key, "value_line": _line_slice(source, m.start(), m.end())},
            confidence="medium",
        ))

    for m in DATASOURCE_KEY_RX.finditer(source):
        url = m.group(1).strip()
        nodes.append(make_node(
            repo=repo_id,
            file=rel_path,
            line=_line_of(source, m.start()),
            language="config",
            kind="store",
            family="data-store",
            detail={"vendor": "jdbc", "url": url[:200], "source": "datasource-key"},
            confidence="high",
        ))

    for pat, vendor in (
        (JDBC_URL_RX, "jdbc"),
        (MONGODB_URI_RX, "mongodb"),
        (REDIS_URL_RX, "redis"),
    ):
        for m in pat.finditer(source):
            nodes.append(make_node(
                repo=repo_id,
                file=rel_path,
                line=_line_of(source, m.start()),
                language="config",
                kind="store",
                family="data-store",
                detail={"vendor": vendor, "url": m.group(0)[:200], "source": "uri-pattern"},
                confidence="high",
            ))

    for m in S3_BUCKET_RX.finditer(source):
        bucket = m.group(1) or m.group(2) or ""
        if bucket:
            nodes.append(make_node(
                repo=repo_id,
                file=rel_path,
                line=_line_of(source, m.start()),
                language="config",
                kind="store",
                family="data-store",
                detail={"vendor": "s3", "bucket": bucket, "source": "bucket-ref"},
                confidence="medium",
            ))

    for pat, sub in CRYPTO_CONFIG_RX:
        for m in pat.finditer(source):
            nodes.append(make_node(
                repo=repo_id,
                file=rel_path,
                line=_line_of(source, m.start()),
                language="config",
                kind="propagator",
                family="crypto-config",
                detail={"subkind": sub, "line": _line_slice(source, m.start(), m.end())},
                confidence="high",
            ))

    for pat, provider in SECRET_REF_RX:
        for m in pat.finditer(source):
            nodes.append(make_node(
                repo=repo_id,
                file=rel_path,
                line=_line_of(source, m.start()),
                language="config",
                kind="propagator",
                family="crypto-key-source",
                detail={"provider": provider, "from": "config", "raw": m.group(0)[:80]},
                confidence="medium",
            ))

    return nodes, edges


def is_config_path(path_name: str, suffix: str) -> bool:
    """True when a file name/suffix identifies it as a config file for this extractor."""
    from ..constants import CONFIG_FILE_NAMES

    if path_name in CONFIG_FILE_NAMES:
        return True
    if path_name.startswith("application-") and suffix in {".yml", ".yaml", ".properties"}:
        return True
    if suffix in {".properties", ".yml", ".yaml", ".env"}:
        return True
    if path_name in {"values.yaml", "values.yml"} or path_name.endswith(".values.yaml"):
        return True
    return False
