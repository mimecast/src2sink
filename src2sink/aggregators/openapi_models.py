"""Data types for OpenAPI graph ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OpenApiSpec:
    """A discovered OpenAPI/Swagger spec: its owning repo, file, paths and servers."""

    target_repo: str
    spec_path: str
    paths: list[str] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)


@dataclass
class OpenApiInbound:
    """A single inbound OpenAPI route: repo, path, HTTP method and spec file."""

    target_repo: str
    path: str
    method: str
    spec_path: str
