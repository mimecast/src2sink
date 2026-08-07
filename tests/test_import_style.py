"""One import style per module per file.

A file that does both `import x` and `from x import y` binds the same module two
ways, and a reader — or a `monkeypatch` — cannot tell from a call site which
binding is in play. Patching `x.thing` leaves `from x import thing` untouched,
which is a real and quiet failure in a test suite that patches as much as this
one does.

**This gate exists because I shipped the same defect four times.** CodeQL flagged
it on three separate pull requests, and the fourth was in the commit that fixed
the third. Each time the fix was two lines and each time it was found by a bot
after review had started.

That is the `OI-36` argument applied to process rather than to code: a mistake
caught by review every time is a mistake nothing prevents. The check costs
milliseconds and fails locally, before a reviewer spends attention on it.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _mixed_imports(path: Path) -> list[str]:
    """Modules this file imports both as a module object and by symbol."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    plain: dict[str, list[int]] = defaultdict(list)
    froms: dict[str, list[int]] = defaultdict(list)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                plain[alias.name].append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            # Relative imports are excluded: `from . import x` and `import pkg.x`
            # spell different things and comparing them would be noise.
            froms[node.module].append(node.lineno)

    return [
        f"{module} (import at {plain[module]}, from-import at {froms[module]})"
        for module in sorted(set(plain) & set(froms))
    ]


def test_no_module_is_imported_two_ways() -> None:
    """The defect CodeQL kept finding, checked before it reaches a reviewer.

    Picking a style is the fix, and which one depends on the file: a test that
    monkeypatches a module needs the module object, so the symbol imports go; a
    file that only calls one function is clearer importing just that.
    """
    offenders: list[str] = []
    for path in sorted([*(_ROOT / "src2sink").rglob("*.py"),
                        *(_ROOT / "tests").glob("*.py"),
                        *(_ROOT / "scripts").glob("*.py")]):
        for mixed in _mixed_imports(path):
            offenders.append(f"{path.relative_to(_ROOT)}: {mixed}")

    assert not offenders, (
        "these files import one module two ways:\n  "
        + "\n  ".join(offenders)
        + "\n\nPick one. If the file monkeypatches the module, keep the module "
        "object and drop the symbol imports; otherwise keep the symbols."
    )


def test_the_gate_can_actually_fail(tmp_path: Path) -> None:
    """A gate that cannot fire is decoration — the `OI-36` lesson, again."""
    mixed = tmp_path / "mixed.py"
    mixed.write_text(
        "import json\nfrom json import loads\n\nprint(json, loads)\n", encoding="utf-8",
    )
    assert _mixed_imports(mixed), "should have caught json imported two ways"

    clean = tmp_path / "clean.py"
    clean.write_text("import json\n\nprint(json.loads)\n", encoding="utf-8")
    assert not _mixed_imports(clean)


def test_a_relative_import_is_not_a_conflict(tmp_path: Path) -> None:
    """`from . import x` alongside `import pkg.x` is not the same statement twice."""
    rel = tmp_path / "rel.py"
    rel.write_text("from . import queues\nimport json\n", encoding="utf-8")
    assert not _mixed_imports(rel)
