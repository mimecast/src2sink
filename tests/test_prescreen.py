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


# ---------------------------------------------------------------------------
# Boundaries and edges (WI-10, Tier A)
#
# A mutation sweep left 12 survivors here at 100% line coverage. The tests above
# assert "something was flagged" or "nothing was", which is the right question
# but blind to *where* each threshold sits — and a screen's thresholds are its
# entire behaviour. Move one and the module still passes every test above while
# quarantining half the fleet, or none of it.
# ---------------------------------------------------------------------------

from src2sink.prescreen import (  # noqa: E402
    _BINARY_SNIFF_CHARS,
    _MAX_REPLACEMENT_RATIO,
)


def test_empty_text_is_screened_without_dividing_by_zero(tmp_path):
    """An empty file is legal and common; the ratio check must not run on it.

    The `if head:` guard is the only thing standing between an empty file and a
    ZeroDivisionError in the pre-screen — which would abort scanning a repo
    rather than skipping one file.
    """
    assert screen(tmp_path / "empty.java", "") is None


def test_replacement_ratio_threshold_sits_where_it_claims(tmp_path):
    """Just over the ratio is flagged; just under is not.

    Existing tests used ~98% replacement characters, so any threshold from 1% to
    97% would have passed them equally.
    """
    over = "�" * 11 + "a" * 89          # 11% > 10%
    under = "�" * 10 + "a" * 90         # exactly 10%, not over
    assert screen(tmp_path / "a.dat", over) is not None
    assert screen(tmp_path / "b.dat", under) is None
    assert _MAX_REPLACEMENT_RATIO == 0.10, "the tests above encode this threshold"


def test_oversized_line_threshold_is_exclusive(tmp_path):
    """A line exactly at the cap passes; one byte over is flagged."""
    assert screen(tmp_path / "a.js", "a" * MAX_LINE_BYTES) is None
    assert screen(tmp_path / "b.js", "a" * (MAX_LINE_BYTES + 1)) is not None


def test_the_binary_sniff_window_is_deliberately_bounded(tmp_path):
    """A null byte past the sniff window is *not* flagged, and that is by design.

    The check is a cheap head-sniff, not a full scan: reading every byte of every
    file to find a null would cost more than it saves. Pinning the boundary keeps
    that a decision rather than an accident — and stops the window being widened
    or narrowed without someone noticing the cost change.
    """
    # Literal sizes, and an explicit assertion on the constant. Deriving the
    # fixture from `_BINARY_SNIFF_CHARS` made this test adapt to any change in
    # it — a mutation run caught that: shrinking the window to 128 shrank the
    # test's own input and it passed regardless. A test that reads the value it
    # is checking cannot detect that value changing.
    assert _BINARY_SNIFF_CHARS == 8192, "the literal sizes below encode this window"
    assert screen(tmp_path / "a.bin", "a" * 8000 + "\x00") == "binary content (null byte)"
    assert screen(tmp_path / "b.bin", "a" * 9000 + "\x00") is None


def test_binary_is_reported_before_an_indicator_match(tmp_path):
    """Order matters: the cheapest, most certain reason should be the one given."""
    reason = screen(
        tmp_path / "x.bin", "\x00 powershell -enc", indicators=("powershell -enc",),
    )
    assert reason == "binary content (null byte)"


def test_a_long_indicator_is_truncated_in_the_reason(tmp_path):
    """The reason echoes operator-supplied text, so its length is bounded."""
    indicator = "z" * 200
    reason = screen(tmp_path / "x.txt", "prefix " + indicator, indicators=(indicator,))
    assert reason is not None
    assert len(reason) < 100, reason
