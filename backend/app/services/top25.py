"""Permanent daily Top 25 snapshot (lock).

Rule
----
LOCK TIME = 90 minutes before the earliest scheduled first pitch of the slate.
The slate date is the America/New_York baseball day (``timeutils``).

At/after the lock (never before), exactly once per slate:

* eligible = stored ``daily_predictions`` for the slate whose
  - ``lineup_status == 'confirmed'``,
  - game has a complete confirmed nine-man card for BOTH sides,
  - row existed at the lock instant (``created_at <= lock``) — so a game whose
    lineups were confirmed after the cutoff can never enter,
  - game first pitch had not passed at the lock instant;
* sorted by the stored ``hr_probability`` desc (player_id tiebreak);
* the first 25 (or fewer, never padded) are written to
  ``daily_top25_snapshots``.

Once written the snapshot is immutable: later scoring runs, lineup
confirmations or re-invocations never change ranks, probabilities or
membership. Only ``player_status`` may be updated (scratch / DNP marking).
No model, scoring, lineup-confirmation or first-pitch logic is changed here.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from app.database import models
from app.services.lineups import CONFIRMED, LINEUP_SLOTS, slate_game_ids
from app.services.timeutils import ensure_utc, slate_today, utcnow

logger = logging.getLogger("dingeriq.top25")

TOP_N = 25
LOCK_OFFSET = timedelta(minutes=90)
PLAYER_STATUSES = {"active", "scratched", "dnp"}
_MUTABLE_FIELDS = {"player_status", "status_updated_at"}


class SnapshotImmutableError(RuntimeError):
    """Raised when code attempts to change a locked Top 25 field."""


@event.listens_for(models.DailyTop25Snapshot, "before_update")
def _block_locked_field_updates(_mapper, _conn, target) -> None:
    state = inspect(target)
    for attr in state.attrs:
        if attr.key in _MUTABLE_FIELDS:
            continue
        if attr.history.has_changes():
            raise SnapshotImmutableError(
                f"daily_top25_snapshots.{attr.key} is locked and cannot be changed"
            )


@event.listens_for(models.DailyTop25Snapshot, "before_delete")
def _block_delete(_mapper, _conn, target) -> None:
    raise SnapshotImmutableError("locked Top 25 snapshot rows cannot be deleted")


# ---------------------------------------------------------------------------
# Lock time
# ---------------------------------------------------------------------------

def earliest_first_pitch(session: Session, day: date) -> Optional[datetime]:
    ids = slate_game_ids(session, day)
    if not ids:
        return None
    value = session.execute(
        select(func.min(models.Game.game_datetime)).where(models.Game.game_id.in_(ids))
    ).scalar()
    return ensure_utc(value)


def lock_time_for(session: Session, day: date) -> Optional[datetime]:
    first = earliest_first_pitch(session, day)
    return None if first is None else first - LOCK_OFFSET


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def _full_confirmed_games(session: Session, day: date) -> set[int]:
    """Games with exactly nine confirmed starters stored on BOTH sides."""
    L = models.GameLineup
    rows = session.execute(
        select(L.game_id, L.side, func.count(func.distinct(L.player_id)))
        .where(L.game_date == day, L.status == CONFIRMED, L.is_starter.is_(True))
        .group_by(L.game_id, L.side)
    ).all()
    sides: Dict[int, set[str]] = {}
    for gid, side, n in rows:
        if int(n) == LINEUP_SLOTS:
            sides.setdefault(int(gid), set()).add(side)
    return {gid for gid, s in sides.items() if {"home", "away"} <= s}


def _eligible(session: Session, day: date, lock_ts: datetime) -> List[models.DailyPrediction]:
    D = models.DailyPrediction
    full_games = _full_confirmed_games(session, day)
    if not full_games:
        return []
    rows = session.execute(
        select(D).where(
            D.game_date == day,
            D.lineup_status == CONFIRMED,
            D.game_id.in_(full_games),
        )
    ).scalars().all()
    out = []
    first_pitch: Dict[int, Optional[datetime]] = {}
    for r in rows:
        created = ensure_utc(r.created_at)
        if created is None or created > lock_ts:
            continue  # did not exist at the cutoff
        if r.game_id not in first_pitch:
            game = session.get(models.Game, r.game_id)
            first_pitch[r.game_id] = ensure_utc(
                r.first_pitch_utc or getattr(game, "game_datetime", None)
            )
        fp = first_pitch[r.game_id]
        if fp is not None and fp <= lock_ts:
            continue  # first pitch already passed at the cutoff
        out.append(r)
    out.sort(key=lambda r: (-r.hr_probability, r.player_id, r.game_id))
    return out


# ---------------------------------------------------------------------------
# Create / read
# ---------------------------------------------------------------------------

def snapshot_rows(session: Session, day: date) -> List[models.DailyTop25Snapshot]:
    S = models.DailyTop25Snapshot
    return list(
        session.execute(select(S).where(S.slate_date == day).order_by(S.rank)).scalars()
    )


def create_top25_snapshot(
    session: Session, day: Optional[date] = None, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Create the day's Top 25 snapshot if the cutoff has passed (idempotent)."""
    day = day or slate_today()
    now = ensure_utc(now) or utcnow()
    existing = snapshot_rows(session, day)
    lock_ts = lock_time_for(session, day)
    if existing:
        return {"status": "already_locked", "slate_date": str(day), "entries": len(existing),
                "lock_time_utc": ensure_utc(existing[0].lock_time_utc).isoformat()}
    if lock_ts is None:
        return {"status": "no_slate", "slate_date": str(day), "entries": 0, "lock_time_utc": None}
    if now < lock_ts:
        return {"status": "before_cutoff", "slate_date": str(day), "entries": 0,
                "lock_time_utc": lock_ts.isoformat()}

    chosen = _eligible(session, day, lock_ts)[:TOP_N]
    for rank, r in enumerate(chosen, start=1):
        session.add(
            models.DailyTop25Snapshot(
                slate_date=day,
                rank=rank,
                player_id=r.player_id,
                player_name=r.player_name,
                team_abbreviation=r.team_abbreviation,
                game_id=r.game_id,
                hr_probability=r.hr_probability,
                confidence=r.confidence,
                model_version=r.model_version,
                lineup_status=r.lineup_status or CONFIRMED,
                lineup_slot=r.lineup_slot,
                first_pitch_utc=ensure_utc(r.first_pitch_utc),
                lock_time_utc=lock_ts,
                created_at=now,
                player_status="active",
            )
        )
    session.flush()
    logger.info("Top 25 locked for %s at %s: %d players", day, lock_ts.isoformat(), len(chosen))
    return {"status": "locked", "slate_date": str(day), "entries": len(chosen),
            "lock_time_utc": lock_ts.isoformat()}


