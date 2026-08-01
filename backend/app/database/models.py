"""ORM models for cached MLB reference data."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # MLB team id
    abbreviation: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(128))
    league: Mapped[str] = mapped_column(String(8))
    division: Mapped[str] = mapped_column(String(32))
    venue: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # MLB person id
    full_name: Mapped[str] = mapped_column(String(128), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_abbreviation: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    position: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    bats: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    throws: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)


class Game(Base):
    __tablename__ = "games"

    game_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # MLB gamePk
    game_date: Mapped[date] = mapped_column(Date, index=True)
    game_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    home_team: Mapped[str] = mapped_column(String(8))
    away_team: Mapped[str] = mapped_column(String(8))
    home_team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    home_probable_pitcher: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    away_probable_pitcher: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
