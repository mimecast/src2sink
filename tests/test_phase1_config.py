"""Phase 1 config extractor tests."""

from __future__ import annotations

import pytest

from src2sink.extractors.config import extract_from_config

YAML_SAMPLE = """
spring:
  datasource:
    url: jdbc:postgresql://analytics-prod:5432/warehouse
  security:
    user:
      password: changeme
jwt:
  secret: ${JWT_SECRET}
  signing-algorithm: HS256
server:
  ssl:
    ciphers: TLS_AES_256_GCM_SHA384
"""

PROPS_SAMPLE = """
mongodb.uri=mongodb+srv://cluster.example.net/mydb
redis.url=rediss://cache.internal:6379
aws.secretsmanager.secret-name=app/db
"""


def test_config_jdbc_and_security_keys() -> None:
    nodes, _ = extract_from_config(
        repo_id="test/app",
        rel_path="src/main/resources/application.yml",
        source=YAML_SAMPLE,
    )
    families = {n.family for n in nodes}
    assert "data-store" in families
    assert "config-security" in families
    assert "crypto-config" in families
    jdbc = [n for n in nodes if n.family == "data-store" and n.detail.get("vendor") == "jdbc"]
    assert jdbc
    assert "postgresql" in jdbc[0].detail.get("url", "")


def test_config_mongo_redis_secrets() -> None:
    nodes, _ = extract_from_config(
        repo_id="test/app",
        rel_path="config/application.properties",
        source=PROPS_SAMPLE,
    )
    vendors = {n.detail.get("vendor") for n in nodes if n.family == "data-store"}
    assert "mongodb" in vendors
    assert "redis" in vendors
    assert any(n.family == "crypto-key-source" for n in nodes)


# ---------------------------------------------------------------------------
# is_config_path — the gate deciding what gets config extraction at all
#
# 28 surviving mutants and no direct test. Everything downstream of this
# function — data-store URLs, base URLs, credential-shaped keys — is only found
# in files it says yes to, so a wrong answer here removes whole classes of
# finding silently.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "suffix", "expected"),
    [
        # Spring's conventional names and their profile variants.
        ("application.yml", ".yml", True),
        ("application.yaml", ".yaml", True),
        ("application.properties", ".properties", True),
        ("bootstrap.yml", ".yml", True),
        ("application-prod.yml", ".yml", True),
        ("application-local.properties", ".properties", True),
        # Any file with a config-ish suffix, wherever it lives.
        ("anything.yml", ".yml", True),
        ("anything.yaml", ".yaml", True),
        ("db.properties", ".properties", True),
        (".env", ".env", True),
        # Helm values.
        ("values.yaml", ".yaml", True),
        ("prod.values.yaml", ".yaml", True),
        # Not config: source, docs, and formats this extractor cannot read.
        ("StockDao.java", ".java", False),
        ("README.md", ".md", False),
        ("package.json", ".json", False),
        ("Dockerfile", "", False),
        ("application.toml", ".toml", False),
    ],
)
def test_is_config_path(name: str, suffix: str, expected: bool) -> None:
    """Which files reach the config extractor, stated case by case."""
    from src2sink.extractors.config import is_config_path

    assert is_config_path(name, suffix) is expected


def test_config_suffixes_are_the_deciding_rule() -> None:
    """The suffix set is what the behaviour above rests on.

    Every other branch in `is_config_path` is subsumed by it — every name in
    CONFIG_FILE_NAMES ends in one of these suffixes, as do `values.yaml` and
    `application-*.yml`. Asserting the set directly is what makes a change to it
    visible, since a redundant branch cannot fail a test.
    """
    from src2sink.extractors.config import _CONFIG_SUFFIXES

    assert _CONFIG_SUFFIXES == frozenset({".properties", ".yml", ".yaml", ".env"})
