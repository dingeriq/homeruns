"""Sync MLB Stats API data into the local Postgres cache."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import models
from app.database.session import session_scope
from app.services.mlb_client import MLBStatsClient

logger = logging.getLogger("dingeriq.sync")


def _upsert(session, table, rows: Iterable[Dict[str, Any]], pk: str) -> int:
    rows = list(rows)
    if not rows:
        return 0
    stmt = pg_insert(table).values(rows)
    update_cols = {c.name: stmt.excluded[c.name] for c in table.__table__.columns if c.name != pk}
    stmt = stmt.on_conflict_do_update(index_elements=[pk], set_=update_cols)
    session.execute(stmt)
    return len(rows)


async def sync_teams(client: MLBStatsClient) -> int:
    data = await client.teams()
    rows: List[Dict[str, Any]] = []
    for t in data.get("teams", []):
        if not t.get("active", True):
            continue
        rows.append(
            {
                "id": t["id"],
                "abbreviation": t.get("abbreviation") or t.get("teamCode", ""),
                "name": t.get("name", ""),
                "league": (t.get("league") or {}).get("name", "")[:8] or "",
                "division": (t.get("division") or {}).get("name", ""),
                "venue": (t.get("venue") or {}).get("name"),
            }
        )
    with session_scope() as s:
        n = _upsert(s, models.Team, rows, "id")
    logger.info("Synced %d teams", n)
    return n


async def sync_games(client: MLBStatsClient, on: date | None = None) -> int:
    on = on or date.today()
    data = await client.schedule(on)
    rows: List[Dict[str, Any]] = []
    for day in data.get("dates", []):
        # Official MLB schedule (local) date for this slate — night games have a
        # UTC first-pitch timestamp that falls on the following calendar day, so
        # deriving game_date from the timestamp splits one slate across two dates.
        try:
            slate_date = date.fromisoformat(day.get("date", ""))
        except Exception:
            slate_date = None
        for g in day.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            home_team = home.get("team", {})
            away_team = away.get("team", {})
            venue = (g.get("venue") or {}).get("name", "")
            game_dt_raw = g.get("gameDate")
            try:
                game_dt = datetime.fromisoformat(game_dt_raw.replace("Z", "+00:00"))
            except Exception:
                game_dt = datetime.now(timezone.utc)
            rows.append(
                {
                    "game_id": g["gamePk"],
                    "game_date": slate_date or game_dt.date(),
                    "game_datetime": game_dt,
                    "home_team": home_team.get("abbreviation")
                    or home_team.get("teamCode")
                    or home_team.get("name", "")[:8],
                    "away_team": away_team.get("abbreviation")
                    or away_team.get("teamCode")
                    or away_team.get("name", "")[:8],
                    "home_team_id": home_team.get("id"),
                    "away_team_id": away_team.get("id"),
                    "venue": venue,
                    "venue_id": (g.get("venue") or {}).get("id"),
                    "status": (g.get("status") or {}).get("detailedState", "Scheduled"),
                    "home_probable_pitcher": (home.get("probablePitcher") or {}).get("fullName"),
                    "away_probable_pitcher": (away.get("probablePitcher") or {}).get("fullName"),
                    "home_probable_pitcher_id": (home.get("probablePitcher") or {}).get("id"),
                    "away_probable_pitcher_id": (away.get("probablePitcher") or {}).get("id"),
                }
            )
    with session_scope() as s:
        n = _upsert(s, models.Game, rows, "game_id")
    logger.info("Synced %d games for %s", n, on.isoformat())
    return n


async def sync_venues(client: MLBStatsClient) -> int:
    """Cache MLB venues with the coordinates weather lookups need."""
    data = await client.venues()
    rows: List[Dict[str, Any]] = []
    for v in data.get("venues", []):
        location = v.get("location") or {}
        coords = location.get("defaultCoordinates") or {}
        rows.append(
            {
                "id": v["id"],
                "name": v.get("name", ""),
                "city": location.get("city"),
                "state": location.get("stateAbbrev") or location.get("state"),
                "country": location.get("country"),
                "latitude": coords.get("latitude"),
                "longitude": coords.get("longitude"),
                "timezone": ((v.get("timeZone") or {}).get("id")),
                "roof_type": ((v.get("fieldInfo") or {}).get("roofType")),
                "azimuth_angle": location.get("azimuthAngle"),
                "elevation_ft": location.get("elevation"),
            }
        )
    with session_scope() as s:
        n = _upsert(s, models.Venue, rows, "id")
    logger.info("Synced %d venues", n)
    return n


async def sync_players(client: MLBStatsClient) -> int:
    with session_scope() as s:
        team_rows = s.query(models.Team.id, models.Team.abbreviation).all()

    total = 0
    all_rows: List[Dict[str, Any]] = []
    seen: set[int] = set()
    # Rate-limit ourselves by iterating sequentially with small pauses.
    for team_id, abbr in team_rows:
        try:
            data = await client.roster(team_id)
        except Exception as exc:
            logger.warning("Roster fetch failed for team %s: %s", team_id, exc)
            continue
        for entry in data.get("roster", []):
            person = entry.get("person", {})
            pos = (entry.get("position") or {}).get("abbreviation")
            pid = person.get("id")
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            all_rows.append(
                {
                    "id": pid,
                    "full_name": person.get("fullName", ""),
                    "team_id": team_id,
                    "team_abbreviation": abbr,
                    "position": pos,
                    "bats": ((person.get("batSide") or {}).get("code") or None),
                    "throws": ((person.get("pitchHand") or {}).get("code") or None),
                }
            )
        await asyncio.sleep(0.15)

    with session_scope() as s:
        total = _upsert(s, models.Player, all_rows, "id")
    logger.info("Synced %d players", total)
    return total


async def sync_probable_pitchers(client: MLBStatsClient) -> int:
    """Ensure every probable starter has a row in ``players`` (identity backfill).

    Probable starters are frequently not on the 26-man active roster snapshot
    (e.g. just recalled), which would otherwise leave the game with a pitcher
    name that resolves to no MLB id.
    """
    with session_scope() as s:
        pairs = s.execute(
            select(
                models.Game.home_probable_pitcher_id,
                models.Game.away_probable_pitcher_id,
            ).where(models.Game.game_date >= date.today())
        ).all()
        known = {pid for (pid,) in s.execute(select(models.Player.id)).all()}

    missing = {
        pid
        for row in pairs
        for pid in row
        if pid is not None and pid not in known
    }
    rows: List[Dict[str, Any]] = []
    for pid in sorted(missing):
        try:
            data = await client.person(pid)
        except Exception as exc:
            logger.warning("Person fetch failed for %s: %s", pid, exc)
            continue
        people = data.get("people") or []
        if not people:
            continue
        person = people[0]
        team = person.get("currentTeam") or {}
        rows.append(
            {
                "id": person["id"],
                "full_name": person.get("fullName", ""),
                "team_id": team.get("id"),
                "team_abbreviation": (team.get("abbreviation") or None),
                "position": ((person.get("primaryPosition") or {}).get("abbreviation") or None),
                "bats": ((person.get("batSide") or {}).get("code") or None),
                "throws": ((person.get("pitchHand") or {}).get("code") or None),
            }
        )
        await asyncio.sleep(0.1)

    if not rows:
        return 0
    with session_scope() as s:
        n = _upsert(s, models.Player, rows, "id")
    logger.info("Backfilled %d probable-pitcher identities", n)
    return n


async def run_full_sync() -> Dict[str, int]:
    client = MLBStatsClient()
    counts: Dict[str, int] = {}
    counts["teams"] = await sync_teams(client)
    counts["games"] = await sync_games(client)
    try:
        counts["venues"] = await sync_venues(client)
    except Exception as exc:
        logger.exception("Venue sync failed: %s", exc)
        counts["venues"] = 0
    try:
        counts["players"] = await sync_players(client)
    except Exception as exc:
        logger.exception("Player sync failed: %s", exc)
        counts["players"] = 0
    try:
        counts["probable_pitchers"] = await sync_probable_pitchers(client)
    except Exception as exc:
        logger.exception("Probable pitcher backfill failed: %s", exc)
        counts["probable_pitchers"] = 0
    try:
        from app.services.lineups import sync_lineups

        counts["lineups"] = (await sync_lineups()).get("lineup_slots_stored", 0)
    except Exception as exc:
        logger.exception("Lineup sync failed: %s", exc)
        counts["lineups"] = 0
    try:
        from app.services.weather_service import OpenWeatherNotConfigured, sync_weather

        counts["weather"] = (await sync_weather()).get("weather_stored", 0)
    except OpenWeatherNotConfigured:
        logger.info("Weather sync skipped: OPENWEATHER_API_KEY not set")
        counts["weather"] = 0
    except Exception as exc:
        logger.exception("Weather sync failed: %s", exc)
        counts["weather"] = 0
    return counts
