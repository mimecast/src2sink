#!/usr/bin/env python3
"""Complexity ratchet — existing hot spots are recorded, new ones are refused.

Two metrics, because they disagree and each catches what the other misses:

* **cyclomatic** counts independent paths — how many cases a test suite must
  cover to exercise the function;
* **cognitive** weights nesting — how hard the function is to hold in your head.

`write_ropa_view` scores cyclomatic 15 / cognitive 11; `collect_pii_touchpoints`
scores cyclomatic 8 / cognitive 26. A single threshold would wave one of them
through.

**This is a ratchet, not a cliff.** The 39 functions already over the line cannot
be refactored in one change, and a gate that fails on day one gets switched off
rather than obeyed. So current offenders are frozen in `ALLOWLIST` with their
scores; the gate fails when a function exceeds the threshold *and* is not
allowlisted, or when an allowlisted function gets **worse**. Improving a function
below its recorded score is expected — run with `--update` to re-freeze, which is
the only supported way to change the file and always shrinks it.

Usage:
    python scripts/complexity_check.py            # exits 1 on a new or worsened hot spot
    python scripts/complexity_check.py --update   # re-freeze after improving something
    python scripts/complexity_check.py --summary $GITHUB_STEP_SUMMARY
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "src2sink"
ALLOWLIST_PATH = Path(__file__).resolve().parent / "complexity-allowlist.json"

# Above these, a function needs either a refactor or a line in the allowlist.
MAX_CYCLOMATIC = 10
MAX_COGNITIVE = 15


def cyclomatic(fn: ast.AST) -> int:
    """Count independent paths through a function (McCabe)."""
    score = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            score += 1 + len(node.ifs)
    return score


def cognitive(fn: ast.AST) -> int:
    """Score how hard a function is to follow, weighting nested control flow.

    Sonar's formulation: each branch costs one point plus one for every level of
    nesting it sits inside, so three nested ifs cost more than three sequential
    ones even though both have the same cyclomatic complexity.
    """
    score = 0

    def walk(node: ast.AST, nesting: int) -> None:
        nonlocal score
        for child in ast.iter_child_nodes(node):
            increases_nesting = False
            if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.IfExp)):
                score += 1 + nesting
                increases_nesting = True
            elif isinstance(child, ast.BoolOp):
                score += 1
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and child is not fn:
                increases_nesting = True
            walk(child, nesting + 1 if increases_nesting else nesting)

    walk(fn, 0)
    return score


def measure() -> dict[str, dict[str, int]]:
    """Return ``{"path:function": {"cyclomatic": n, "cognitive": n}}`` for hot spots."""
    out: dict[str, dict[str, int]] = {}
    for path in sorted(SOURCE_DIR.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cyc, cog = cyclomatic(node), cognitive(node)
            if cyc > MAX_CYCLOMATIC or cog > MAX_COGNITIVE:
                out[f"{rel}:{node.name}"] = {"cyclomatic": cyc, "cognitive": cog}
    return out


def _load_allowlist() -> dict[str, dict[str, int]]:
    """Return the frozen scores, or {} when the file is absent."""
    try:
        data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("frozen", {})
    return entries if isinstance(entries, dict) else {}


def main() -> int:
    """Compare measured complexity against the frozen allowlist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="re-freeze the allowlist")
    parser.add_argument("--summary", help="append a markdown summary to this file")
    args = parser.parse_args()

    measured = measure()
    allow = _load_allowlist()

    if args.update:
        ALLOWLIST_PATH.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Complexity hot spots frozen at their current scores. This file may "
                        "only shrink: refactor a function below its recorded score and re-run "
                        "with --update. Adding an entry by hand to silence the gate defeats it."
                    ),
                    "max_cyclomatic": MAX_CYCLOMATIC,
                    "max_cognitive": MAX_COGNITIVE,
                    "frozen": dict(sorted(measured.items())),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Froze {len(measured)} hot spot(s).")
        return 0

    new: list[str] = []
    worse: list[str] = []
    for name, scores in sorted(measured.items()):
        frozen = allow.get(name)
        if frozen is None:
            new.append(f"  {name}  cyclomatic={scores['cyclomatic']} cognitive={scores['cognitive']}")
        elif scores["cyclomatic"] > frozen["cyclomatic"] or scores["cognitive"] > frozen["cognitive"]:
            worse.append(
                f"  {name}  cyclomatic {frozen['cyclomatic']}->{scores['cyclomatic']}"
                f"  cognitive {frozen['cognitive']}->{scores['cognitive']}"
            )

    improved = [
        name
        for name, frozen in allow.items()
        if name not in measured
        or measured[name]["cyclomatic"] < frozen["cyclomatic"]
        or measured[name]["cognitive"] < frozen["cognitive"]
    ]

    print(f"{len(measured)} function(s) over the threshold; {len(allow)} frozen.")
    if improved:
        print(f"{len(improved)} improved — re-freeze with --update to lock the gain in.")

    if new:
        print(f"\n{len(new)} function(s) newly over the threshold "
              f"(cyclomatic>{MAX_CYCLOMATIC} or cognitive>{MAX_COGNITIVE}):\n")
        print("\n".join(new))
        print("\nSplit the function, or freeze it deliberately with --update and say why in the PR.")
    if worse:
        print(f"\n{len(worse)} allowlisted function(s) got worse:\n")
        print("\n".join(worse))
        print("\nThe ratchet only turns one way — bring these back to at least their frozen score.")

    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(
                f"## Complexity ratchet\n\n- over threshold: {len(measured)}\n"
                f"- frozen: {len(allow)}\n- new: {len(new)}\n- worsened: {len(worse)}\n"
                f"- improved (re-freeze to lock in): {len(improved)}\n"
            )

    return 1 if (new or worse) else 0


if __name__ == "__main__":
    raise SystemExit(main())
