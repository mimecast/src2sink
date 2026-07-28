"""Write OpenAPI / Helm discovery artefacts under metabase/graphs/."""

from __future__ import annotations

import json
from pathlib import Path

from ..graph_common import build_repo_alias_index, load_v2_repo_records
from ..renderers.markdown import md_table

from .openapi_discovery import OPENAPI_GLOBS, discover_helm_hosts, discover_openapi_specs
from .openapi_match import build_openapi_inbound_index, match_http_out_to_openapi
from .openapi_models import OpenApiSpec


def write_openapi_artifacts(
    metabase_root: Path,
    repos_root: Path | None,
    repo_jsons: list[Path] | None = None,
) -> list[OpenApiSpec]:
    """Discover specs/Helm hosts, write JSONL + markdown artefacts, return the specs."""
    graphs_dir = metabase_root / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    specs: list[OpenApiSpec] = []
    if repos_root and repos_root.is_dir():
        specs = discover_openapi_specs(repos_root)

    jsonl_path = graphs_dir / "openapi-specs.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for spec in specs:
            fh.write(json.dumps({
                "target_repo": spec.target_repo,
                "spec_path": spec.spec_path,
                "paths": spec.paths,
                "servers": spec.servers,
            }, ensure_ascii=False) + "\n")

    inbound = build_openapi_inbound_index(specs)
    openapi_edges: list[dict[str, object]] = []
    if repo_jsons and inbound:
        records = load_v2_repo_records(metabase_root, json_paths=repo_jsons)
        alias_to_repo = build_repo_alias_index(records)
        openapi_edges = match_http_out_to_openapi(records, inbound, alias_to_repo)

    edges_path = graphs_dir / "openapi-service-edges.jsonl"
    with edges_path.open("w", encoding="utf-8") as fh:
        for row in openapi_edges:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    helm_hosts = discover_helm_hosts(repos_root) if repos_root else []
    helm_path = graphs_dir / "helm-service-hosts.jsonl"
    with helm_path.open("w", encoding="utf-8") as fh:
        for row in helm_hosts:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    md: list[str] = [
        "# OpenAPI / Swagger specs (discovered)\n",
        f"_Scanned under `repos/` for `{'`, `'.join(OPENAPI_GLOBS)}`._\n",
        f"\n**Specs:** {len(specs)}. **Cross-repo edges (openapi confidence):** "
        f"{len(openapi_edges)}. **Helm host hints:** {len(helm_hosts)}.\n",
        "\n## Specs by service\n",
        md_table(
            ["Target repo", "Path count", "Spec file"],
            [
                [spec.target_repo, str(len(spec.paths)), spec.spec_path]
                for spec in sorted(specs, key=lambda s: -len(s.paths))[:80]
            ],
        ),
    ]
    if len(specs) > 80:
        md.append(f"\n_{len(specs) - 80} more in `openapi-specs.jsonl`._\n")

    (graphs_dir / "openapi-specs.md").write_text("\n".join(md), encoding="utf-8")
    return specs
