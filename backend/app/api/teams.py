from __future__ import annotations

from typing import List

from fastapi import APIRouter

from app.models.schemas import Team
from app.services import mlb_data

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=List[Team])
async def list_teams() -> List[Team]:
    return mlb_data.list_teams()
