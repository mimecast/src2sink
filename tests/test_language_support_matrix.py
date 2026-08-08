"""`OI-43`: language support is a matrix, and every hole in it must be named.

`OI-36` gave silent failure a gate, and it works — but it looks for an `except`
whose body discards the error. **There is no exception anywhere in `OI-43`.**
There is a `dict.get(language, frozenset())` returning empty and a loop that runs
zero times, and the result is a language that produces no field types, no
supertypes, and therefore no `T1` or `T2` call resolution — while looking exactly
like a supported one.

That is the third distinct surface for the same failure, after the handlers
themselves and `OI-39`'s over-broad regex. The lesson `OI-39` already taught is
that *the surface is wider than any list of `except` blocks*, so this gate is
built the same way `OI-36`'s was: a thing that must be complete, and a list of
holes each of which carries a reason someone signed.

**Two checks, because one of them would have missed the worst case.**

The *structural* check asks whether each language appears in each table. It would
have found the missing `FIELD_NODE_TYPES` entries.

The *behavioural* check asks what a language actually produces from real source.
It is the one that matters, because Go's `type_declaration` **is** in
`CLASS_NODE_TYPES` — the structural check passes — and every Go type declaration
is still discarded, since Go puts the name on the child `type_spec` and
`_declaration_name` asks the node itself. A table can be complete and wrong.
"""

from __future__ import annotations

import src2sink.extractors.ast_walk as aw
from src2sink.constants import SOURCE_EXTENSIONS
from src2sink.extractors.base import supported_languages
import pytest

from src2sink.extractors.unified import extract_from_file
from src2sink.resolve import resolve_calls

# The per-language tables that decide what a grammar yields. Named here rather
# than discovered, so adding a table is a deliberate act that shows up in review.
_TABLES = {
    "CLASS_NODE_TYPES": aw.CLASS_NODE_TYPES,
    "METHOD_NODE_TYPES": aw.METHOD_NODE_TYPES,
    "CALL_NODE_TYPES": aw.CALL_NODE_TYPES,
    "FIELD_NODE_TYPES": aw.FIELD_NODE_TYPES,
    "SUPERTYPE_NODE_TYPES": aw.SUPERTYPE_NODE_TYPES,
}

# Languages scanned with no tree-sitter grammar at all. Every AST pass returns
# before doing anything, so the file contributes to no path.
_NO_GRAMMAR: dict[str, str] = {
    "scala": (
        "deferred, not blocked — `tree-sitter-scala` is on PyPI. Scala files are "
        "counted in the language breakdown and get the regex passes only, which "
        "is documented under Known limitations. `OI-43` step 5 puts it last "
        "deliberately: adding the grammar without the resolution tables buys "
        "calls and methods with nothing behind them, which is how the rest of "
        "this matrix got into the state below."
    ),
}

# `table:language` pairs deliberately absent today. Each is a hole in call
# resolution by consent, and the reason is what stops it becoming folklore.
_TABLE_GAPS: dict[str, str] = {
    "FIELD_NODE_TYPES:javascript": (
        "**Intrinsic, not unfilled.** JavaScript declares no types, so there is "
        "no declared field type to read and T1 cannot fire for it however much "
        "work is done. Its supertypes *are* read, so T2 still applies. This "
        "entry is the one gap here that will never close."
    ),
}

