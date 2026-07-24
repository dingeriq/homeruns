"""Admin endpoints — trigger data sync manually."""
from __future__ import annotations

from fastapi import APIRouter

from app.services.sync import run_full_sync

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/sync")
async def trigger_sync() -> dict:
    counts = await run_full_sync()
    return {"status": "ok", "synced": counts}
