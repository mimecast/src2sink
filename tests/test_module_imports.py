"""Import every src2sink module.

Guards against import-time breakage and covers thin modules (per-language
extractor stubs, models, mermaid) that are otherwise never imported by the
functional tests.
"""

from __future__ import annotations

import importlib
import pkgutil

import src2sink


def test_all_modules_import():
    failures = []
    for mod in pkgutil.walk_packages(src2sink.__path__, prefix="src2sink."):
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{mod.name}: {type(exc).__name__}: {exc}")
    assert not failures, "import failures:\n" + "\n".join(failures)
