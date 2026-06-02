"""Audit trail persistence layer.

Public surface:
  - `AuditStore` — the only class callers instantiate. Handles cycle
    lifecycle, record insertion, query routing, and SQLite connection
    management with PRAGMA foreign_keys=ON.

Internal modules:
  - `schema` — SQLAlchemy Core table definitions and indexes.
  - `store` — AuditStore implementation.
  - `queries` — pure-read CTE walks and projections.
  - `composer` — reconstruct a Composite from a cycle's records.

See `docs/audit-trail.md` for the design rationale.
"""

from __future__ import annotations

from .store import AuditStore

__all__ = ["AuditStore"]
