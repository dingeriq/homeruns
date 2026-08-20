from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, Query

from app.database.session import session_scope
from app.models.prediction_detail import PredictionDetail
from app.models.schemas import Prediction
from app.services.prediction_detail import build_prediction_detail
from app.services.scoring import score_slate

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/today", response_model=List[Prediction])
async def predictions_today(
    on: Optional[date] = Query(None, description="Slate date (default: today)"),
    limit: Optional[int] = Query(None, ge=1, le=500, description="Cap returned rows"),
) -> List[Prediction]:
    """Ranked HR probabilities for today's stored starters.

    Returns an empty list when no model artifact is registered or no lineup is
    stored for the slate — probabilities are never fabricated.
    """
    with session_scope() as s:
        result = score_slate(s, on, limit=limit)
    if result.get("status") != "ok":
        return []
    return [
        Prediction(
            player_id=p["player_id"],
            player_name=p["player_name"],
            game_id=p["game_id"],
            hr_probability=p["hr_probability"],
            confidence=p["confidence"],
        )
        for p in result.get("predictions", [])
    ]


@router.get("/{player_id}", response_model=PredictionDetail)
async def prediction_detail(player_id: int = Path(..., ge=1)) -> PredictionDetail:
    """Structured prediction detail for a single hitter.

    Returns every section the DingerIQ model consumes. Sections that are not
    yet backed by ingested data are null and listed in ``data_availability``;
    no probability, confidence or factor weight is fabricated.
    """
    with session_scope() as s:
        detail = build_prediction_detail(s, player_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")
    return detail
