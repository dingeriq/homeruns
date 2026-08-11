"""Baseball Savant Statcast ingestion.

Downloads raw pitch-level Statcast data from the public Baseball Savant
`statcast_search` CSV export and stores it verbatim in Postgres.

No feature engineering, no modelling — raw landing only.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

import httpx
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

logger = logging.getLogger("dingeriq.statcast")

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_CHUNK = 500

BARREL_VALUES = {"barrel", "6"}


def _f(row: Dict[str, str], key: str) -> Optional[float]:
    raw = (row.get(key) or "").strip()
    if raw in ("", "null", "NA", "NaN"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _i(row: Dict[str, str], key: str) -> Optional[int]:
    val = _f(row, key)
    return int(val) if val is not None else None


def _s(row: Dict[str, str], key: str, limit: int = 32) -> Optional[str]:
    raw = (row.get(key) or "").strip()
    return raw[:limit] or None


class SavantClient:
    """Thin async client for Baseball Savant CSV exports."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.savant_api_base

    async def search_csv(self, start: date, end: date, season: int) -> str:
        params = {
            "all": "true",
            "hfSea": f"{season}|",
            "player_type": "pitcher",
            "game_date_gt": start.isoformat(),
            "game_date_lt": end.isoformat(),
            "min_pitches": "0",
            "min_results": "0",
            "group_by": "name",
            "sort_col": "pitches",
            "player_event_sort": "api_p_release_speed",
            "sort_order": "desc",
            "type": "details",
        }
        url = f"{self.base_url}/statcast_search/csv"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                    logger.info("Savant CSV %s -> %s", start, end)
                    resp = await client.get(
                        url,
                        params=params,
                        headers={"User-Agent": "DingerIQ/0.1 (statcast ingest)"},
                    )
                    resp.raise_for_status()
                    # Savant omits a charset in Content-Type; decode explicitly as
                    # UTF-8 so accented names (Pérez, Sánchez) survive ingestion.
                    return resp.content.decode("utf-8", errors="replace")
        raise RuntimeError("unreachable")


def parse_rows(csv_text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: List[Dict[str, Any]] = []
    for r in reader:
        game_pk = _i(r, "game_pk")
        pitcher_id = _i(r, "pitcher")
        batter_id = _i(r, "batter")
        if game_pk is None or pitcher_id is None or batter_id is None:
            continue
        at_bat = _i(r, "at_bat_number")
        pitch_no = _i(r, "pitch_number")
        if at_bat is None or pitch_no is None:
            continue

        bb_type = (r.get("launch_speed_angle") or "").strip().lower()
        launch_speed = _f(r, "launch_speed")
        launch_angle = _f(r, "launch_angle")
        barrel = bb_type in BARREL_VALUES
        hard_hit = launch_speed is not None and launch_speed >= 95.0

        game_date_raw = (r.get("game_date") or "").strip()
        try:
            game_date = date.fromisoformat(game_date_raw)
        except ValueError:
            continue

        rows.append(
            {
                "pitch_uid": f"{game_pk}-{at_bat}-{pitch_no}",
                "game_id": game_pk,
                "game_date": game_date,
                "at_bat_number": at_bat,
                "pitch_number": pitch_no,
                "pitcher_id": pitcher_id,
                "batter_id": batter_id,
                "pitch_type": _s(r, "pitch_type", 8),
                "pitch_name": _s(r, "pitch_name", 32),
                "velocity": _f(r, "release_speed"),
                "spin_rate": _f(r, "release_spin_rate"),
                "horizontal_break": _f(r, "pfx_x"),
                "vertical_break": _f(r, "pfx_z"),
                "release_pos_x": _f(r, "release_pos_x"),
                "release_pos_y": _f(r, "release_pos_y"),
                "release_pos_z": _f(r, "release_pos_z"),
                "extension": _f(r, "release_extension"),
                "plate_x": _f(r, "plate_x"),
                "plate_z": _f(r, "plate_z"),
                "exit_velocity": launch_speed,
                "launch_angle": launch_angle,
                "hit_distance": _f(r, "hit_distance_sc"),
                "spray_angle": _f(r, "hc_x"),
                "spray_angle_y": _f(r, "hc_y"),
                "barrel_flag": barrel,
                "hard_hit_flag": hard_hit,
                "estimated_ba": _f(r, "estimated_ba_using_speedangle"),
                "estimated_woba": _f(r, "estimated_woba_using_speedangle"),
                "woba_value": _f(r, "woba_value"),
                "bb_type": _s(r, "bb_type", 16),
                "events": _s(r, "events", 32),
                "description": _s(r, "description", 48),
                "stand": _s(r, "stand", 2),
                "p_throws": _s(r, "p_throws", 2),
                "home_team": _s(r, "home_team", 8),
                "away_team": _s(r, "away_team", 8),
            }
        )
    return rows


def _chunked(rows: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def store_pitches(rows: List[Dict[str, Any]]) -> int:
    total = 0
    for chunk in _chunked(rows, _CHUNK):
        with session_scope() as s:
            total += _upsert(s, models.StatcastPitch, chunk, "pitch_uid")
    return total


async def sync_statcast(
    start: date | None = None,
    end: date | None = None,
    season: int | None = None,
) -> Dict[str, Any]:
    """Ingest raw Statcast pitches for a date range (default: yesterday+today)."""
    end = end or date.today()
    start = start or (end - timedelta(days=settings.statcast_lookback_days))
    season = season or settings.mlb_season

    client = SavantClient()
    csv_text = await client.search_csv(start, end, season)
    rows = parse_rows(csv_text)
    stored = store_pitches(rows)
    logger.info("Statcast: parsed %d rows, stored %d (%s..%s)", len(rows), stored, start, end)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "season": season,
        "rows_parsed": len(rows),
        "rows_stored": stored,
    }
