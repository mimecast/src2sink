"""Per-language tree-sitter extractors (Phase 1+)."""

from .base import (
    load_language,
    make_parser,
    parse_file,
    parse_source,
    supported_languages,
)

__all__ = [
    "load_language",
    "make_parser",
    "parse_file",
    "parse_source",
    "supported_languages",
]
