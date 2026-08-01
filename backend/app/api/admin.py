"""Admin endpoints — trigger data sync manually."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.statcast_service import sync_statcast
from app.services.sync import run_full_sync

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/sync")
async def trigger_sync() -> dict:
    counts = await run_full_sync()
    return {"status": "ok", "synced": counts}


@router.post("/statcast-sync")
async def trigger_statcast_sync(
    start: Optional[date] = Query(None, description="Start game date (inclusive)"),
    end: Optional[date] = Query(None, description="End game date (inclusive)"),
    season: Optional[int] = Query(None, description="Season year for the Savant query"),
) -> dict:
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    result = await sync_statcast(start=start, end=end, season=season)
    return {"status": "ok", "statcast": result}
