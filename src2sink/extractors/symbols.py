"""Per-file identifier -> string-literal maps shared by the extractors.

A call site that reaches its endpoint or its query through a named constant
(``host + SUBMIT_PATH``, ``SAFE + " AND ref = '"``) resolves to nothing when only
the text around the call is inspected. Building a cheap per-file map of
identifier -> literal, then resolving the identifiers referenced nearby, recovers
the value without a symbol table or type resolution.

The map is filtered by a caller-supplied predicate so each extractor records only
literals it can use — endpoint-shaped ones for ``http-out``, SQL-shaped ones for
the ``sql`` family. One implementation, two vocabularies: keeping separate copies
of the assignment patterns is how the two bounded literal bodies in
``SQL_SOURCE_RX`` drifted apart and lost the embedded-quote case (OI-8).

Scans untrusted source text; matches are only recorded, never evaluated.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

# `NAME = "literal"` / `NAME: Type = "literal"` / `NAME := "literal"` across
# Java, Kotlin, Python, TS and Go. Both runs are length-bounded so the pattern
# stays linear on hostile input (see tests/test_redos_bounds.py).
SYMBOL_ASSIGN_RX = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]{0,63})\s*"
    r"(?::\s*[A-Za-z_][A-Za-z0-9_<>\[\].,? ]{0,63})?"
    r"\s*(?::?=)\s*(?:[fbruFBRU]{0,2})?[\"']([^\"'\n]{1,240})[\"']"
)
# Java/Kotlin enum members that pass their value to the constructor:
# `SUBMIT_SYNC("/v1/query/sync")`.
ENUM_MEMBER_RX = re.compile(
    r"\b([A-Z][A-Z0-9_]{1,63})\s*\(\s*[\"']([^\"'\n]{1,240})[\"']"
)
# An identifier on either side of a `+`, i.e. taking part in a concatenation.
CONCAT_IDENT_RX = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]{0,63})\s*\+|\+\s*([A-Za-z_][A-Za-z0-9_]{0,63})\b"
)

MAX_SYMBOLS_PER_FILE = 400


def build_symbol_table(
    source: str, is_interesting: Callable[[str], bool]
) -> dict[str, str]:
    """Map identifier -> string literal declared in this file, filtered by predicate.

    Only literals ``is_interesting`` accepts are recorded, so the table stays
    small and resolving from it cannot invent a value that is not in the source.
    """
    symbols: dict[str, str] = {}
    for rx in (SYMBOL_ASSIGN_RX, ENUM_MEMBER_RX):
        for m in rx.finditer(source):
            if len(symbols) >= MAX_SYMBOLS_PER_FILE:
                return symbols
            name, value = m.group(1), m.group(2)
            if name in symbols or not is_interesting(value):
                continue
            symbols[name] = value
    return symbols


def iter_concatenated_symbols(
    region: str, symbols: dict[str, str]
) -> Iterator[tuple[int, str, str]]:
    """Yield ``(offset, identifier, literal)`` for table symbols joined by ``+``.

    A constant referenced *verbatim* is not a construction — only one taking part
    in a concatenation is. That distinction is what keeps a base query used as-is
    reported as parameterised while the same constant with a clause appended is
    reported as mixed (OI-11).
    """
    for m in CONCAT_IDENT_RX.finditer(region):
        name = m.group(1) or m.group(2)
        value = symbols.get(name)
        if value is not None:
            yield m.start(), name, value
