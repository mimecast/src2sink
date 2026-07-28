"""Path-containment helpers for reads derived from untrusted repository content.

The scanner ingests repositories that may be hostile (see docs/threat-model.md,
findings T-1 and T-2). Two primitives must never let a crafted repo read files
outside its own tree:

* a ``.git/HEAD`` symbolic ref (``ref: <path>``) is attacker-controlled and was
  concatenated onto a filesystem path — a traversal read primitive; and
* symlinked files encountered while walking the repo could point anywhere.

These helpers resolve a candidate path and confirm it stays within an allowed
root before it is read.
"""

from __future__ import annotations

from pathlib import Path


def resolve_within(path: Path, root: Path) -> Path | None:
    """Resolve ``path`` and return it only if it stays within ``root``.

    Returns the fully-resolved path when it is ``root`` itself or a descendant
    of ``root``; returns ``None`` if it escapes ``root`` or cannot be resolved
    (e.g. a broken symlink or a permission error). ``root`` is resolved too, so
    symlinked roots compare correctly.
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except OSError:
        return None
    if resolved == root_resolved or resolved.is_relative_to(root_resolved):
        return resolved
    return None


def is_within(path: Path, root: Path) -> bool:
    """True if ``path`` resolves to a location within ``root`` (see resolve_within)."""
    return resolve_within(path, root) is not None


def is_escaping_symlink(path: Path, root: Path) -> bool:
    """True if ``path`` is a symlink whose target resolves outside ``root``.

    Non-symlinks and in-tree symlinks return ``False`` (safe to read). Used to
    skip files that would exfiltrate content from outside the repository.
    """
    try:
        if not path.is_symlink():
            return False
    except OSError:
        return True
    return not is_within(path, root)
