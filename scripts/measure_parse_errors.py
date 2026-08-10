#!/usr/bin/env python3
"""Measure how many scanned files parse to a tree containing ERROR nodes (`OI-43` step 6).

**A measurement instrument, not a gate.** It is deliberately outside the `ci`
chain: it needs a real fleet on disk, and its output is a number to reason about
rather than a threshold to pass. It exists in `scripts/` so the fleet figures in
`OI-43` can be reproduced rather than quoted.

`OI-43` step 6 asks whether a file whose tree contains an error should be
reported, and at what granularity. Sizing it turned out to need three numbers,
not one:

1. **Prevalence** — what share of files, and of repos? A rare event can carry a
   per-file note; a common one cannot, for the reason step 4 already settled (a
   note on every Scala file in the estate is a signal nobody reads).
2. **Severity** — how much of the file does the error region cover? tree-sitter
   *recovers*: a stray token yields one small ERROR node and the rest of the
   file still parses. Calling that "this file failed to parse" would be wrong in
   the same direction Phase 1 avoided when it split an unparsed manifest from an
   unparsed lockfile.
3. **Where the error lands** — the parent node type. This is the one that
   mattered. The raw count is not one population but two: source that is
   genuinely broken, and *valid* source a grammar cannot accept. Only the parent
   type tells them apart, and a number that merges them cannot be reported to
   anyone.

File selection mirrors ``_scan_repo`` in ``build_metabase_v2`` exactly — the same
walk, size cap, per-repo file cap, prescreen, config-file diversion and extension
table, and the same test-path skip that ``extract_from_file`` applies *before* it
parses. Anything looser puts files in the denominator that no run has ever
parsed; on a sample corpus the test-path skip alone moved it by 36%.

Usage:
  uv run python scripts/measure_parse_errors.py --repos-root ~/fleet --depth 2 -o out.json
  uv run python scripts/measure_parse_errors.py --repo ~/a --repo ~/b -o out.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from tree_sitter import Node, Parser

from src2sink import prescreen
from src2sink.build_metabase_v2 import (
    iter_repo_files,
    language_for_path,
    repo_relpath,
    safe_read_text,
)
from src2sink.constants import MAX_FILE_BYTES
from src2sink.extractors.base import load_language, supported_languages
from src2sink.extractors.config import is_config_path

# Imported rather than reimplemented, private name and all: the measurement's
# only claim is that its denominator is production's, and a second copy of this
# predicate would be free to drift out of agreement with the first.
from src2sink.extractors.unified import _is_test_path
from src2sink.limits import DEFAULT_MAX_FILES_PER_REPO

# Share-of-file-covered-by-errors buckets. Open at the top (1.01) so a file that
# is entirely one ERROR node lands somewhere rather than being dropped.
BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.001),
    (0.001, 0.01),
    (0.01, 0.10),
    (0.10, 0.50),
    (0.50, 1.01),
)

_PARSERS: dict[str, Parser] = {}


def parser_for(language_id: str) -> Parser:
    """Return a per-process cached parser for ``language_id``.

    Production builds a fresh parser per file. Same grammar, same tree — this
    only avoids re-loading the language once per file across a fleet-sized run.
    """
    if language_id not in _PARSERS:
        _PARSERS[language_id] = Parser(load_language(language_id))
    return _PARSERS[language_id]


def error_regions(root: Node) -> tuple[int, int, int, int, Counter[str]]:
    """Walk a tree once, returning (errors, missing, error_bytes, max_span, contexts).

    ``error_bytes`` sums only *outermost* ERROR nodes; descending into one and
    adding its children would count the same source twice. ``max_span`` is the
    largest single contiguous error region, which is what separates "one bad
    token" from "the grammar gave up at line 3".

    A MISSING node is counted apart from an ERROR node because it covers **zero
    bytes** — it is a token tree-sitter inserted to recover, so it contributes
    nothing to ``error_bytes`` and a severity measure based on bytes alone would
    score a MISSING-only file 0.0 and read as untouched.
    """
    n_error = n_missing = error_bytes = max_span = 0
    contexts: Counter[str] = Counter()
    stack = [root]
    while stack:
        node = stack.pop()
        parent = node.parent.type if node.parent else "<root>"
        if node.is_missing:
            n_missing += 1
            contexts[f"missing/{parent}"] += 1
            continue
        if node.type == "ERROR":
            span = node.end_byte - node.start_byte
            n_error += 1
            error_bytes += span
            max_span = max(max_span, span)
            contexts[f"error/{parent}"] += 1
            continue
        if node.has_error:
            stack.extend(node.children)
    return n_error, n_missing, error_bytes, max_span, contexts


def scan_repo(target: tuple[str, str]) -> dict[str, Any]:
    """Parse every file one repo's scan would parse, and report its ERROR trees."""
    repo_id, repo_root_s = target
    repo_root = Path(repo_root_s)
    grammars = supported_languages()

    parsed: Counter[str] = Counter()
    errored: Counter[str] = Counter()
    no_grammar: Counter[str] = Counter()
    test_paths: Counter[str] = Counter()
    bytes_parsed: Counter[str] = Counter()
    bytes_errored: Counter[str] = Counter()
    contexts: Counter[str] = Counter()
    files: list[dict[str, Any]] = []

    scanned = 0
    for path in iter_repo_files(repo_root):
        # The cap counts every walked file, not only the ones with a source
        # extension — counting differently here would give a denominator the
        # real run never had.
        if DEFAULT_MAX_FILES_PER_REPO and scanned >= DEFAULT_MAX_FILES_PER_REPO:
            break
        scanned += 1
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if MAX_FILE_BYTES and size > MAX_FILE_BYTES:
            continue
        text = safe_read_text(path)
        if not text:
            continue
        if prescreen.screen(path, text):
            continue
        if is_config_path(path.name, path.suffix.lower()):
            continue
        lang = language_for_path(path)
        if not lang:
            continue
        rel = repo_relpath(repo_root, path)
        if _is_test_path(rel):
            test_paths[lang] += 1
            continue
        if lang not in grammars:
            # Scala today: no grammar at all, already reported by `_parse_or_note`
            # as a hard parse failure. Counted apart so it cannot inflate the
            # ERROR-tree number, which is about grammars that *do* run.
            no_grammar[lang] += 1
            continue

        src = text.encode("utf-8")
        try:
            tree = parser_for(lang).parse(src)
        except (KeyError, OSError, ValueError):
            no_grammar[lang] += 1
            continue

        parsed[lang] += 1
        bytes_parsed[lang] += len(src)
        if not tree.root_node.has_error:
            continue

        n_error, n_missing, error_bytes, max_span, ctx = error_regions(tree.root_node)
        errored[lang] += 1
        bytes_errored[lang] += error_bytes
        contexts.update({f"{lang}:{k}": v for k, v in ctx.items()})
        files.append(
            {
                "repo": repo_id,
                "path": rel,
                "language": lang,
                "bytes": len(src),
                "error_nodes": n_error,
                "missing_nodes": n_missing,
                "error_bytes": error_bytes,
                "max_error_span": max_span,
                "error_ratio": round(error_bytes / len(src), 4) if src else 0.0,
            }
        )

    return {
        "repo": repo_id,
        "parsed": dict(parsed),
        "errored": dict(errored),
        "no_grammar": dict(no_grammar),
        "test_paths": dict(test_paths),
        "bytes_parsed": dict(bytes_parsed),
        "bytes_errored": dict(bytes_errored),
        "contexts": dict(contexts),
        "files": files,
    }


