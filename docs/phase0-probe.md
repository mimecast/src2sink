# Phase 0 — Registry index probe report (tree-sitter)

Probed **2026-05-18** against (`pypi-all` default index in `pyproject.toml`).

| Package | Result | Version installed | Notes |
| --- | --- | --- | --- |
| `tree-sitter` | **PASS** | 0.25.2 | Python bindings |
| `tree-sitter-languages` | **FAIL** | — | No wheel for Python **3.14** (`cp314`); bundle only ships up to `cp312` |
| `tree-sitter-java` | **PASS** | 0.23.5 | Per-language wheel |
| `tree-sitter-python` | **PASS** | 0.25.0 | |
| `tree-sitter-javascript` | **PASS** | 0.25.0 | |
| `tree-sitter-typescript` | **PASS** | 0.23.2 | `language_typescript` + `language_tsx` |
| `tree-sitter-go` | **PASS** | 0.25.0 | |
| `tree-sitter-kotlin` | **PASS** | 1.1.0 | |
| `pytest` (dev) | **PASS** | 9.0.3 | For `metabase/tests/` |

## Decision

Use **per-language grammar wheels** (not `tree-sitter-languages`). All fleet languages required for Phase 1 resolve on Python 3.14 via registry index.

## Verification

```sh
uv run pytest metabase/tests/test_phase0_smoke.py -q
```

Smoke test parses minimal snippets for: `java`, `python`, `javascript`, `typescript`, `tsx`, `go`, `kotlin`.

## Fallbacks (not needed in Phase 0)

If a grammar is later quarantined (403 from Index), do **not** bypass the registry. Quote the error, open lifecycle approval, and defer that language to Phase 1.5 with:

- Java: `javalang` (lexer-only fallback)
- Python: stdlib `ast`
- JS: remain blocked until grammar clears
