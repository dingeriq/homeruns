"""Lineup endpoints — confirmed / projected batting orders and PA opportunity."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Path, Query

from app.services.lineups import (
    expected_pa_by_slot,
    lineups_for_date,
    lineups_for_game,
)
from app.database.session import session_scope

router = APIRouter(prefix="/lineups", tags=["lineups"])


@router.get("/today")
async def lineups_today(
    on: Optional[date] = Query(None, description="Slate date (default: today)"),
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = await asyncio.to_thread(lineups_for_date, on)
    return {
        "date": (on or date.today()).isoformat(),
        "count": len(rows),
        "confirmed": sum(1 for r in rows if r["status"] == "confirmed"),
        "projected": sum(1 for r in rows if r["status"] == "projected"),
        "lineups": rows,
        "status": "ok" if rows else "unavailable",
        "reason": None if rows else "No lineup posted or projectable for this slate yet.",
    }


@router.get("/expected-pa")
async def expected_pa() -> Dict[str, Any]:
    """Empirical plate appearances per batting-order slot, measured from our data."""

    def _read() -> Dict[int, Dict[str, Any]]:
        with session_scope() as s:
            return expected_pa_by_slot(s)

    slots = await asyncio.to_thread(_read)
    return {
        "method": "mean distinct Statcast at-bats per stored confirmed lineup slot",
        "slots": {str(k): v for k, v in slots.items()},
    }


@router.get("/game/{game_id}")
async def lineup_for_game(game_id: int = Path(..., ge=1)) -> Dict[str, Any]:
    rows = await asyncio.to_thread(lineups_for_game, game_id)
    return {
        "game_id": game_id,
        "count": len(rows),
        "lineups": rows,
        "status": "ok" if rows else "unavailable",
        "reason": None if rows else "No lineup stored for this game yet.",
    }
