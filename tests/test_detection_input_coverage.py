"""The detection fingerprint must cover everything that can change a record.

`DETECTION_VERSION` guards the incremental scan: a repo is skipped only when its
sha *and* the detector match, so a detection change that does not bump the
version never reaches repositories that have not themselves changed (`OI-16`).
`scripts/detection_version_check.py` enforces the bump — but only for the files
it is told to watch, and that list was written by hand from an assumption that
"detection" meant `extractors/`.

It does not. A record's contents are produced by everything on the path from
``build_metabase_v2`` down: dependency parsing in ``repo_utils``, the
internal/external decision in ``internal_groups``, the redaction applied in
``summary_to_dict``, the file caps that decide what is scanned at all. Changing
any of those changes records while the gate stays green.

So the watch list is *derived* here rather than trusted. A hand-maintained list
drifts the moment an import changes, which is exactly how this gap opened.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "src2sink"

# Written *after* records are on disk, so they cannot change one. They shape the
# aggregate artefacts, which are recomputed from records on every run and are
# therefore never served stale by the incremental skip.
_POST_RECORD_PACKAGES = (".aggregators.", ".renderers.", ".models.")

# Modules on the record path that deliberately are not fingerprinted. Each needs
# a reason, because an entry here is a hole in the gate by consent.
_EXEMPT: dict[str, str] = {
    "src2sink": "package docstring only; no importable behaviour",
}


def _module_name(path: Path) -> str:
    """Return the dotted module name for a file in the package."""
    rel = path.relative_to(_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _import_graph() -> dict[str, set[str]]:
    """Map each first-party module to the first-party modules it imports."""
    graph: dict[str, set[str]] = {}
    for path in _PKG.rglob("*.py"):
        mod = _module_name(path)
        package = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
        parts = package.split(".")
        deps: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                base = ".".join(parts[: len(parts) - (node.level - 1)])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if target.startswith("src2sink"):
                deps.add(target)
        graph[mod] = deps
    return graph


def _record_path_modules() -> set[str]:
    """Return every module reachable from the record-producing entry point."""
    graph = _import_graph()
    seen: set[str] = set()
    stack = ["src2sink.build_metabase_v2"]
    while stack:
        mod = stack.pop()
        if mod in seen or mod not in graph:
            continue
        seen.add(mod)
        stack.extend(graph[mod])
    return {m for m in seen if not any(p in m for p in _POST_RECORD_PACKAGES)}


def _watched_modules() -> set[str]:
    """Return the modules the committed fingerprint covers."""
    files = json.loads(
        (_ROOT / "scripts" / "detection-fingerprint.json").read_text(encoding="utf-8")
    )["files"]
    return {f.replace("/", ".").removesuffix(".py") for f in files}


def test_every_record_producing_module_is_fingerprinted():
    """A module that can change a record must be watched, or exempted by name.

    Without this the gate is only as good as someone's memory of which files
    count as "detection" — and that memory was wrong: `repo_utils` produces
    `dependencies_internal` on every record and was not watched.
    """
    gap = sorted(_record_path_modules() - _watched_modules() - set(_EXEMPT))
    assert not gap, (
        "these modules can change a repo record but do not trip the "
        "DETECTION_VERSION gate:\n  "
        + "\n  ".join(m.removeprefix("src2sink.") for m in gap)
        + "\n\nAdd them to DETECTION_INPUT_FILES in "
        "scripts/detection_version_check.py and re-freeze, or exempt them in "
        "_EXEMPT above with a reason."
    )


def test_exemptions_are_still_on_the_record_path():
    """An exemption for a module that has moved is stale and hides the next gap."""
    stale = sorted(set(_EXEMPT) - _record_path_modules())
    assert not stale, f"exemptions no longer on the record path: {stale}"


def test_the_gate_does_not_watch_post_record_modules():
    """Watching an aggregator would force pointless fleet rescans.

    Aggregate artefacts are rebuilt from records on every run, so they are never
    served stale — bumping the detector for them would invalidate every record
    to fix output that was already being regenerated.
    """
    watched = _watched_modules()
    misplaced = sorted(m for m in watched if any(p in m for p in _POST_RECORD_PACKAGES))
    assert not misplaced, f"post-record modules should not be fingerprinted: {misplaced}"