def discover(repos_root: Path, depth: int) -> list[tuple[str, str]]:
    """Return (repo_id, path) for every repo ``depth`` levels under ``repos_root``."""
    out: list[tuple[str, str]] = []
    if depth == 1:
        for entry in sorted(repos_root.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                out.append((entry.name, str(entry)))
        return out
    for group in sorted(repos_root.iterdir()):
        if not group.is_dir() or group.name.startswith("."):
            continue
        for repo in sorted(group.iterdir()):
            if repo.is_dir() and not repo.name.startswith("."):
                out.append((f"{group.name}/{repo.name}", str(repo)))
    return out


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-repo results into the three numbers the decision needs."""
    parsed: Counter[str] = Counter()
    errored: Counter[str] = Counter()
    no_grammar: Counter[str] = Counter()
    test_paths: Counter[str] = Counter()
    contexts: Counter[str] = Counter()
    buckets: Counter[str] = Counter()
    repos_with_error: set[str] = set()
    per_repo_error: dict[str, int] = defaultdict(int)
    all_files: list[dict[str, Any]] = []
    missing_only = 0

    for result in results:
        parsed.update(result["parsed"])
        errored.update(result["errored"])
        no_grammar.update(result["no_grammar"])
        test_paths.update(result["test_paths"])
        contexts.update(result["contexts"])
        if result["files"]:
            repos_with_error.add(result["repo"])
        per_repo_error[result["repo"]] = len(result["files"])
        all_files.extend(result["files"])

    for entry in all_files:
        if entry["error_nodes"] == 0 and entry["missing_nodes"] > 0:
            missing_only += 1
        for low, high in BUCKETS:
            if low <= entry["error_ratio"] < high:
                buckets[f"{low:.3f}-{high:.3f}"] += 1
                break

    total_parsed = sum(parsed.values())
    total_errored = sum(errored.values())
    return {
        "repos": len(results),
        "repos_with_at_least_one_error_tree": len(repos_with_error),
        "files_parsed": total_parsed,
        "files_with_error_tree": total_errored,
        "pct_files_with_error_tree": (
            round(100 * total_errored / total_parsed, 2) if total_parsed else 0.0
        ),
        "files_without_grammar": dict(no_grammar),
        "files_skipped_as_test_paths": dict(test_paths),
        "files_with_missing_nodes_only": missing_only,
        "by_language": {
            lang: {
                "parsed": parsed[lang],
                "errored": errored[lang],
                "pct": round(100 * errored[lang] / parsed[lang], 2) if parsed[lang] else 0.0,
            }
            for lang in sorted(parsed)
        },
        "error_contexts": dict(contexts.most_common()),
        "error_ratio_buckets": dict(buckets),
        "worst_repos": sorted(
            ((name, count) for name, count in per_repo_error.items() if count),
            key=lambda kv: -kv[1],
        )[:20],
        "worst_files": sorted(all_files, key=lambda f: -float(f["error_ratio"]))[:20],
    }


def report(summary: dict[str, Any]) -> None:
    """Print the summary as a table."""
    print()
    print(f"repos scanned                 {summary['repos']}")
    print(f"repos with >=1 ERROR tree     {summary['repos_with_at_least_one_error_tree']}")
    print(f"files parsed                  {summary['files_parsed']}")
    print(
        f"files with an ERROR tree      {summary['files_with_error_tree']} "
        f"({summary['pct_files_with_error_tree']}%)"
    )
    print(f"files with no grammar         {summary['files_without_grammar']}")
    print(
        f"files skipped as test paths   "
        f"{sum(summary['files_skipped_as_test_paths'].values())} (never parsed by production)"
    )
    print()
    print(f"{'language':12} {'parsed':>9} {'errored':>9} {'pct':>7}")
    for lang, row in summary["by_language"].items():
        print(f"{lang:12} {row['parsed']:9} {row['errored']:9} {row['pct']:6.2f}%")
    print()
    print(
        f"of those, MISSING-node only   {summary['files_with_missing_nodes_only']} "
        "(zero-width; an inserted token, not a lost region)"
    )
    print()
    print("error_ratio buckets (share of file covered by ERROR nodes):")
    for name, count in sorted(summary["error_ratio_buckets"].items()):
        print(f"  {name:14} {count}")
    print()
    print("where the errors land (language:kind/parent-node):")
    for name, count in list(summary["error_contexts"].items())[:20]:
        print(f"  {name:44} {count}")


def main() -> int:
    """CLI entry point: scan the given repos and write the parse-error measurement."""
    ap = argparse.ArgumentParser(description="Measure ERROR trees across a fleet (OI-43 step 6).")
    ap.add_argument("--repos-root", type=Path, help="Fleet root to discover repos under.")
    ap.add_argument(
        "--depth", type=int, default=2, choices=(1, 2), help="Repo nesting under --repos-root."
    )
    ap.add_argument(
        "--repo", type=Path, action="append", default=[], help="An individual repo (repeatable)."
    )
    ap.add_argument("-o", "--output", type=Path, required=True, help="Where to write the JSON.")
    ap.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 4) - 2),
        help="Worker processes.",
    )
    args = ap.parse_args()

    targets: list[tuple[str, str]] = [(p.name, str(p)) for p in args.repo]
    if args.repos_root:
        targets.extend(discover(args.repos_root.expanduser(), args.depth))
    if not targets:
        raise SystemExit("nothing to scan: pass --repo or --repos-root")

    print(f"scanning {len(targets)} repos on {args.jobs} workers")
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for done, result in enumerate(pool.map(scan_repo, targets), 1):
            results.append(result)
            if done % 25 == 0 or done == len(targets):
                print(f"  {done}/{len(targets)}")

    summary = summarise(results)
    args.output.write_text(
        json.dumps({"summary": summary, "repos": results}, indent=2), encoding="utf-8"
    )
    report(summary)
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