# One realistic three-type shape per language: an interface, an implementation
# of it, and a caller holding the interface as a declared field. That is the
# shape `OI-17`'s T1 and T2 exist for, so what a language yields from it is the
# honest statement of what resolution it can support.
_SAMPLES: dict[str, tuple[str, str]] = {
    "java": ("Svc.java", """
interface Repo { void find(); }
class JdbcRepo implements Repo { public void find() { db.query("SELECT 1"); } }
class Svc { private Repo repo; void go() { repo.find(); } }
"""),
    # Bodies are newline-separated deliberately: `tree_sitter_kotlin` 1.1.0 does
    # not accept a single-line class body, so `class Svc { fun go() {} }` parses
    # to an ERROR tree and yields nothing. Real Kotlin is multiline, so this is a
    # property of the probe rather than of the fleet — but see `OI-43` step 6:
    # nothing anywhere checks `has_error`, so that failure is silent too.
    "kotlin": ("Svc.kt", """
interface Repo {
    fun find()
}

class JdbcRepo : Repo {
    override fun find() {
        db.query("SELECT 1")
    }
}

class Svc(private val repo: Repo) {
    fun go() {
        repo.find()
    }
}
"""),
    "typescript": ("svc.ts", """
interface Repo { find(): void }
class JdbcRepo implements Repo { find(): void { db.query("SELECT 1") } }
class Svc { private repo: Repo; go() { this.repo.find() } }
"""),
    "tsx": ("svc.tsx", """
interface Repo { find(): void }
class JdbcRepo implements Repo { find(): void { db.query("SELECT 1") } }
class Svc { private repo: Repo; go() { this.repo.find() } }
"""),
    # `extends` is in the sample deliberately: JavaScript has no declared field
    # types, so `fields` is permanently 0 for it, and without a supertype the
    # frozen record would exercise neither half and could not see either regress.
    "javascript": ("svc.js", """
class Base { find() {} }
class JdbcRepo extends Base { find() { db.query("SELECT 1") } }
class Svc { constructor(repo) { this.repo = repo } go() { this.repo.find() } }
"""),
    "python": ("svc.py", """
class Repo:
    def find(self): ...
class JdbcRepo(Repo):
    def find(self): db.query("SELECT 1")
class Svc:
    repo: Repo
    def go(self): self.repo.find()
"""),
    # Embedding is in the sample because it is Go's only syntactic supertype:
    # `type Repo interface { Base }` and an unnamed struct field both promote
    # another type's methods. Interface *satisfaction* is structural and says
    # nothing syntactically, so it can never appear here.
    "go": ("svc.go", """
type Base interface { Ping() }
type Repo interface {
	Base
	Find()
}
type JdbcRepo struct {}
func (j *JdbcRepo) Find() { db.Query("SELECT 1") }
type Svc struct { repo Repo }
func (s *Svc) Go() { s.repo.Find() }
"""),
}

# What each language *actually* produces from its sample today. Frozen, because
# the point is that a change here is visible: a drop is a regression, a rise is
# `OI-43` step 3 landing and the record must move with it.
#
# Read the zeros. `fields` and `supertypes` at 0 mean T1 and T2 cannot fire, so
# every call in that language resolves `low` or not at all.
#
# These numbers were **measured, not predicted**. The first draft of this table
# was written by hand and was wrong for four of the seven languages, which is a
# fair illustration of why the behavioural check exists at all.
_OBSERVED: dict[str, dict[str, int]] = {
    "java":       {"types": 3, "fields": 1, "supertypes": 1, "methods": 3, "owned_methods": 3, "calls": 2},
    "kotlin":     {"types": 3, "fields": 1, "supertypes": 1, "methods": 3, "owned_methods": 3, "calls": 2},
    "typescript": {"types": 3, "fields": 1, "supertypes": 1, "methods": 2, "owned_methods": 2, "calls": 2},
    "tsx":        {"types": 3, "fields": 1, "supertypes": 1, "methods": 2, "owned_methods": 2, "calls": 2},
    # `fields` is permanently 0: JavaScript declares no types. Its supertypes are
    # read, so T2 applies where T1 never can.
    "javascript": {"types": 3, "fields": 0, "supertypes": 1, "methods": 4, "owned_methods": 4, "calls": 2},
    "python":     {"types": 3, "fields": 1, "supertypes": 1, "methods": 3, "owned_methods": 3, "calls": 2},
    "go":         {"types": 4, "fields": 1, "supertypes": 1, "methods": 2, "owned_methods": 2, "calls": 2},
}


def _capabilities(language: str) -> dict[str, int]:
    """What one language yields from its sample, by observation family."""
    rel_path, source = _SAMPLES[language]
    nodes, _edges = extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source,
    )
    types = [n for n in nodes if n.family == "type-decl"]
    methods = [n for n in nodes if n.family == "method-decl"]
    return {
        "types": len(types),
        "fields": sum(len(n.detail.get("fields") or {}) for n in types),
        "supertypes": sum(len(n.detail.get("supertypes") or []) for n in types),
        "methods": len(methods),
        # Counted separately from `methods` because the two move independently,
        # and the difference is a whole defect: Go's receiver fix (`OI-43` step 2)
        # changed no method *count* at all, only whether each one knew what it
        # hung off. Without this column the gate would have watched that land and
        # said nothing.
        "owned_methods": len([n for n in methods if n.detail.get("class")]),
        "calls": len([n for n in nodes if n.family == "call-site"]),
    }


