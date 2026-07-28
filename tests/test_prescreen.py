"""TA-007 — malicious/pathological content is pre-screened before parsing.

Verifies the always-on structural checks (binary, minified/oversized line), the
opt-in operator indicator list, and that analyse_repo_v2 skips a flagged file
and records why rather than handing it to the extractors (threat-model SEC-NEW-4).
"""

from __future__ import annotations


from src2sink.build_metabase_v2 import analyse_repo_v2
from src2sink.prescreen import MAX_LINE_BYTES, configure_indicators, load_indicators, screen


def test_screen_flags_binary_null_byte(tmp_path):
    assert screen(tmp_path / "x.bin", "abc\x00def") is not None


def test_screen_flags_high_replacement_ratio(tmp_path):
    assert screen(tmp_path / "x.dat", "�" * 100 + "ok") is not None


def test_screen_flags_oversized_line(tmp_path):
    reason = screen(tmp_path / "x.js", "a" * (MAX_LINE_BYTES + 1))
    assert reason is not None
    assert "--max-line-bytes" in reason  # tells the user which knob to turn


def test_configure_max_line_bytes(tmp_path):
    from src2sink.prescreen import configure_max_line_bytes

    long_line = "a" * 200
    try:
        configure_max_line_bytes(100)  # tighten
        assert screen(tmp_path / "x.js", long_line) is not None
        configure_max_line_bytes(0)  # disable the check entirely
        assert screen(tmp_path / "x.js", long_line) is None
    finally:
        configure_max_line_bytes(MAX_LINE_BYTES)  # reset for other tests


def test_screen_passes_normal_source(tmp_path):
    assert screen(tmp_path / "M.java", "class M {\n  int x = 1;\n}\n") is None


def test_screen_matches_configured_indicator(tmp_path):
    reason = screen(tmp_path / "x.sh", "echo hi\npowershell -enc AAAA\n", indicators=("powershell -enc",))
    assert reason is not None and "indicator" in reason


def test_configure_and_load_indicators(tmp_path):
    f = tmp_path / "indicators.txt"
    f.write_text("# comment\npowershell -enc\n\nEVAL(BASE64\n", encoding="utf-8")
    inds = load_indicators(f)
    assert inds == ("powershell -enc", "EVAL(BASE64")
    try:
        configure_indicators(inds)
        # process-wide config is used when the indicators kwarg is omitted
        assert screen(tmp_path / "a.txt", "x eval(base64_decode y") is not None
        assert screen(tmp_path / "b.txt", "clean content") is None
    finally:
        configure_indicators(())  # reset global for other tests


def test_load_indicators_missing_file_returns_empty(tmp_path):
    assert load_indicators(tmp_path / "nope.txt") == ()


def test_analyse_repo_skips_flagged_file_with_note(tmp_path):
    repo = tmp_path / "repo" / "src"
    repo.mkdir(parents=True)
    (repo / "Good.java").write_text("class Good {}", encoding="utf-8")
    # A minified/obfuscated blob that must be skipped before parsing.
    (repo / "packed.js").write_text("var a=" + "1;" * (MAX_LINE_BYTES // 2 + 10), encoding="utf-8")

    summary = analyse_repo_v2(tmp_path / "repo", "grp", "repo", "grp/repo")
    assert any("skipped" in n and "packed.js" in n for n in summary.notes)
    # The good file was still scanned.
    assert summary.language_breakdown.get("java") == 1
    # The packed file did not contribute nodes.
    assert not any(n.file.endswith("packed.js") for n in summary.nodes)
