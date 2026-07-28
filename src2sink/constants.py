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

TEST_PATH_RX = re.compile(
    r"(?:^|/)(?:"
    r"test|tests|spec|specs|__tests__|fixtures?|examples?|samples?|"
    r"sandbox|playground|local|demo|mock|mocks|stub|stubs|"
    r"src/test|src/it|integration[-_]?test|"
    r"qa[-_]?test|e2e"
    r"|"
    r"[a-z][a-zA-Z0-9]*Tests?"
    r")(?:/|$)",
    re.IGNORECASE,
)

WEAK_ALGOS = frozenset({
    "MD5", "MD4", "SHA1", "SHA-1", "DES", "RC4", "RC2", "BLOWFISH",
})
