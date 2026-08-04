#!/usr/bin/env python3
"""Batch flow traces for raw-code-payload catalogue entries (Phase 2 polish).

Reads `taint/raw-code-payload-endpoints.jsonl` and writes one markdown report
per unique (repo, endpoint_path) under `graphs/traces/`.

Examples:
  uv run python metabase/scripts/trace_batch.py --metabase-root metabase

  uv run python metabase/scripts/trace_batch.py --metabase-root metabase \\
    --limit 10 --skip-existing

  uv run python metabase/scripts/trace_batch.py --metabase-root metabase \\
    --scan-repos repos --limit 50
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .aggregators.payload_producers import build_producer_indices
from .aggregators.service_calls import collect_service_edges
from .graph_common import load_v2_repo_records
from .internal_groups import (
    add_internal_groups_arguments,
    apply_internal_groups_from_args,
)
from .known_api_clients import ApiClientConfigError, configure_from_path
from .trace import render_trace_markdown, run_trace

INVALID_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_slug(repo: str, endpoint_path: str) -> str:
    """Build a filesystem-safe output filename stem from a repo id and endpoint path."""
    repo_part = repo.replace("/", "-")
    path_part = endpoint_path.strip("/").replace("/", "-") or "root"
    path_part = INVALID_PATH_CHARS.sub("-", path_part)
    return f"{repo_part}-{path_part}"


def load_catalogue_targets(
    metabase_root: Path,
) -> list[tuple[str, str]]:
    """Return sorted, deduplicated (repo, endpoint_path) pairs from the raw-code-payload catalogue."""
    jsonl = metabase_root / "taint" / "raw-code-payload-endpoints.jsonl"
    if not jsonl.is_file():
        raise SystemExit(
            f"Catalogue not found: {jsonl}. Run build_metabase_v2.py "
            "(aggregate) first.",
        )
    seen: set[tuple[str, str]] = set()
    targets: list[tuple[str, str]] = []
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            repo = rec.get("repo", "")
            path = (rec.get("detail") or {}).get("endpoint_path", "")
            if not repo or not path:
                continue
            key = (repo, path)
            if key in seen:
                continue
            seen.add(key)
            targets.append(key)
    return sorted(targets)


def batch_trace(
    metabase_root: Path,
    *,
    limit: int = 0,
    skip_existing: bool = False,
    scan_repos: Path | None = None,
) -> tuple[int, int, int]:
    """Render a trace markdown report per catalogue target; return (written, skipped, errors)."""
    targets = load_catalogue_targets(metabase_root)
    if limit > 0:
        targets = targets[:limit]

    out_dir = metabase_root / "graphs" / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)

    # All three are fleet-wide and target-independent, so they are built once
    # here rather than per target. The service-call graph in particular is the
    # dominant cost of a trace, and rebuilding it per target made a batch
    # quadratic in fleet size (OI-14).
    records = load_v2_repo_records(metabase_root)
    producer_indices = build_producer_indices(
        metabase_root,
        repos_root=scan_repos,
    )
    service_edges, _unmatched = collect_service_edges(records)

    written = 0
    skipped = 0
    errors = 0

    for repo, path in targets:
        out_path = out_dir / f"{_safe_slug(repo, path)}.md"
        if skip_existing and out_path.is_file():
            skipped += 1
            continue
        try:
            report = run_trace(
                metabase_root,
                repo,
                path_filter=path,
                repos_root=scan_repos,
                scan_repos=scan_repos is not None,
                records=records,
                producer_indices=producer_indices,
                service_edges=service_edges,
            )
            out_path.write_text(render_trace_markdown(report), encoding="utf-8")
            written += 1
            print(f"  wrote {out_path.name}")
        except SystemExit:
            errors += 1
            print(f"  skip {repo} {path} (repo not in metabase)", file=sys.stderr)
        except Exception as exc:  # pragma: no cover
            errors += 1
            print(f"  error {repo} {path}: {exc}", file=sys.stderr)

    return written, skipped, errors


def main() -> int:
    """CLI entry point: parse args, configure bindings, and run the batch trace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metabase-root", required=True)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max catalogue entries to trace (0 = all)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not overwrite existing trace markdown files",
    )
    parser.add_argument(
        "--scan-repos",
        nargs="?",
        const="repos",
        default=None,
        metavar="REPOS_ROOT",
        help="Pass through to trace.py for literal repo scan (slow)",
    )
    parser.add_argument("--api-clients", default=None, help="Path to api-clients.json")
    parser.add_argument(
        "--allow-empty-api-clients",
        action="store_true",
        help="Continue when --api-clients loads 0 bindings (otherwise a hard "
        "error, since it silently disables cross-repo API-client detection).",
    )
    add_internal_groups_arguments(parser)
    args = parser.parse_args()

    metabase_root = Path(args.metabase_root).resolve()
    repos_root = Path(args.scan_repos).resolve() if args.scan_repos else None

    apply_internal_groups_from_args(args)

    if args.api_clients:
        try:
            configure_from_path(
                args.api_clients,
                warn=True,
                allow_empty=args.allow_empty_api_clients,
            )
        except ApiClientConfigError as exc:
            raise SystemExit(f"ERROR: {exc}") from exc

    written, skipped, errors = batch_trace(
        metabase_root,
        limit=args.limit,
        skip_existing=args.skip_existing,
        scan_repos=repos_root,
    )
    print(
        f"Batch trace done: wrote={written} skipped={skipped} errors={errors}",
    )
    return 1 if errors and not written else 0


if __name__ == "__main__":
    raise SystemExit(main())
