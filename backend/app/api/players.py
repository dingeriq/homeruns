from __future__ import annotations

from typing import List

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.database import models
from app.database.session import session_scope
from app.models.schemas import Player

router = APIRouter(prefix="/players", tags=["players"])


@router.get("", response_model=List[Player])
async def list_players(limit: int = Query(500, ge=1, le=5000)) -> List[Player]:
    with session_scope() as s:
        rows = s.execute(
            select(models.Player).order_by(models.Player.full_name).limit(limit)
        ).scalars().all()
        return [
            Player(
                id=p.id,
                full_name=p.full_name,
                team_id=p.team_id or 0,
                team_abbreviation=p.team_abbreviation or "",
                position=p.position or "",
                bats=p.bats or "",
                throws=p.throws or "",
            )
            for p in rows
        ]
