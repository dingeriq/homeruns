from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Path

from app.database.session import session_scope
from app.models.prediction_detail import PredictionDetail
from app.models.schemas import Prediction
from app.services.prediction_detail import build_prediction_detail

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/today", response_model=List[Prediction])
async def predictions_today() -> List[Prediction]:
    """Placeholder — real model output arrives in a later phase."""
    return []


@router.get("/{player_id}", response_model=PredictionDetail)
async def prediction_detail(player_id: int = Path(..., ge=1)) -> PredictionDetail:
    """Structured prediction detail for a single hitter.

    Returns every section the DingerIQ model will consume. Sections that are not
    yet backed by ingested data are null and listed in ``data_availability``;
    no probability, confidence or factor weight is fabricated.
    """
    with session_scope() as s:
        detail = build_prediction_detail(s, player_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")
    return detail
