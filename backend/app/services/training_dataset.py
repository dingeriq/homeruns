"""Leak-safe training corpus assembly on top of the existing feature builder.

No new feature logic lives here. This module only:

1. enumerates (batter, game) pairs that already exist in ``statcast_pitches``,
2. resolves the pitcher the batter first faced in that game (the starter),
3. calls ``feature_builder.build_features(..., include_label=True)``, which
   restricts every feature to ``game_date < target_game_date``,
4. upserts the result into ``feature_snapshots``,
5. reads snapshots back out as plain dicts for training.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import models
from app.services.feature_builder import (
    FEATURE_NAMES,
    FEATURE_SET_VERSION,
    build_features,
    upsert_snapshot,
)

logger = logging.getLogger("dingeriq.training")


def batter_game_pairs(
    session: Session,
    start: date,
    end: date,
    limit: Optional[int] = None,
) -> List[Tuple[int, int, date]]:
    """Distinct (batter_id, game_id, game_date) observed in stored Statcast."""
    P = models.StatcastPitch
    stmt = (
        select(P.batter_id, P.game_id, P.game_date)
        .where(P.game_date >= start, P.game_date <= end)
        .group_by(P.batter_id, P.game_id, P.game_date)
        .order_by(P.game_date, P.game_id, P.batter_id)
    )
    if limit:
        stmt = stmt.limit(limit)
    return [(int(b), int(g), d) for b, g, d in session.execute(stmt).all()]


def starting_pitcher_faced(
    session: Session, batter_id: int, game_id: int
) -> Optional[int]:
    """Pitcher of the batter's first plate appearance — i.e. the starter.

    Pitcher identity is known pre-game (probable starter), so using it is not a
    leak; the *stats* for that pitcher still come from before the target date.
    """
    P = models.StatcastPitch
    row = session.execute(
        select(P.pitcher_id)
        .where(P.batter_id == batter_id, P.game_id == game_id)
        .order_by(P.at_bat_number, P.pitch_number)
        .limit(1)
    ).scalars().first()
    return int(row) if row is not None else None


def build_snapshots(
    session: Session,
    start: date,
    end: date,
    *,
    limit: Optional[int] = None,
    window_days: int = 30,
) -> Dict[str, Any]:
    """Materialise labelled ``feature_snapshots`` for a date range."""
    pairs = batter_game_pairs(session, start, end, limit=limit)
    written = 0
    skipped = 0
    for batter_id, game_id, game_day in pairs:
        try:
            pitcher_id = starting_pitcher_faced(session, batter_id, game_id)
            row = build_features(
                session,
                batter_id=batter_id,
                target_game_date=game_day,
                pitcher_id=pitcher_id,
                game_id=game_id,
                window_days=window_days,
                include_label=True,
            )
            upsert_snapshot(session, row)
            written += 1
        except Exception:  # a bad pair must never abort the whole corpus
            logger.exception("snapshot failed for batter=%s game=%s", batter_id, game_id)
            skipped += 1
    session.flush()
    return {
        "pairs": len(pairs),
        "snapshots_written": written,
        "skipped": skipped,
        "start": str(start),
        "end": str(end),
        "feature_set_version": FEATURE_SET_VERSION,
        "window_days": window_days,
    }


def load_training_rows(
    session: Session,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> List[Dict[str, Any]]:
    """Labelled snapshots as plain dicts, ordered by game date (time order)."""
    S = models.FeatureSnapshot
    stmt = select(S).where(
        S.feature_set_version == feature_set_version, S.hit_hr.isnot(None)
    )
    if start:
        stmt = stmt.where(S.game_date >= start)
    if end:
        stmt = stmt.where(S.game_date <= end)
    stmt = stmt.order_by(S.game_date, S.game_id, S.batter_id)

    rows: List[Dict[str, Any]] = []
    for snap in session.execute(stmt).scalars():
        row: Dict[str, Any] = {
            "batter_id": snap.batter_id,
            "pitcher_id": snap.pitcher_id,
            "game_id": snap.game_id,
            "game_date": snap.game_date,
            "hit_hr": snap.hit_hr,
        }
        for name in FEATURE_NAMES:
            row[name] = getattr(snap, name, None)
        rows.append(row)
    return rows


def corpus_summary(
    session: Session, feature_set_version: str = FEATURE_SET_VERSION
) -> Dict[str, Any]:
    S = models.FeatureSnapshot
    total, min_d, max_d, positives = session.execute(
        select(
            func.count(S.id),
            func.min(S.game_date),
            func.max(S.game_date),
            func.sum(func.coalesce(S.hit_hr, 0)),
        ).where(S.feature_set_version == feature_set_version)
    ).one()
    total = int(total or 0)
    positives = int(positives or 0)
    return {
        "feature_set_version": feature_set_version,
        "rows": total,
        "positives": positives,
        "base_rate": round(positives / total, 6) if total else None,
        "first_game_date": str(min_d) if min_d else None,
        "last_game_date": str(max_d) if max_d else None,
    }


__all__ = [
    "batter_game_pairs",
    "build_snapshots",
    "corpus_summary",
    "load_training_rows",
    "starting_pitcher_faced",
]
