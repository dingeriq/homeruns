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
# Baseball Savant hard-caps a CSV export at 25,000 rows and truncates silently.
# A full MLB day is ~4,500 pitches, so 2-day windows stay well under the cap.
_WINDOW_DAYS = 2
_SAVANT_ROW_CAP = 25000



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
                    # Savant omits a charset in Content-Type and prefixes the
                    # export with a UTF-8 BOM; decode as utf-8-sig so accented
                    # names survive AND the first header ("pitch_type") is not
                    # mangled into "\ufeffpitch_type".
                    return resp.content.decode("utf-8-sig", errors="replace")
        raise RuntimeError("unreachable")


def parse_rows(csv_text: str) -> List[Dict[str, Any]]:
    # Defensive: a BOM here would rename the first column and silently null it.
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
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


def store_pitches_with_session(session, rows: List[Dict[str, Any]]) -> int:
    """Upsert rows in 500-row batches on an existing session.

    One commit per batch keeps transactions short (no long-lived write lock)
    while reusing a single pooled connection for the whole sync. Every batch is
    an idempotent ON CONFLICT (pitch_uid) upsert, so a retry never duplicates.
    """
    total = 0
    for chunk in _chunked(rows, _CHUNK):
        try:
            total += _upsert(session, models.StatcastPitch, chunk, "pitch_uid")
            session.commit()
        except Exception:
            session.rollback()
            raise
    return total


def store_pitches(rows: List[Dict[str, Any]]) -> int:
    """Standalone entry point (own session) — kept for callers/tests."""
    with session_scope() as s:
        return store_pitches_with_session(s, rows)


async def sync_statcast(
    start: date | None = None,
    end: date | None = None,
    season: int | None = None,
    window_days: int | None = None,
    pause_seconds: float | None = None,
) -> Dict[str, Any]:
    """Ingest raw Statcast pitches for a date range (default: yesterday+today).

    The range is fetched in small date windows and each window is written to
    Postgres before the next one is requested, so a long backfill is chunked,
    resumable (re-running any range is an idempotent upsert on ``pitch_uid``)
    and never holds a season-sized CSV in memory. A failing window is recorded
    and skipped — no rows are fabricated or imputed.

    A single SQLAlchemy session (one pooled connection) is reused for the whole
    multi-window run, with a commit after every 500-row batch, and a short
    configurable pause between windows to limit request/database pressure.
    """
    end = end or date.today()
    start = start or (end - timedelta(days=settings.statcast_lookback_days))
    season = season or settings.mlb_season
    step = max(1, window_days or _WINDOW_DAYS)
    pause = settings.statcast_window_pause_seconds if pause_seconds is None else pause_seconds
    pause = max(0.0, float(pause))

    client = SavantClient()
    total_parsed = 0
    total_stored = 0
    windows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    truncated: List[Dict[str, str]] = []
    pending: List[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        w_end = min(cursor + timedelta(days=step - 1), end)
        pending.append((cursor, w_end))
        cursor = w_end + timedelta(days=1)

    session = SessionLocal()
    try:
        first_window = True
        while pending:
            w_start, w_end = pending.pop(0)
            if not first_window and pause:
                await asyncio.sleep(pause)
            first_window = False
            try:
                csv_text = await client.search_csv(w_start, w_end, season)
                rows = parse_rows(csv_text)
            except Exception as exc:  # network, Savant 5xx, parse failure
                logger.exception("Statcast window %s..%s failed", w_start, w_end)
                errors.append(
                    {
                        "start": w_start.isoformat(),
                        "end": w_end.isoformat(),
                        "error": type(exc).__name__,
                    }
                )
                continue

            # Savant silently truncates at the export cap — split and refetch
            # rather than storing a partial window.
            if len(rows) >= _SAVANT_ROW_CAP and w_start < w_end:
                mid = w_start + timedelta(days=(w_end - w_start).days // 2)
                logger.warning(
                    "Statcast window %s..%s hit the %d-row export cap; splitting",
                    w_start,
                    w_end,
                    _SAVANT_ROW_CAP,
                )
                pending.insert(0, (mid + timedelta(days=1), w_end))
                pending.insert(0, (w_start, mid))
                continue
            if len(rows) >= _SAVANT_ROW_CAP:
                truncated.append({"start": w_start.isoformat(), "end": w_end.isoformat()})

            try:
                stored = await asyncio.to_thread(store_pitches_with_session, session, rows)
            except Exception as exc:  # database error — window stays retryable
                logger.exception("Statcast window %s..%s failed to store", w_start, w_end)
                errors.append(
                    {
                        "start": w_start.isoformat(),
                        "end": w_end.isoformat(),
                        "error": type(exc).__name__,
                    }
                )
                continue

            total_parsed += len(rows)
            total_stored += stored
            windows.append(
                {
                    "start": w_start.isoformat(),
                    "end": w_end.isoformat(),
                    "rows_parsed": len(rows),
                    "rows_stored": stored,
                }
            )
            logger.info(
                "Statcast window %s..%s: parsed %d, stored %d", w_start, w_end, len(rows), stored
            )
    finally:
        session.close()

    logger.info(
        "Statcast: parsed %d rows, stored %d (%s..%s, %d windows, %d errors)",
        total_parsed,
        total_stored,
        start,
        end,
        len(windows),
        len(errors),
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "season": season,
        "window_days": step,
        "pause_seconds": pause,
        "windows": len(windows),
        "rows_parsed": total_parsed,
        "rows_stored": total_stored,
        "errors": errors,
        "truncated_windows": truncated,
        "window_detail": windows,
    }