# --- the structural check -----------------------------------------------------


def test_every_scanned_language_has_a_grammar_or_a_reason() -> None:
    """A language scanned without a grammar contributes to no path, silently."""
    scanned = set(SOURCE_EXTENSIONS.values())
    gap = sorted(scanned - supported_languages() - set(_NO_GRAMMAR))
    assert not gap, (
        f"these languages are scanned but have no tree-sitter grammar: {gap}\n\n"
        "Every AST pass returns before doing anything, so their files take part "
        "in no path and the answer is 'nothing reaches a sink here'. Add the "
        "grammar, or add the language to _NO_GRAMMAR with a reason."
    )


def test_every_grammar_language_is_in_every_table_or_named() -> None:
    """A language missing from a table yields nothing for that pass, silently."""
    holes: list[str] = []
    for table_name, table in _TABLES.items():
        for language in sorted(supported_languages()):
            key = f"{table_name}:{language}"
            if language not in table and key not in _TABLE_GAPS:
                holes.append(key)
    assert not holes, (
        "these languages have a grammar but are missing from a table:\n  "
        + "\n  ".join(holes)
        + "\n\nThe lookup returns an empty frozenset and the loop runs zero "
        "times — no error, no note, no observations. Fill the table, or add the "
        "pair to _TABLE_GAPS with the reason and what it costs resolution."
    )


def test_no_named_gap_has_quietly_been_filled() -> None:
    """A stale exemption hides the next hole — `OI-36`'s lesson, again."""
    stale = sorted(
        key for key in _TABLE_GAPS
        if key.split(":")[1] in _TABLES[key.split(":")[0]]
    )
    assert not stale, (
        f"these gaps are no longer gaps — good news, but remove them from "
        f"_TABLE_GAPS so the list keeps meaning something: {stale}"
    )


def test_no_grammar_exemption_is_stale() -> None:
    """An exemption for a language that now has a grammar, or is no longer scanned."""
    scanned = set(SOURCE_EXTENSIONS.values())
    stale = sorted(
        lang for lang in _NO_GRAMMAR
        if lang in supported_languages() or lang not in scanned
    )
    assert not stale, f"_NO_GRAMMAR entries no longer apply: {stale}"


def test_every_exemption_states_a_reason() -> None:
    """A bare entry is an undocumented hole, which is what this gate is against."""
    thin = sorted(
        k for k, v in {**_NO_GRAMMAR, **_TABLE_GAPS}.items() if len(v.strip()) < 40
    )
    assert not thin, f"exemptions need a reason, not a placeholder: {thin}"


# --- the behavioural check ----------------------------------------------------


def test_each_language_yields_what_the_record_says() -> None:
    """The check the structural one cannot make: a table can be complete and wrong.

    Go is the proof. `type_declaration` is in `CLASS_NODE_TYPES`, so every
    structural check above passes for it — and it still produces zero type
    declarations, because the grammar puts the name one level down. Only running
    real source through the extractor finds that.
    """
    drift: list[str] = []
    for language, expected in sorted(_OBSERVED.items()):
        actual = _capabilities(language)
        if actual != expected:
            drift.append(f"{language}: recorded {expected}, observed {actual}")
    assert not drift, (
        "what a language yields has changed:\n  " + "\n  ".join(drift)
        + "\n\nIf a number went UP this is `OI-43` step 3 landing — update "
        "_OBSERVED to lock the gain in. If it went DOWN, a language quietly "
        "stopped producing observations and every path through it just "
        "disappeared without an error."
    )


