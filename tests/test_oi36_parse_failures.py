"""`OI-36` phase 1: the two clusters the gate was scoped ahead of.

`tests/test_silent_failure_gate.py` froze 43 handlers that discard an error
without recording anything. Seven of them were not incidental — they were the
issue in its purest form, and they are fixed here:

* **the four dependency parsers** — a malformed `pyproject.toml`,
  `package.json` or lockfile yielded zero dependencies, which is the same value
  as a repo that genuinely declares none. `OI-18` in four more places.
* **the three `ts_extractors` passes** — a file tree-sitter could not parse took
  part in no path, and the answer came back *"nothing reaches a sink here"* at
  full confidence, from a foundation that had not been read. That one sits
  underneath every `OI-17` result.

The gate proves a handler *records something*. These tests prove it records the
**right** thing, in a place someone will see, and — the part that matters most —
that a broken input is now distinguishable from an empty one. A note nobody can
act on would pass the gate and fix nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from src2sink.build_metabase_v2 import (
    _collect_dependencies,
    _scan_repo_files,
    _unparsed_counts,
    _write_run_manifest,
)
from src2sink.constants import NOTE_PARSE_FAILED, NOTE_UNPARSED_MANIFEST
from src2sink.dependencies import (
    parse_npm_dependencies,
    parse_python_dependencies,
)
from src2sink.extractors.unified import extract_from_file
from src2sink.schema import RepoSummaryV2


# --- the dependency parsers ---------------------------------------------------


def test_a_malformed_pyproject_is_not_an_empty_repo(tmp_path):
    """The whole complaint: `[]` meant both "none" and "we could not read it"."""
    (tmp_path / "pyproject.toml").write_text("[project\nname = broken", encoding="utf-8")

    deps, notes = parse_python_dependencies(tmp_path)
    assert deps == []
    assert len(notes) == 1
    assert "pyproject.toml" in notes[0]
    assert NOTE_UNPARSED_MANIFEST in notes[0]
    assert "incomplete for this repo, not empty" in notes[0], (
        "the note must state the consequence; 'parse error' still leaves the "
        "reader unable to tell an incomplete list from a complete one"
    )


def test_a_healthy_pyproject_produces_no_note(tmp_path):
    """A gate that fires on success trains people to ignore it."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["requests>=2"]\n', encoding="utf-8",
    )
    deps, notes = parse_python_dependencies(tmp_path)
    assert [d["artifactId"] for d in deps] == ["requests"]
    assert notes == []


def test_an_absent_manifest_is_not_a_failure(tmp_path):
    """A repo with no Python in it has not failed to parse anything."""
    assert parse_python_dependencies(tmp_path) == ([], [])


def test_a_malformed_package_json_is_reported(tmp_path):
    """The npm half of the same defect."""
    pkg = tmp_path / "package.json"
    pkg.write_text("{not json", encoding="utf-8")

    deps, notes = parse_npm_dependencies(tmp_path, pkg)
    assert deps == []
    assert len(notes) == 1 and NOTE_UNPARSED_MANIFEST in notes[0]


def test_a_broken_lockfile_says_the_versions_are_ranges(tmp_path):
    """A different consequence, so a different note.

    The manifest still parses, so dependencies *are* found — but every one of
    them silently demotes from a resolved version to a range. Reporting that as
    "could not parse dependencies" would be wrong in the other direction.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["requests>=2"]\n', encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("[[package\nbroken", encoding="utf-8")

    deps, notes = parse_python_dependencies(tmp_path)
    assert [d["artifactId"] for d in deps] == ["requests"], "the manifest still parsed"
    assert len(notes) == 1
    assert "uv.lock" in notes[0]
    assert "understate what is actually pinned" in notes[0]


def test_the_note_reaches_the_repo_summary(tmp_path):
    """A note the scan never collects is the same as no note at all."""
    (tmp_path / "pyproject.toml").write_text("[project\nbroken", encoding="utf-8")

    _deps, notes = _collect_dependencies(tmp_path)
    assert any(NOTE_UNPARSED_MANIFEST in n for n in notes)


# --- the tree-sitter passes ---------------------------------------------------


def test_a_file_that_will_not_parse_says_so(monkeypatch):
    """`OI-17`'s foundation: no observations must not read as no sinks."""
    import src2sink.extractors.ts_extractors as ts

    def explode(*_a, **_kw):
        raise ValueError("no grammar")

    monkeypatch.setattr(ts, "parse_source", explode)

    notes: list[str] = []
    nodes, _edges = extract_from_file(
        repo_id="g/r", rel_path="Svc.java", language="java",
        source="class Svc { void go() {} }", notes=notes,
    )

    assert len(notes) == 1, f"one broken file should read as one problem: {notes}"
    assert "Svc.java" in notes[0]
    assert NOTE_PARSE_FAILED in notes[0]
    assert "takes part in no path" in notes[0], (
        "the consequence is the point — a reader needs to know the silence about "
        "this file is not evidence of safety"
    )
    assert not [n for n in nodes if n.family in {"call-site", "method-decl", "type-decl"}]


