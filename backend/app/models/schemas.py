"""Pydantic response models."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "healthy"
    service: str = "DingerIQ API"


class Team(BaseModel):
    id: int
    abbreviation: str
    name: str
    league: str = Field(..., description="AL or NL")
    division: str


class Player(BaseModel):
    id: int
    full_name: str
    team_id: int
    team_abbreviation: str
    position: str
    bats: str
    throws: str


class Game(BaseModel):
    game_id: int
    game_date: datetime
    home_team: str
    away_team: str
    venue: str
    status: str
    home_probable_pitcher: Optional[str] = None
    away_probable_pitcher: Optional[str] = None



class Prediction(BaseModel):
    player_id: int
    player_name: str
    game_id: int
    hr_probability: float
    confidence: float


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


__all__ = [
    "HealthResponse",
    "Team",
    "Player",
    "Game",
    "Prediction",
    "ErrorResponse",
    "List",
]