def test_the_matrix_records_todays_known_holes() -> None:
    """The gate must show the problem it was written for, or it proves nothing.

    A gate whose frozen record happens to be all-green would pass for the wrong
    reason. These are the holes `OI-43` was filed about, asserted so that fixing
    them has to come here and say so.
    """
    assert _OBSERVED["go"]["types"] > 0, "OI-43 step 2 regressed: Go types vanished again"
    assert _OBSERVED["go"]["owned_methods"] > 0, (
        "OI-43 step 2 regressed: Go methods stopped knowing their receiver type"
    )
    # `OI-43` step 3: the matrix is filled everywhere the concept exists.
    for language in ("java", "kotlin", "typescript", "tsx", "python", "go"):
        assert _OBSERVED[language]["fields"] > 0, f"{language} lost declared field types"
    for language in _OBSERVED:
        assert _OBSERVED[language]["supertypes"] > 0, f"{language} lost supertypes"
    assert _OBSERVED["javascript"]["fields"] == 0, (
        "JavaScript declares no types, so this is the one entry that can never "
        "be filled — if it is non-zero the probe is measuring something else"
    )


def test_the_gate_can_actually_fail() -> None:
    """A gate that cannot fire is decoration — `OI-36`'s lesson one level up."""
    java = _capabilities("java")
    assert java["fields"] > 0 and java["supertypes"] > 0, (
        "the sample must exercise what it claims to measure; if Java yields no "
        "fields the probe is broken, not the extractor"
    )
    assert "scala" not in supported_languages(), (
        "the _NO_GRAMMAR path must still be reachable, or the staleness check "
        "above is the only thing keeping this honest"
    )


# --- the note (`OI-43` step 4) ------------------------------------------------


def test_coverage_gaps_are_computed_not_restated() -> None:
    """The list that rotted into `OI-43` was hand-maintained. This one is derived.

    A note claiming a limitation that has since been fixed is worse than no note,
    because it is confidently wrong; reading the live tables means the claim and
    the behaviour cannot disagree.
    """
    for language in ("java", "kotlin", "typescript", "tsx", "python", "go"):
        assert aw.coverage_gaps(language) == (), f"{language} is fully covered now"
    # JavaScript declares no types, so T1 is out of reach by language design
    # rather than by unfilled work — the one gap that will never close.
    assert "T1" in " ".join(aw.coverage_gaps("javascript"))
    assert "T2" not in " ".join(aw.coverage_gaps("javascript")), (
        "JavaScript supertypes are read, so T2 applies where T1 never can"
    )
    assert "no tree-sitter grammar" in aw.coverage_gaps("scala")[0]


def test_a_filled_table_silences_its_gap(monkeypatch) -> None:
    """The proof that it is derived: fill a table, the gap disappears."""
    before = aw.coverage_gaps("javascript")
    monkeypatch.setitem(aw.FIELD_NODE_TYPES, "javascript", frozenset({"field_definition"}))
    after = aw.coverage_gaps("javascript")
    assert len(after) == len(before) - 1
    assert not [g for g in after if "T1" in g]


def test_the_note_is_per_repo_per_language_not_per_file(tmp_path) -> None:
    """Scala alone would otherwise put a note on every Scala file in the estate.

    A signal that loud stops being read, which is the noise question that kept
    this out of `OI-36` phase 1.
    """
    from collections import Counter

    from src2sink.build_metabase_v2 import _note_language_coverage
    from src2sink.schema import RepoSummaryV2

    summary = RepoSummaryV2(group="g", name="r")
    _note_language_coverage(
        summary, Counter({"javascript": 40, "scala": 900, "java": 12, "go": 7})
    )

    assert len(summary.notes) == 2, "one note per affected language, whatever the file count"
    for covered in ("java", "go"):
        # `f"{covered}:"`, not `covered` — "java" is a prefix of "javascript",
        # and the loose form passed while asserting the opposite of the truth.
        assert not [n for n in summary.notes if n.startswith(f"{covered}:")], (
            f"{covered} is fully covered and must stay quiet, or the signal is noise"
        )
    scala = next(n for n in summary.notes if n.startswith("scala"))
    assert "900 file(s)" in scala
    assert "incomplete rather than absent" in scala, "the consequence is the point"


def test_a_fully_covered_repo_carries_no_note() -> None:
    """A JVM-only repo has nothing to be told."""
    from collections import Counter

    from src2sink.build_metabase_v2 import _note_language_coverage
    from src2sink.schema import RepoSummaryV2

    summary = RepoSummaryV2(group="g", name="r")
    _note_language_coverage(summary, Counter({"java": 10, "kotlin": 3, "python": 5}))
    assert summary.notes == []


