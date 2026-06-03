"""Destructive cleanup utilities.

Used by `scripts/clean.sh`. Every function here removes data from
disk. Each is opt-in (scripts/clean.sh requires a flag); none are
called by normal init paths.

Functions:
  wipe_audit_db()  — delete the audit SQLite file (and its parent
                     directory if it becomes empty).
  wipe_hf_cache()  — delete the HF dataset cache directory.
  wipe_all()       — both of the above, sequentially.

All return a list of paths that were actually deleted, so the caller
can report exactly what happened. Missing paths are silently skipped
(idempotent: running cleanup twice doesn't raise).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import config


def wipe_audit_db(db_path: str | None = None) -> list[Path]:
    """Delete the audit SQLite file.

    Args:
        db_path: explicit override. None = AUDIT_DB_PATH env or default.

    Returns a list of paths actually removed (empty if nothing existed).
    """
    p = config.audit_db_path(db_path)
    # Refuse to wipe :memory: (no-op; nothing to delete).
    if str(p) == ":memory:":
        return []

    removed: list[Path] = []
    if p.exists():
        p.unlink()
        removed.append(p)
    # If parent dir is now empty AND was the default hidden dir, remove it.
    parent = p.parent
    if (
        parent.exists()
        and parent.name == config.DEFAULT_AUDIT_DB_DIR
        and not any(parent.iterdir())
    ):
        parent.rmdir()
        removed.append(parent)
    return removed


def wipe_hf_cache() -> list[Path]:
    """Delete the HF dataset cache directory and everything in it.

    The directory may be large (~12 MB for this dataset, more if the
    user has cached other datasets via HF_HOME). Function does not
    try to be selective; the whole cache_dir goes.

    Returns a list with the cache_dir if it was removed.
    """
    p = config.hf_cache_path()
    removed: list[Path] = []
    if p.exists():
        shutil.rmtree(p)
        removed.append(p)
    return removed


def wipe_all() -> list[Path]:
    """Run both wipes. Returns the combined list of removed paths."""
    return wipe_audit_db() + wipe_hf_cache()
