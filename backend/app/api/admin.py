"""Admin endpoints — trigger data sync manually and audit data quality."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.monitoring import record_synced_counts, track_job
from app.services.data_audit import run_audit
from app.services.park_factors import compute_park_factors
from app.services.statcast_service import sync_statcast
from app.services.sync import run_full_sync
from app.services.weather_service import OpenWeatherNotConfigured, sync_weather

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/data-audit")
async def data_audit() -> dict:
    """Read-only audit: MLB id integrity + exact Statcast coverage."""
    try:
        return await asyncio.to_thread(run_audit)
    except Exception as exc:  # database down / schema missing
        raise HTTPException(status_code=503, detail=f"audit unavailable: {type(exc).__name__}")



@router.post("/sync")
async def trigger_sync() -> dict:
    with track_job("manual_full_sync"):
        counts = await run_full_sync()
    record_synced_counts(counts)
    return {"status": "ok", "synced": counts}


@router.post("/statcast-sync")
async def trigger_statcast_sync(
    start: Optional[date] = Query(None, description="Start game date (inclusive)"),
    end: Optional[date] = Query(None, description="End game date (inclusive)"),
    season: Optional[int] = Query(None, description="Season year for the Savant query"),
) -> dict:
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    with track_job("manual_statcast_sync"):
        result = await sync_statcast(start=start, end=end, season=season)
    record_synced_counts(result)
    return {"status": "ok", "statcast": result}


@router.post("/weather-sync")
async def trigger_weather_sync(
    on: Optional[date] = Query(None, description="Game date to refresh (default: today)"),
) -> dict:
    try:
        with track_job("manual_weather_sync"):
            result = await sync_weather(on=on)
    except OpenWeatherNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    record_synced_counts({"weather": result.get("weather_stored", 0)})
    return {"status": "ok", "weather": result}


@router.post("/park-factors-sync")
async def trigger_park_factor_sync() -> dict:
    """Recompute venue HR factors (overall + by batter handedness) from Statcast."""
    with track_job("manual_park_factor_sync"):
        result = await asyncio.to_thread(compute_park_factors)
    record_synced_counts({"park_factors": result.get("park_factors_stored", 0)})
    return {"status": "ok", "park_factors": result}
