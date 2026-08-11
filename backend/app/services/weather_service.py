"""OpenWeather ingestion keyed by MLB venue and first-pitch time.

Two OpenWeather endpoints are used (both available on the free tier):

* ``/data/2.5/forecast``  — 5 day / 3 hour forecast, used for upcoming games.
  The 3-hour slot nearest to first pitch is selected.
* ``/data/2.5/weather``   — current conditions, used when first pitch is
  already within the last few hours (or in the next 30 minutes), which is more
  accurate than the coarse forecast grid.

Nothing is fabricated: if the venue has no coordinates, or OpenWeather returns
no usable slot, no row is written and the game is reported as unavailable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.database import models
from app.database.session import session_scope
from app.services.sync import _upsert

logger = logging.getLogger("dingeriq.weather")

_TIMEOUT = httpx.Timeout(20.0, connect=8.0)

_COMPASS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

# Forecast slots are 3h apart; anything further from first pitch than this is
# not a meaningful representation of game conditions.
MAX_SLOT_DISTANCE = timedelta(hours=3)


class OpenWeatherNotConfigured(RuntimeError):
    """Raised when no OPENWEATHER_API_KEY is present in the environment."""


def compass(deg: Optional[float]) -> Optional[str]:
    if deg is None:
        return None
    return _COMPASS[int((float(deg) % 360) / 22.5 + 0.5) % 16]


def _mps_to_mph(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value) * 2.236936, 1)


def roof_status_for(roof_type: Optional[str]) -> Optional[str]:
    """Map the MLB venue roof type onto a game-time roof status.

    Retractable roofs are genuinely unknown ahead of time — the MLB API does
    not publish the open/closed decision — so they are reported as such
    rather than guessed.
    """
    if not roof_type:
        return None
    normalized = roof_type.strip().lower()
    if normalized in ("dome", "indoor", "closed"):
        return "closed"
    if normalized in ("open", "outdoor"):
        return "open"
    if "retract" in normalized:
        return "retractable"
    return None


class OpenWeatherClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or settings.openweather_api_key
        self.base_url = base_url or settings.openweather_api_base
        if not self.api_key:
            raise OpenWeatherNotConfigured(
                "OPENWEATHER_API_KEY is not set in the environment"
            )

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        query = {**params, "appid": self.api_key, "units": "imperial"}
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.get(url, params=query)
                    resp.raise_for_status()
                    return resp.json()
        raise RuntimeError("unreachable")

    async def forecast(self, lat: float, lon: float) -> Dict[str, Any]:
        return await self._get("/data/2.5/forecast", {"lat": lat, "lon": lon})

    async def current(self, lat: float, lon: float) -> Dict[str, Any]:
        return await self._get("/data/2.5/weather", {"lat": lat, "lon": lon})


def _slot_to_row(slot: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize either endpoint's payload — both share this response shape."""
    main = slot.get("main") or {}
    wind = slot.get("wind") or {}
    clouds = slot.get("clouds") or {}
    weather = (slot.get("weather") or [{}])[0]
    deg = wind.get("deg")
    pop = slot.get("pop")
    return {
        "temperature_f": _round(main.get("temp")),
        "feels_like_f": _round(main.get("feels_like")),
        "humidity_pct": _round(main.get("humidity")),
        "pressure_hpa": _round(main.get("pressure")),
        # units=imperial already returns mph for wind speed.
        "wind_speed_mph": _round(wind.get("speed"), 1),
        "wind_gust_mph": _round(wind.get("gust"), 1),
        "wind_deg": _round(deg, 1),
        "wind_direction": compass(deg),
        "cloud_pct": _round(clouds.get("all")),
        "precipitation_prob": None if pop is None else round(float(pop), 3),
        "conditions": (weather.get("main") or None),
        "description": (weather.get("description") or None),
    }


def _round(value: Any, digits: int = 1) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _pick_slot(payload: Dict[str, Any], target: datetime) -> Optional[Dict[str, Any]]:
    """Nearest 3-hour forecast slot to first pitch, if within 3 hours."""
    best: Optional[Dict[str, Any]] = None
    best_delta: Optional[timedelta] = None
    for slot in payload.get("list", []):
        ts = slot.get("dt")
        if ts is None:
            continue
        slot_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        delta = abs(slot_time - target)
        if best_delta is None or delta < best_delta:
            best, best_delta = slot, delta
    if best is None or best_delta is None or best_delta > MAX_SLOT_DISTANCE:
        return None
    return best


