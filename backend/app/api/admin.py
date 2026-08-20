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
from app.services.player_backfill import backfill_players
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


@router.post("/players-backfill")
async def trigger_player_backfill(
    limit: Optional[int] = Query(
        None, ge=1, description="Cap the number of missing ids processed this run"
    ),
    batch_size: int = Query(50, ge=1, le=100, description="MLB /people ids per request"),
) -> dict:
    """Resolve Statcast person ids missing from ``players`` via the MLB Stats API.

    Read-then-upsert only: canonical ids are never changed, no name matching,
    and an existing non-null field is never overwritten with NULL.
    """
    with track_job("manual_player_backfill"):
        result = await backfill_players(limit=limit, batch_size=batch_size)
    record_synced_counts(
        {
            "players_backfilled": result.get("players_inserted", 0),
            "players_identity_updated": result.get("players_updated", 0),
            "players_unresolved": result.get("unresolved_ids", 0),
        }
    )
    return {"status": "ok", "player_backfill": result}


# ---------------------------------------------------------------------------
# V1 model controls — manual only. Nothing here runs on startup or in the
# scheduler; snapshots and training are always operator-triggered.
# ---------------------------------------------------------------------------


def _build_snapshots_sync(start: date, end: date, limit: Optional[int], window_days: int) -> dict:
    with session_scope() as s:
        return build_snapshots(s, start, end, limit=limit, window_days=window_days)


def _train_sync(start: Optional[date], end: Optional[date], min_rows: int) -> dict:
    with session_scope() as s:
        rows = load_training_rows(s, start=start, end=end)
        summary = corpus_summary(s)
    artifact = train_model(rows, min_rows=min_rows)
    path = save_artifact(artifact)
    return {
        "corpus": summary,
        "rows_used": len(rows),
        "model_version": artifact.get("model_version"),
        "feature_set_version": artifact.get("feature_set_version"),
        "trained_at": artifact.get("trained_at"),
        "n_train": artifact.get("n_train"),
        "n_holdout": artifact.get("n_holdout"),
        "base_rate": artifact.get("base_rate"),
        "metrics": artifact.get("metrics"),
        "artifact_path": str(path),
    }


@router.post("/build-snapshots")
async def trigger_build_snapshots(
    start: date = Query(..., description="First game date (inclusive)"),
    end: date = Query(..., description="Last game date (inclusive)"),
    limit: Optional[int] = Query(
        None, ge=1, description="Cap the number of (batter, game) pairs processed"
    ),
    window_days: int = Query(30, ge=1, le=365, description="Trailing feature window"),
) -> dict:
    """Materialise leak-safe labelled ``feature_snapshots`` for a date range."""
    if start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    with track_job("manual_build_snapshots"):
        result = await asyncio.to_thread(_build_snapshots_sync, start, end, limit, window_days)
    record_synced_counts({"feature_snapshots": result.get("snapshots_written", 0)})
    return {"status": "ok", "snapshots": result}


@router.post("/train-model")
async def trigger_train_model(
    start: Optional[date] = Query(None, description="Restrict corpus to game_date >= start"),
    end: Optional[date] = Query(None, description="Restrict corpus to game_date <= end"),
    min_rows: int = Query(200, ge=50, description="Minimum labelled rows required to train"),
) -> dict:
    """Train + calibrate the V1 model from stored snapshots and persist it."""
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    try:
        with track_job("manual_train_model"):
            result = await asyncio.to_thread(_train_sync, start, end, min_rows)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    record_synced_counts({"model_training_rows": result.get("rows_used", 0)})
    return {"status": "ok", "training": result}


@router.get("/model-status")
async def get_model_status() -> dict:
    """Registered model artifact + labelled corpus summary (read-only)."""
    status = model_status()
    corpus: Optional[dict]
    try:
        with session_scope() as s:
            corpus = corpus_summary(s)
    except Exception:
        corpus = None
    return {"model": status, "corpus": corpus}

