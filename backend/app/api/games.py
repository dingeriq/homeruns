from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app.database import models
from app.database.session import session_scope
from app.models.schemas import Game

router = APIRouter(prefix="/games", tags=["games"])


def _slate_game_ids(session, day: date) -> set[int]:
    """Game ids that the slate for ``day`` actually references.

    The MLB schedule date is the *local* game date, but ``games.game_date`` was
    historically stored as the UTC date of first pitch, so night games land on
    the following calendar day. Lineups and daily predictions are keyed on the
    slate date, so they are the authoritative source of the slate membership.
    """
    ids: set[int] = set()
    for model in (models.GameLineup, models.DailyPrediction):
        ids.update(
            int(gid)
            for gid in session.execute(
                select(model.game_id).where(model.game_date == day).distinct()
            ).scalars()
            if gid is not None
        )
    return ids


@router.get("/today", response_model=List[Game])
async def games_today(
    on: Optional[date] = Query(None, description="Slate date (default: today)"),
) -> List[Game]:
    day = on or date.today()
    with session_scope() as s:
        slate_ids = _slate_game_ids(s, day)
        conditions = [models.Game.game_date == day]
        if slate_ids:
            conditions.append(models.Game.game_id.in_(slate_ids))
        rows = (
            s.execute(
                select(models.Game)
                .where(or_(*conditions))
                .order_by(models.Game.game_datetime)
            )
            .scalars()
            .all()
        )
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
