from __future__ import annotations

from typing import List

from fastapi import APIRouter

from app.models.schemas import Game
from app.services import mlb_data

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/today", response_model=List[Game])
async def games_today() -> List[Game]:
    return mlb_data.list_games_today()
