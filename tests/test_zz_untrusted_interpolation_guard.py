"""Lint guard: no raw f-string interpolation of untrusted ``detail`` fields.

SAST report, architectural recommendation for findings 1/4: the untrusted →
output boundary must be un-bypassable. ``md_table`` neutralises table cells, but
content emitted *outside* a table (headings, bullets, Mermaid fences) is a
footgun — an extracted ``detail`` field dropped straight into an f-string can
break Markdown structure or the fenced block (indirect prompt injection).

ruff has no user-authored custom rules, so this project-specific rule lives here
as an AST guard instead. It flags the most direct footgun: a ``detail.get("x")``
or ``detail["x"]`` field extraction interpolated into an f-string without passing
through a sanitiser. It is one defence-in-depth layer, NOT a substitute for
review (it cannot see a detail value first bound to a local and then
interpolated). Scope: the aggregator/renderer/trace writers that emit documents.
"""

from __future__ import annotations

import ast
import pathlib

# Sanitisers that make an untrusted value safe to interpolate.
_ALLOWED = {"for_markdown", "for_table_cell", "for_mermaid_label", "redact_literals"}

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src2sink"
_TARGETS = (
    sorted((_SRC / "aggregators").glob("*.py"))
    + sorted((_SRC / "renderers").glob("*.py"))
    + [_SRC / "trace.py"]
)


def _is_detail_field_access(node: ast.AST) -> bool:
    """True for ``detail.get("x")`` or ``detail["x"]`` (dict field extraction)."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "detail"
    ):
        return True
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "detail"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    )


def _is_sanitiser_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id in _ALLOWED) or (
        isinstance(func, ast.Attribute) and func.attr in _ALLOWED
    )


def _violations(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for js in ast.walk(tree):
        if not isinstance(js, ast.JoinedStr):
            continue
        for fv in js.values:
            if not isinstance(fv, ast.FormattedValue):
                continue
            if _is_sanitiser_call(fv.value):
                continue
            if any(_is_detail_field_access(n) for n in ast.walk(fv.value)):
                out.append(f"{path.name}:{fv.lineno}  {ast.unparse(fv.value)[:70]}")
    return out


def test_no_raw_detail_field_in_fstrings():
    """Fail if any doc writer interpolates a raw detail field into an f-string."""
    found = [v for t in _TARGETS for v in _violations(t)]
    assert not found, (
        "untrusted detail field interpolated into an f-string without a sanitiser "
        "(wrap in for_markdown/for_table_cell/for_mermaid_label/redact_literals):\n  "
        + "\n  ".join(found)
    )
