from __future__ import annotations

from datetime import date
from typing import List

from fastapi import APIRouter
from sqlalchemy import select

from app.database import models
from app.database.session import session_scope
from app.models.schemas import Game

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/today", response_model=List[Game])
async def games_today() -> List[Game]:
    today = date.today()
    with session_scope() as s:
        rows = s.execute(
            select(models.Game).where(models.Game.game_date == today).order_by(models.Game.game_datetime)
        ).scalars().all()
        return [
            Game(
                game_id=g.game_id,
                game_date=g.game_datetime,
                home_team=g.home_team,
                away_team=g.away_team,
                venue=g.venue,
                status=g.status,
                home_probable_pitcher=g.home_probable_pitcher,
                away_probable_pitcher=g.away_probable_pitcher,
            )
            for g in rows
        ]