def test_three_passes_failing_on_one_file_is_one_note(monkeypatch):
    """Calls, declarations and types all parse the same file and fail identically."""
    import src2sink.extractors.ts_extractors as ts

    monkeypatch.setattr(ts, "parse_source", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))

    notes: list[str] = []
    extract_from_file(
        repo_id="g/r", rel_path="A.java", language="java", source="class A {}", notes=notes,
    )
    assert len(notes) == 1


def test_a_healthy_file_produces_no_note():
    """Again: the signal has to stay rare enough to mean something."""
    notes: list[str] = []
    extract_from_file(
        repo_id="g/r", rel_path="A.java", language="java",
        source="class A { void go() { db.query(\"SELECT 1\"); } }", notes=notes,
    )
    assert notes == []


def test_the_scan_threads_the_notes_sink(tmp_path, monkeypatch):
    """`notes` is optional, so nothing stops the scan quietly dropping it.

    Roughly thirty tests unpack `extract_from_file`'s pair and none of them care
    about notes, which is why the sink is a keyword rather than a third return
    value. The cost of that choice is exactly this risk, so it is asserted rather
    than trusted.
    """
    import src2sink.extractors.ts_extractors as ts

    monkeypatch.setattr(ts, "parse_source", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    (tmp_path / "A.java").write_text("class A {}", encoding="utf-8")

    summary = RepoSummaryV2(group="g", name="r")
    _scan_repo_files(tmp_path, "g/r", summary)
    assert any(NOTE_PARSE_FAILED in n for n in summary.notes), (
        "the scan stopped passing notes= to extract_from_file; every parse "
        "failure in the fleet just became invisible again"
    )


# --- the fleet-level count ----------------------------------------------------


def _record(root: Path, name: str, notes: list[str]) -> None:
    d = root / "repos" / "g"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({
        "schema_version": 2, "group": "g", "name": name,
        "nodes": [], "edges": [], "dependencies_internal": [], "notes": notes,
    }), encoding="utf-8")


def test_the_run_counts_what_the_fleet_could_not_read(tmp_path):
    """A note in one record among 746 is not something anyone reads."""
    _record(tmp_path, "a", [f"A.java: java {NOTE_PARSE_FAILED} (ValueError); ..."])
    _record(tmp_path, "b", [
        f"B.java: java {NOTE_PARSE_FAILED} (OSError); ...",
        f"pyproject.toml is present but {NOTE_UNPARSED_MANIFEST} (X); ...",
    ])
    _record(tmp_path, "c", [])

    counts = _unparsed_counts(sorted((tmp_path / "repos" / "g").glob("*.json")))
    assert counts == {
        "source_files": 2, "manifests": 1, "repos_affected": 2, "records_unreadable": 0,
    }


def test_a_record_it_cannot_read_is_not_counted_as_clean(tmp_path, capsys):
    """The reporter must not have the defect it reports.

    An unreadable record means the count understates the problem while looking
    authoritative — which is `OI-36` arriving inside its own fix.
    """
    _record(tmp_path, "a", [])
    (tmp_path / "repos" / "g" / "broken.json").write_text("{not json", encoding="utf-8")

    counts = _unparsed_counts(sorted((tmp_path / "repos" / "g").glob("*.json")))
    assert counts["records_unreadable"] == 1
    assert "lower bound" in capsys.readouterr().err


def test_the_count_lands_in_the_manifest(tmp_path):
    """Where an operator will actually meet it."""
    _record(tmp_path, "a", [f"A.java: java {NOTE_PARSE_FAILED} (ValueError); ..."])

    class _Args:
        repos_root, metabase_root = "repos", "metabase"
        workers = repo_timeout = max_files_per_repo = 1
        max_file_bytes = max_line_bytes = 1
        force = False
        repo = limit = api_clients = prescreen_indicators = None

    _write_run_manifest(
        tmp_path, _Args(), [], skipped=0, timed_out=0,
        started_at="T0", finished_at="T1", total_seconds=1.0,
        json_paths=sorted((tmp_path / "repos" / "g").glob("*.json")),
    )
    manifest = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["unparsed"]["source_files"] == 1
