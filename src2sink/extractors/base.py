"""Tree-sitter parser loading and shared extractor utilities."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Language, Parser, Tree

_LANGUAGE_MODULES: dict[str, tuple[str, str]] = {
    "java": ("tree_sitter_java", "language"),
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "go": ("tree_sitter_go", "language"),
    "kotlin": ("tree_sitter_kotlin", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
}


def load_language(language_id: str) -> Language:
    """Load and return the tree-sitter Language for ``language_id``.

    Raises KeyError for an unsupported language id.
    """
    if language_id not in _LANGUAGE_MODULES:
        raise KeyError(f"unsupported language: {language_id}")
    module_name, attr = _LANGUAGE_MODULES[language_id]
    mod = importlib.import_module(module_name)
    factory = getattr(mod, attr)
    from tree_sitter import Language

    return Language(factory())


def make_parser(language_id: str) -> Parser:
    """Return a tree-sitter Parser configured for ``language_id``."""
    from tree_sitter import Parser

    return Parser(load_language(language_id))


def parse_source(language_id: str, source: str | bytes) -> Tree:
    """Parse untrusted ``source`` into a tree-sitter Tree; the code is never executed."""
    if isinstance(source, str):
        source = source.encode("utf-8")
    return make_parser(language_id).parse(source)


def parse_file(language_id: str, path: Path) -> Tree:
    """Read ``path`` and parse its (untrusted) contents into a tree-sitter Tree."""
    return parse_source(language_id, path.read_bytes())


def supported_languages() -> frozenset[str]:
    """Return the set of language ids with a registered tree-sitter grammar."""
    return frozenset(_LANGUAGE_MODULES)
