"""Weather endpoints — OpenWeather data resolved per game / venue / first pitch."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path
from sqlalchemy import select

from app.database import models
from app.database.session import session_scope
from app.models.schemas import GameWeatherResponse
from app.services.weather_service import weather_for_games

router = APIRouter(prefix="/weather", tags=["weather"])


def _games_on(day: date) -> List[models.Game]:
    with session_scope() as s:
        rows = s.execute(
            select(models.Game)
            .where(models.Game.game_date == day)
            .order_by(models.Game.game_datetime)
        ).scalars().all()
        return [
            models.Game(
                game_id=g.game_id,
                game_datetime=g.game_datetime,
                venue=g.venue,
                venue_id=g.venue_id,
                home_team=g.home_team,
                away_team=g.away_team,
            )
            for g in rows
        ]


def _to_response(game: models.Game, weather: Optional[dict]) -> GameWeatherResponse:
    payload = {
        "game_id": game.game_id,
        "game_datetime": game.game_datetime,
        "venue": game.venue,
        "venue_id": game.venue_id,
        "home_team": game.home_team,
        "away_team": game.away_team,
        "status": "available" if weather else "unavailable",
    }
    if weather:
        payload.update(
            {
                key: weather.get(key)
                for key in (
                    "forecast_time",
                    "forecast_offset_minutes",
                    "is_forecast",
                    "temperature_f",
                    "feels_like_f",
                    "humidity_pct",
                    "pressure_hpa",
                    "wind_speed_mph",
                    "wind_gust_mph",
                    "wind_deg",
                    "wind_direction",
                    "cloud_pct",
                    "precipitation_prob",
                    "conditions",
                    "description",
                    "roof_status",
                    "source",
                    "fetched_at",
                )
            }
        )
    else:
        payload["reason"] = (
            "No OpenWeather record stored for this game yet. Run POST /admin/weather-sync "
            "or wait for the scheduled refresh."
        )
    return GameWeatherResponse(**payload)


@router.get("/today", response_model=List[GameWeatherResponse])
async def weather_today() -> List[GameWeatherResponse]:
    games = await asyncio.to_thread(_games_on, date.today())
    weather = await asyncio.to_thread(weather_for_games, [g.game_id for g in games])
    return [_to_response(g, weather.get(g.game_id)) for g in games]


@router.get("/game/{game_id}", response_model=GameWeatherResponse)
async def weather_by_game(game_id: int = Path(..., gt=0)) -> GameWeatherResponse:
    def _load():
        with session_scope() as s:
            g = s.get(models.Game, game_id)
            if g is None:
                return None
            return models.Game(
                game_id=g.game_id,
                game_datetime=g.game_datetime,
                venue=g.venue,
                venue_id=g.venue_id,
                home_team=g.home_team,
                away_team=g.away_team,
            )

    game = await asyncio.to_thread(_load)
    if game is None:
        raise HTTPException(status_code=404, detail=f"game {game_id} not found")
    weather = await asyncio.to_thread(weather_for_games, [game_id])
    return _to_response(game, weather.get(game_id))
