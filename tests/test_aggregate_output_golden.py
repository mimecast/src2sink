"""Every rendered aggregate artefact, captured byte for byte.

The safety net for Phase 1 of the 3.0 plan. 14 of 32 aggregators import
`renderers.markdown` and compute-and-write in one step, so there is no computed
result to persist — which is what blocks `OI-15`. Splitting them is mechanical,
and mechanical changes across 14 modules are only safe if something proves the
output did not move.

So this runs the whole aggregation over the synthetic fleet and asserts every
generated file is unchanged, content and all. A split that alters a single
character fails here, and the diff says which file and which line.

Refresh with ``UPDATE_METABASE_SNAPSHOTS=1`` and **read the diff** — an
unexplained change during a refactor that claims to preserve behaviour is the
finding, not an inconvenience.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from src2sink.aggregators.graphs import aggregate_graphs_v2
from src2sink.build_metabase_v2 import analyse_repo_v2, summary_to_dict

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYN = FIXTURES / "synthetic-repos"
SNAPSHOT = FIXTURES / "aggregate-output-golden.json"
UPDATE = os.environ.get("UPDATE_METABASE_SNAPSHOTS", "").lower() in {"1", "true", "yes"}


def _build_metabase(tmp_path: Path) -> list[Path]:
    """Scan every synthetic repo and write its record, returning the record paths."""
    written: list[Path] = []
    for group_dir in sorted(p for p in SYN.iterdir() if p.is_dir()):
        for repo_dir in sorted(p for p in group_dir.iterdir() if p.is_dir()):
            summary = analyse_repo_v2(
                repo_dir, group_dir.name, repo_dir.name,
                f"{group_dir.name}/{repo_dir.name}",
            )
            out = tmp_path / "repos" / group_dir.name
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{repo_dir.name}.json"
            path.write_text(
                json.dumps(summary_to_dict(summary), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            written.append(path)
    return written


# Several artefacts stamp their generation time, which is the one thing about
# them that legitimately differs between two identical runs.
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def _rendered_artefacts(root: Path) -> dict[str, list[str]]:
    """Return every generated artefact's content, keyed by path relative to the root.

    Content rather than a hash: when a refactor does move the output, a diff that
    shows the line is worth far more than one that shows a changed digest.
    """
    out: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".md", ".jsonl"):
            continue
        rel = str(path.relative_to(root))
        if rel.startswith("repos/"):
            continue  # per-repo records are covered by the characterization suite
        text = _TIMESTAMP.sub("<generated>", path.read_text(encoding="utf-8"))
        out[rel] = text.splitlines()
    return out


@pytest.mark.watchdog(120)
def test_aggregate_output_is_unchanged(tmp_path: Path) -> None:
    """Every aggregate artefact, byte for byte.

    This is what makes the compute/render split reviewable: the claim is that
    nothing about the output moves, and the claim is checked rather than asserted.
    """
    repo_jsons = _build_metabase(tmp_path)
    aggregate_graphs_v2(tmp_path, repo_jsons, phase3=True)
    actual = _rendered_artefacts(tmp_path)

    if UPDATE:
        SNAPSHOT.write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        pytest.skip("aggregate output snapshot refreshed")

    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert sorted(actual) == sorted(expected), (
        "the set of generated artefacts changed: "
        f"added {sorted(set(actual) - set(expected))}, "
        f"removed {sorted(set(expected) - set(actual))}"
    )
    for name in sorted(expected):
        assert actual[name] == expected[name], (
            f"{name} changed. If this is intentional, refresh with "
            "UPDATE_METABASE_SNAPSHOTS=1 and review the diff — a refactor that "
            "claims to preserve behaviour should move nothing here."
        )


def test_the_golden_set_is_not_empty() -> None:
    """A harness that captures nothing would pass through any change at all."""
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert len(expected) >= 5, f"only {len(expected)} artefacts captured"
    assert any(name.endswith(".md") for name in expected), "no rendered markdown captured"
