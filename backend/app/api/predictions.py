from __future__ import annotations

from typing import List

from fastapi import APIRouter

from app.models.schemas import Prediction

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/today", response_model=List[Prediction])
async def predictions_today() -> List[Prediction]:
    """Placeholder — real model output arrives in a later phase."""
    return []
