"""Shared constants for metabase extractors (v1 + v2)."""

from __future__ import annotations

import re

SKIP_DIRS = {
    ".git", ".idea", ".vscode", ".gradle", ".mvn", ".pytest_cache",
    "node_modules", "target", "build", "dist", "out", "bin",
    "vendor", ".terraform", "__pycache__", ".cache", ".tox",
    "coverage", ".nyc_output", ".next", ".nuxt", ".svelte-kit",
    "tmp", "temp", ".DS_Store",
    ".tmp_gitlab_archives",
}

SOURCE_EXTENSIONS = {
    ".java": "java", ".kt": "kotlin", ".scala": "scala",
    ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".py": "python", ".go": "go",
}

CONFIG_FILE_NAMES = {
    "application.yml", "application.yaml", "application.properties",
    "bootstrap.yml", "bootstrap.yaml", "bootstrap.properties",
}

MAX_FILE_BYTES = 1_500_000

# Paths whose contents are not the deployed service. Matched against whole path
# *segments*, so `src/latest/x.go` is not a test because of the word "test" in
# it — see `_CAMEL_TEST` below for why that needs saying.
_TEST_DIR_SEGMENTS = (
    r"test|tests|spec|specs|__tests__|fixtures?|examples?|samples?|"
    r"sandbox|playground|local|demo|mock|mocks|stub|stubs|"
    r"src/test|src/it|integration[-_]?test|"
    r"qa[-_]?test|e2e|cypress|"
    # Lowercase compound conventions, named explicitly rather than caught by a
    # suffix rule — a rule broad enough to catch these also catches `latest`.
    r"load[-_]?tests?|smoke[-_]?tests?|perf[-_]?tests?|acceptance[-_]?tests?"
)

# A camelCase test class directory — `StockControllerTest`, `fooTests`.
# **Case-sensitive on purpose.** Under the surrounding `IGNORECASE` this branch
# read as "any segment ending in test", which silently excluded `latest/`,
# `protest/`, `contest/`, `attest/` and `greatest/` from *all* extraction — a
# versioned `api/latest/` directory being the realistic one. Requiring a literal
# capital `T` keeps `FooTest` and drops the English words.
_CAMEL_TEST = r"(?-i:[a-zA-Z][a-zA-Z0-9]*Tests?)"

# Test files that live beside the code they test rather than under a test
# directory: `routes.spec.ts`, `handler_test.go`, `test_views.py`. Segment
# matching cannot see these, so 64% of `OI-37`'s false endpoints sat in files
# like them. Anchored to source extensions so `openapi.spec.yaml` — an API
# *specification* — is not mistaken for a test.
_TEST_FILE_NAMES = (
    r"[^/]+\.(?:spec|test|cy)\.(?:js|jsx|mjs|cjs|ts|tsx)|"
    r"[^/]+_test\.go|"
    r"test_[^/]+\.py|[^/]+_test\.py|"
    r"[^/]+Tests?\.(?:java|kt)"
)

TEST_PATH_RX = re.compile(
    r"(?:^|/)(?:"
    + _TEST_DIR_SEGMENTS
    + r"|"
    + _CAMEL_TEST
    + r")(?:/|$)"
    r"|"
    r"(?:^|/)(?:" + _TEST_FILE_NAMES + r")$",
    re.IGNORECASE,
)

WEAK_ALGOS = frozenset({
    "MD5", "MD4", "SHA1", "SHA-1", "DES", "RC4", "RC2", "BLOWFISH",
})
