"""Shared fixtures for audit-store unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.audit import AuditStore


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    """Fresh AuditStore on a tmp_path SQLite file. Initializes schema."""
    db_path = tmp_path / "audit.db"
    s = AuditStore(db_path=str(db_path))
    s.initialize()
    return s
