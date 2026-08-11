"""Admin endpoints — trigger data sync manually and audit data quality."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.monitoring import record_synced_counts, track_job
from app.services.data_audit import run_audit
from app.services.db_usage import run_db_usage
from app.services.lineups import sync_lineups
from app.services.park_factors import compute_park_factors
from app.services.statcast_audit import run_statcast_audit
from app.services.statcast_service import sync_statcast
from app.services.sync import run_full_sync
from app.services.odds_service import OddsApiNotConfigured, sync_odds
from app.services.weather_service import OpenWeatherNotConfigured, sync_weather

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/data-audit")
async def data_audit() -> dict:
    """Read-only audit: MLB id integrity + exact Statcast coverage."""
    try:
        return await asyncio.to_thread(run_audit)
    except Exception as exc:  # database down / schema missing
        raise HTTPException(status_code=503, detail=f"audit unavailable: {type(exc).__name__}")


@router.get("/db-usage")
async def db_usage() -> dict:
    """Read-only PostgreSQL resource usage: size, indexes, connections, load."""
    try:
        return await asyncio.to_thread(run_db_usage)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"db usage unavailable: {type(exc).__name__}"
        )




@router.get("/statcast-audit")
async def statcast_audit(
    start: Optional[date] = Query(None, description="Restrict aggregates to game_date >= start"),
    end: Optional[date] = Query(None, description="Restrict aggregates to game_date <= end"),
) -> dict:
    """Read-only, deep Statcast coverage + trainability audit.

    Without start/end this audits the whole table. Supplying a range uses the
    indexed game_date filter, which keeps the query cheap during an ingest.
    """
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    try:
        return await asyncio.to_thread(run_statcast_audit, start, end)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"statcast audit unavailable: {type(exc).__name__}"
        )



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
    window_days: Optional[int] = Query(
        None, ge=1, le=31, description="Fetch window size in days (default 2)"
    ),
    pause_seconds: Optional[float] = Query(
        None, ge=0, le=60, description="Pause between Savant windows (default from settings)"
    ),
) -> dict:
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    with track_job("manual_statcast_sync"):
        result = await sync_statcast(
            start=start,
            end=end,
            season=season,
            window_days=window_days,
            pause_seconds=pause_seconds,
        )

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


@router.post("/lineups-sync")
async def trigger_lineup_sync(
    on: Optional[date] = Query(None, description="Slate date to ingest (default: today)"),
) -> dict:
    """Ingest confirmed lineups (projecting from stored history where absent)."""
    with track_job("manual_lineup_sync"):
        result = await sync_lineups(on=on)
    record_synced_counts({"lineups": result.get("lineup_slots_stored", 0)})
    return {"status": "ok", "lineups": result}


@router.post("/odds-sync")
async def trigger_odds_sync(
    on: Optional[date] = Query(None, description="Slate date to tag snapshots with"),
    include_player_props: bool = Query(True, description="Also pull per-event HR props"),
) -> dict:
    """Ingest sportsbook markets. Snapshots are appended, never overwritten."""
    try:
        with track_job("manual_odds_sync"):
            result = await sync_odds(on=on, include_player_props=include_player_props)
    except OddsApiNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    record_synced_counts({"odds": result.get("snapshots_stored", 0)})
    return {"status": "ok", "odds": result}