def _games_needing_weather(on: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Games joined to venue coordinates, for today forward."""
    from datetime import date as _date

    day = on or _date.today()
    with session_scope() as s:
        rows = s.execute(
            select(
                models.Game.game_id,
                models.Game.game_datetime,
                models.Game.venue,
                models.Game.venue_id,
                models.Venue.latitude,
                models.Venue.longitude,
                models.Venue.roof_type,
            )
            .outerjoin(models.Venue, models.Venue.id == models.Game.venue_id)
            .where(models.Game.game_date == day)
            .order_by(models.Game.game_datetime)
        ).all()
    return [
        {
            "game_id": r[0],
            "game_datetime": r[1],
            "venue": r[2],
            "venue_id": r[3],
            "latitude": r[4],
            "longitude": r[5],
            "roof_type": r[6],
        }
        for r in rows
    ]


async def sync_weather(on: Optional[Any] = None) -> Dict[str, Any]:
    """Fetch and store first-pitch weather for every game on ``on`` (default today)."""
    client = OpenWeatherClient()
    games = _games_needing_weather(on)
    now = datetime.now(timezone.utc)

    rows: List[Dict[str, Any]] = []
    skipped_no_coords: List[int] = []
    skipped_no_slot: List[int] = []
    errors: List[str] = []

    # One API call per distinct venue, reused by every game there.
    cache: Dict[tuple, Dict[str, Any]] = {}

    for game in games:
        lat, lon = game["latitude"], game["longitude"]
        if lat is None or lon is None:
            skipped_no_coords.append(game["game_id"])
            continue
        first_pitch = game["game_datetime"]
        if first_pitch is None:
            skipped_no_slot.append(game["game_id"])
            continue
        if first_pitch.tzinfo is None:
            first_pitch = first_pitch.replace(tzinfo=timezone.utc)

        # Games already underway (or about to start) get live conditions.
        use_current = first_pitch <= now + timedelta(minutes=30)
        key = (round(lat, 3), round(lon, 3), use_current)
        try:
            if key not in cache:
                cache[key] = (
                    await client.current(lat, lon)
                    if use_current
                    else await client.forecast(lat, lon)
                )
            payload = cache[key]
        except Exception as exc:
            errors.append(f"{game['venue']}: {type(exc).__name__}")
            logger.warning("OpenWeather fetch failed for %s: %s", game["venue"], exc)
            continue

        if use_current:
            slot = payload
            slot_time = datetime.fromtimestamp(
                int(payload.get("dt") or now.timestamp()), tz=timezone.utc
            )
        else:
            slot = _pick_slot(payload, first_pitch)
            if slot is None:
                skipped_no_slot.append(game["game_id"])
                continue
            slot_time = datetime.fromtimestamp(int(slot["dt"]), tz=timezone.utc)

        row = _slot_to_row(slot)
        row.update(
            {
                "game_id": game["game_id"],
                "venue_id": game["venue_id"],
                "venue_name": game["venue"],
                "game_datetime": first_pitch,
                "forecast_time": slot_time,
                "forecast_offset_minutes": int(
                    (slot_time - first_pitch).total_seconds() // 60
                ),
                "is_forecast": not use_current,
                "roof_status": roof_status_for(game["roof_type"]),
                "source": "openweather/current" if use_current else "openweather/forecast",
                "fetched_at": now,
            }
        )
        rows.append(row)

    stored = 0
    if rows:
        with session_scope() as s:
            stored = _upsert(s, models.GameWeather, rows, "game_id")

    result = {
        "games": len(games),
        "weather_stored": stored,
        "skipped_missing_coordinates": len(skipped_no_coords),
        "skipped_no_forecast_slot": len(skipped_no_slot),
        "errors": errors,
    }
    logger.info("Weather sync: %s", result)
    return result


def weather_for_game(game_id: int) -> Optional[models.GameWeather]:
    with session_scope() as s:
        row = s.get(models.GameWeather, game_id)
        if row is None:
            return None
        s.expunge(row)
        return row
