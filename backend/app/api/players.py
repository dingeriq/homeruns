from __future__ import annotations

from typing import List

from fastapi import APIRouter

from app.models.schemas import Player
from app.services import mlb_data

router = APIRouter(prefix="/players", tags=["players"])


@router.get("", response_model=List[Player])
async def list_players() -> List[Player]:
    return mlb_data.list_players()
