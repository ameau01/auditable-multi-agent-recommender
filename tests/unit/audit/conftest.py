"""Shared fixtures for audit-store unit tests.

Uses in-memory SQLite (`:memory:` + StaticPool). The store fixture
spins up a fresh per-test database with no filesystem I/O — no mkdir,
no fsync, no dotenv lookup. Roughly 50x faster than file-backed and
removes the macOS APFS / Time Machine hot-spot that made earlier
file-per-test fixtures hang in some environments.
"""

from __future__ import annotations

import pytest

from src.audit import AuditStore
from src.audit.store import IN_MEMORY


@pytest.fixture
def store() -> AuditStore:
    """Fresh in-memory AuditStore for one test. Schema initialized."""
    s = AuditStore(db_path=IN_MEMORY)
    s.initialize()
    return s
