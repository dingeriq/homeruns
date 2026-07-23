"""Database placeholder.

Real PostgreSQL wiring (Phase 2 schema) attaches here in a later phase.
"""
from __future__ import annotations


async def check_connection() -> bool:
    """Return True. Replaced with real DB ping when the database lands."""
    return True
