"""Pydantic response models."""
from __future__ import annotations

from datetime import datetime, date as _date
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
    #: Absolute first pitch (UTC). Kept under the historical field name for
    #: backwards compatibility with existing clients.
    game_date: datetime
    #: Official MLB slate ("baseball day") date this game belongs to.
    slate_date: Optional[_date] = None
    #: Same instant as ``game_date``, explicitly named and always UTC.
    first_pitch_utc: Optional[datetime] = None
    home_team: str
    away_team: str
    venue: str
    status: str
    home_probable_pitcher: Optional[str] = None
    away_probable_pitcher: Optional[str] = None



class GameWeatherResponse(BaseModel):
    game_id: int
    game_datetime: Optional[datetime] = None
    venue: Optional[str] = None
    venue_id: Optional[int] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    status: str = Field(..., description="available | unavailable")
    reason: Optional[str] = None

    forecast_time: Optional[datetime] = None
    forecast_offset_minutes: Optional[int] = None
    is_forecast: Optional[bool] = None
    temperature_f: Optional[float] = None
    feels_like_f: Optional[float] = None
    humidity_pct: Optional[float] = None
    pressure_hpa: Optional[float] = None
    wind_speed_mph: Optional[float] = None
    wind_gust_mph: Optional[float] = None
    wind_deg: Optional[float] = None
    wind_direction: Optional[str] = None
    wind_out_mph: Optional[float] = None
    cloud_pct: Optional[float] = None
    precipitation_prob: Optional[float] = None
    conditions: Optional[str] = None
    description: Optional[str] = None
    roof_status: Optional[str] = None
    source: Optional[str] = None
    fetched_at: Optional[datetime] = None


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
    "GameWeatherResponse",
    "Prediction",
    "ErrorResponse",
    "List",
]
