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
from src2sink.extractors.unified import extract_from_file

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
    **{
        f"FIELD_NODE_TYPES:{lang}": (
            f"`OI-43` step 3. Without declared field types, `OI-17`'s T1 tier "
            f"cannot fire for {lang}, so every call falls to T3 (unique name, "
            f"`low`) or is dropped. The concept exists in this language, so this "
            f"is unfilled work rather than an impossibility."
        )
        for lang in ("python", "javascript", "typescript", "tsx", "go")
    },
    **{
        f"SUPERTYPE_NODE_TYPES:{lang}": (
            f"`OI-43` step 3. Without supertypes, a call on an interface cannot "
            f"reach the implementations that have a body, so T2 cannot fire for "
            f"{lang} and an interface-typed hop is a dead end."
        )
        for lang in ("python", "javascript", "typescript", "tsx", "go")
    },
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
    "javascript": ("svc.js", """
class JdbcRepo { find() { db.query("SELECT 1") } }
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
    "go": ("svc.go", """
type Repo interface { Find() }
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
    "java":       {"types": 3, "fields": 1, "supertypes": 1, "methods": 3, "calls": 2},
    "kotlin":     {"types": 3, "fields": 1, "supertypes": 1, "methods": 3, "calls": 2},
    "typescript": {"types": 2, "fields": 0, "supertypes": 0, "methods": 2, "calls": 2},
    "tsx":        {"types": 2, "fields": 0, "supertypes": 0, "methods": 2, "calls": 2},
    "javascript": {"types": 2, "fields": 0, "supertypes": 0, "methods": 3, "calls": 2},
    "python":     {"types": 3, "fields": 0, "supertypes": 0, "methods": 3, "calls": 2},
    # `type_declaration` IS in CLASS_NODE_TYPES, so the structural check passes.
    # Go still yields nothing, because the name lives on the child `type_spec`
    # and `_declaration_name` asks the node itself. `OI-43` step 2.
    "go":         {"types": 0, "fields": 0, "supertypes": 0, "methods": 2, "calls": 2},
}


def _capabilities(language: str) -> dict[str, int]:
    """What one language yields from its sample, by observation family."""
    rel_path, source = _SAMPLES[language]
    nodes, _edges = extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source,
    )
    types = [n for n in nodes if n.family == "type-decl"]
    return {
        "types": len(types),
        "fields": sum(len(n.detail.get("fields") or {}) for n in types),
        "supertypes": sum(len(n.detail.get("supertypes") or []) for n in types),
        "methods": len([n for n in nodes if n.family == "method-decl"]),
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
    assert _OBSERVED["go"]["types"] == 0, "OI-43 step 2 (Go `type_spec`) landed"
    for language in ("typescript", "go", "python", "javascript", "tsx"):
        assert _OBSERVED[language]["fields"] == 0
        assert _OBSERVED[language]["supertypes"] == 0
    for language in ("java", "kotlin"):
        assert _OBSERVED[language]["fields"] > 0, "the JVM column is the filled one"
        assert _OBSERVED[language]["supertypes"] > 0


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
