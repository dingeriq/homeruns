"""Read-only sportsbook market endpoints.

These serve the *latest* snapshot per book/outcome while every historical
snapshot stays in ``odds_snapshots`` for line-movement analysis. Nothing here
produces or influences a DingerIQ HR probability.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Path, Query

from app.config import settings
from app.services.odds_service import HR_MARKETS, odds_for_date, odds_for_player

router = APIRouter(prefix="/odds", tags=["odds"])


@router.get("/today")
async def odds_today(
    on: Optional[date] = Query(None, description="Slate date (default: today)"),
    market: Optional[str] = Query(None, description="Filter to a single market key"),
) -> Dict[str, Any]:
    payload: Dict[str, Any] = await asyncio.to_thread(odds_for_date, on)
    if market:
        payload["odds"] = [row for row in payload["odds"] if row["market"] == market]
        payload["count"] = len(payload["odds"])
        payload["filtered_market"] = market
    payload["configured"] = settings.odds_is_configured
    if not payload["configured"] and payload["status"] == "unavailable":
        payload["reason"] = (
            "ODDS_API_KEY is not set in the backend environment, so no odds have been ingested."
        )
    payload["hr_markets"] = list(HR_MARKETS)
    return payload


@router.get("/player/{player_id}")
async def odds_player(
    player_id: int = Path(..., ge=1),
    on: Optional[date] = Query(None, description="Restrict to one slate date"),
) -> Dict[str, Any]:
    return await asyncio.to_thread(odds_for_player, player_id, on)