# --- what the matrix buys: resolution tiers (`OI-43` step 3) ------------------

_RESOLUTION_SAMPLES: dict[str, tuple[str, str]] = {
    "typescript": ("svc.ts", """
interface Repo { find(): void }
class JdbcRepo implements Repo { find(): void { db.query("SELECT 1") } }
class Svc { private repo: Repo; go() { this.repo.find() } }
"""),
    "python": ("svc.py", """
class Repo:
    def find(self): ...
class JdbcRepo(Repo):
    def find(self): db.query("SELECT 1")
class Svc:
    repo: Repo
    def go(self): self.repo.find()
"""),
    # Go's canonical shape is a *concrete* field: interface satisfaction is
    # structural, so an interface-typed field can never resolve above T3 here.
    "go": ("svc.go", """
type JdbcRepo struct {}
func (j *JdbcRepo) Find() { db.Query("SELECT 1") }
type Svc struct { repo JdbcRepo }
func (s *Svc) Go() { s.repo.Find() }
"""),
}

# The tier each language reaches on the canonical shape: an interface-typed
# field, called, resolving to the implementation that has a body. Filling the
# tables is only worth anything if this moves, so it is asserted rather than
# assumed — `OI-43` step 2 was explicit that it changed no tier, and this is the
# step that does.
_EXPECTED_TIER = {"typescript": "T2", "python": "T1", "go": "T1"}


@pytest.mark.parametrize("language", sorted(_RESOLUTION_SAMPLES))
def test_the_canonical_shape_resolves_above_unique_name(language):
    """Before `OI-43` step 3 every one of these was T3 — unique name, `low`."""
    rel_path, source = _RESOLUTION_SAMPLES[language]
    nodes, _edges = extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source,
    )
    resolved = resolve_calls(nodes)
    tiers = {r.tier for r in resolved}
    assert tiers == {_EXPECTED_TIER[language]}, (
        f"{language} resolved by {tiers or 'nothing'}; filling the tables is "
        "only worth something if the tier moves off T3"
    )


def test_go_resolves_a_concrete_field_by_declared_type():
    """The fixable half of Go's gap, now fixed.

    `_normalise_receiver` knew only `this.` and `self.`, and Go's receiver name
    is the author's choice — `func (s *Svc)` makes `s.repo` exactly `this.repo`,
    but nothing carried `s` from the declaration to the call, so every Go field
    access was discarded as an unfollowable chain. Go had the facts and could not
    use them.
    """
    for receiver, call in (("s *Svc", "s"), ("svc Svc", "svc")):
        nodes, _edges = extract_from_file(
            repo_id="g/r", rel_path="svc.go", language="go", source=f"""
type JdbcRepo struct {{}}
func (j *JdbcRepo) Find() {{ db.Query("SELECT 1") }}
type Svc struct {{ repo JdbcRepo }}
func ({receiver}) Go() {{ {call}.repo.Find() }}
""",
        )
        resolved = resolve_calls(nodes)
        assert [(r.tier, r.confidence) for r in resolved] == [("T1", "high")], (
            f"a {'pointer' if '*' in receiver else 'value'} receiver must resolve "
            "the same way; the name is arbitrary either way"
        )


def test_go_interfaces_remain_unreachable_by_design():
    """The half that is not fixable, asserted so nobody expects it to be.

    Go interface satisfaction is **structural**: a type implements an interface
    by having the methods, and declares no link to it. No syntactic read can
    connect `JdbcRepo` to `Repo`, so T2 can never fire through a Go interface
    however much work is done — unlike `OI-43`'s other gaps, which were unfilled
    rather than impossible.
    """
    nodes, _edges = extract_from_file(
        repo_id="g/r", rel_path="svc.go", language="go", source="""
type Repo interface { Find() }
type JdbcRepo struct {}
func (j *JdbcRepo) Find() { db.Query("SELECT 1") }
""",
    )
    types = {n.detail["class"]: n.detail for n in nodes if n.family == "type-decl"}
    assert types["JdbcRepo"]["supertypes"] == [], (
        "if this ever becomes non-empty, Go has gained a syntactic implements "
        "clause and this whole limitation is worth revisiting"
    )
