from __future__ import annotations

from typing import List

from fastapi import APIRouter
from sqlalchemy import select

from app.database import models
from app.database.session import session_scope
from app.models.schemas import Team

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=List[Team])
async def list_teams() -> List[Team]:
    with session_scope() as s:
        rows = s.execute(select(models.Team).order_by(models.Team.abbreviation)).scalars().all()
        return [
            Team(
                id=t.id,
                abbreviation=t.abbreviation,
                name=t.name,
                league=t.league or "",
                division=t.division or "",
            )
            for t in rows
        ]