def mark_player_status(
    session: Session, day: date, player_id: int, status: str, *, game_id: Optional[int] = None
) -> int:
    """Mark a locked player scratched/DNP. Never removes or replaces anyone."""
    if status not in PLAYER_STATUSES:
        raise ValueError(f"invalid player_status {status!r}")
    S = models.DailyTop25Snapshot
    q = select(S).where(S.slate_date == day, S.player_id == player_id)
    if game_id is not None:
        q = q.where(S.game_id == game_id)
    rows = session.execute(q).scalars().all()
    for r in rows:
        r.player_status = status
        r.status_updated_at = utcnow()
    session.flush()
    return len(rows)


def _serialise(r: models.DailyTop25Snapshot) -> Dict[str, Any]:
    return {
        "slate_date": r.slate_date.isoformat(),
        "rank": r.rank,
        "player_id": r.player_id,
        "player_name": r.player_name or str(r.player_id),
        "team": r.team_abbreviation,
        "game_id": r.game_id,
        "hr_probability": r.hr_probability,
        "confidence": r.confidence,
        "model_version": r.model_version,
        "lineup_status": r.lineup_status,
        "lineup_slot": r.lineup_slot,
        "first_pitch_utc": ensure_utc(r.first_pitch_utc).isoformat() if r.first_pitch_utc else None,
        "lock_time_utc": ensure_utc(r.lock_time_utc).isoformat(),
        "player_status": r.player_status,
    }


def get_top25(session: Session, day: Optional[date] = None) -> Dict[str, Any]:
    """Locked snapshot for ``day`` (any historical date), or a not-locked marker."""
    day = day or slate_today()
    rows = snapshot_rows(session, day)
    if rows:
        return {"status": "locked", "slate_date": str(day),
                "lock_time_utc": ensure_utc(rows[0].lock_time_utc).isoformat(),
                "entries": [_serialise(r) for r in rows]}
    lock_ts = lock_time_for(session, day)
    return {"status": "not_locked", "slate_date": str(day),
            "lock_time_utc": lock_ts.isoformat() if lock_ts else None, "entries": []}


def top10(session: Session, day: Optional[date] = None) -> List[Dict[str, Any]]:
    return [e for e in get_top25(session, day)["entries"] if e["rank"] <= 10]


__all__ = [
    "LOCK_OFFSET", "TOP_N", "SnapshotImmutableError", "create_top25_snapshot",
    "get_top25", "lock_time_for", "mark_player_status", "top10",
]
