"""Park factor endpoints — venue HR factors derived from stored Statcast BBE."""
from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.park_factors import list_park_factors

router = APIRouter(prefix="/park-factors", tags=["park-factors"])


@router.get("")
async def park_factors(
    season: Optional[int] = Query(None, ge=1900, le=2100),
    venue_id: Optional[int] = Query(None, ge=1),
) -> List[dict]:
    try:
        return await asyncio.to_thread(list_park_factors, season, venue_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"park factors unavailable: {type(exc).__name__}")
