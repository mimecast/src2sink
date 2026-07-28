"""Phase 1 config extractor tests."""

from __future__ import annotations

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
