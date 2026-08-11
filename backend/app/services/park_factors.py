"""Stadium HR park factors, computed from stored Statcast batted balls.

Method (no invented constants):

* A batted ball event (BBE) is any stored Statcast row with a ``bb_type``.
* ``hr_rate = home runs / BBE`` for a venue+season, computed overall and split
  by batter handedness (``stand`` = 'L' / 'R').
* ``hr_factor = venue hr_rate / league hr_rate`` over the same season and the
  same handedness slice. 1.00 means neutral.
* Factors are only stored when the sample clears the minimum thresholds below;
  otherwise the factor stays ``None`` and the row records why.

Everything is derived from data we actually ingested — nothing is seeded from
third-party tables.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import models
from app.database.session import session_scope

logger = logging.getLogger("dingeriq.park_factors")

MIN_BBE_OVERALL = 300
MIN_BBE_HANDED = 150
SOURCE = "dingeriq:statcast_bbe"


def _hand_key(stand: Optional[str]) -> str:
    return stand if stand in ("L", "R") else "U"


def _collect(session: Session) -> Dict[tuple, Dict[str, int]]:
    """Aggregate BBE / HR counts keyed by (season, venue_id, venue, hand)."""
    P = models.StatcastPitch
    G = models.Game
    season = func.extract("year", P.game_date)
    rows = session.execute(
        select(
            season.label("season"),
            G.venue_id,
            G.venue,
            P.stand,
            func.count(P.pitch_uid),
            func.sum(func.cast(P.events == "home_run", models.Integer)),
        )
        .join(G, G.game_id == P.game_id)
        .where(P.bb_type.is_not(None))
        .group_by(season, G.venue_id, G.venue, P.stand)
    ).all()

    out: Dict[tuple, Dict[str, int]] = {}
    for season_val, venue_id, venue, stand, bbe, hrs in rows:
        yr = int(season_val)
        hand = _hand_key(stand)
        for h in {hand, "ALL"}:
            key = (yr, venue_id, venue, h)
            bucket = out.setdefault(key, {"batted_balls": 0, "home_runs": 0})
            bucket["batted_balls"] += int(bbe or 0)
            bucket["home_runs"] += int(hrs or 0)
    return out


def compute_park_factors() -> dict:
    """Recompute and persist every venue/season/handedness park factor."""
    stored = 0
    skipped = 0
    seasons: set = set()
    with session_scope() as s:
        buckets = _collect(s)
        if not buckets:
            return {
                "status": "unavailable",
                "reason": "no Statcast batted-ball rows joined to games with a venue",
                "park_factors_stored": 0,
            }

        # League baselines per (season, hand)
        league: Dict[tuple, Dict[str, int]] = {}
        for (yr, _vid, _v, hand), counts in buckets.items():
            base = league.setdefault((yr, hand), {"batted_balls": 0, "home_runs": 0})
            base["batted_balls"] += counts["batted_balls"]
            base["home_runs"] += counts["home_runs"]

        now = datetime.now(timezone.utc)
        s.execute(delete(models.ParkFactor).where(models.ParkFactor.source == SOURCE))
        for (yr, venue_id, venue, hand), counts in sorted(
            buckets.items(), key=lambda kv: (kv[0][0], kv[0][2] or "", kv[0][3])
        ):
            seasons.add(yr)
            bbe = counts["batted_balls"]
            hrs = counts["home_runs"]
            hr_rate = round(hrs / bbe, 5) if bbe else None
            base = league.get((yr, hand), {})
            league_bbe = base.get("batted_balls", 0)
            league_hr = base.get("home_runs", 0)
            league_rate = round(league_hr / league_bbe, 5) if league_bbe else None

            minimum = MIN_BBE_OVERALL if hand == "ALL" else MIN_BBE_HANDED
            factor: Optional[float] = None
            note: Optional[str] = None
            if bbe < minimum:
                skipped += 1
                note = f"insufficient sample: {bbe} BBE < {minimum} required"
            elif not league_rate:
                note = "no league baseline for this season/handedness"
            else:
                factor = round((hr_rate or 0.0) / league_rate, 3)

            s.add(
                models.ParkFactor(
                    venue_id=venue_id,
                    venue_name=venue,
                    season=yr,
                    batter_hand=hand,
                    batted_balls=bbe,
                    home_runs=hrs,
                    hr_rate=hr_rate,
                    league_hr_rate=league_rate,
                    hr_factor=factor,
                    sample_note=note,
                    source=SOURCE,
                    computed_at=now,
                )
            )
            stored += 1

    logger.info("Park factors computed: %s rows (%s below sample threshold)", stored, skipped)
    return {
        "status": "ok",
        "park_factors_stored": stored,
        "below_sample_threshold": skipped,
        "seasons": sorted(seasons),
        "min_batted_balls": {"overall": MIN_BBE_OVERALL, "by_hand": MIN_BBE_HANDED},
        "source": SOURCE,
    }


def _row_to_dict(row: models.ParkFactor) -> dict:
    return {
        "venue_id": row.venue_id,
        "venue_name": row.venue_name,
        "season": row.season,
        "batter_hand": row.batter_hand,
        "batted_balls": row.batted_balls,
        "home_runs": row.home_runs,
        "hr_rate": row.hr_rate,
        "league_hr_rate": row.league_hr_rate,
        "hr_factor": row.hr_factor,
        "sample_note": row.sample_note,
        "source": row.source,
        "computed_at": row.computed_at,
    }


def list_park_factors(season: Optional[int] = None, venue_id: Optional[int] = None) -> List[dict]:
    with session_scope() as s:
        stmt = select(models.ParkFactor)
        if season is not None:
            stmt = stmt.where(models.ParkFactor.season == season)
        if venue_id is not None:
            stmt = stmt.where(models.ParkFactor.venue_id == venue_id)
        rows = s.execute(
            stmt.order_by(
                models.ParkFactor.season.desc(),
                models.ParkFactor.venue_name,
                models.ParkFactor.batter_hand,
            )
        ).scalars().all()
        return [_row_to_dict(r) for r in rows]


def park_factors_for_venue(
    session: Session,
    venue_id: Optional[int] = None,
    venue_name: Optional[str] = None,
    season: Optional[int] = None,
) -> Optional[dict]:
    """Latest stored factors for one venue, keyed ALL / L / R."""
    if venue_id is None and not venue_name:
        return None
    stmt = select(models.ParkFactor)
    if venue_id is not None:
        stmt = stmt.where(models.ParkFactor.venue_id == venue_id)
    else:
        stmt = stmt.where(models.ParkFactor.venue_name == venue_name)
    if season is not None:
        stmt = stmt.where(models.ParkFactor.season == season)
    rows = session.execute(
        stmt.order_by(models.ParkFactor.season.desc())
    ).scalars().all()
    if not rows:
        return None
    latest_season = rows[0].season
    by_hand = {r.batter_hand: r for r in rows if r.season == latest_season}
    overall = by_hand.get("ALL")
    return {
        "venue": (overall or rows[0]).venue_name,
        "venue_id": (overall or rows[0]).venue_id,
        "season": latest_season,
        "hr_factor": overall.hr_factor if overall else None,
        "hr_factor_lhb": by_hand["L"].hr_factor if "L" in by_hand else None,
        "hr_factor_rhb": by_hand["R"].hr_factor if "R" in by_hand else None,
        "batted_balls": overall.batted_balls if overall else None,
        "home_runs": overall.home_runs if overall else None,
        "hr_rate": overall.hr_rate if overall else None,
        "league_hr_rate": overall.league_hr_rate if overall else None,
        "sample_note": overall.sample_note if overall else None,
        "source": (overall or rows[0]).source,
        "computed_at": (overall or rows[0]).computed_at,
    }


__all__ = [
    "compute_park_factors",
    "list_park_factors",
    "park_factors_for_venue",
    "MIN_BBE_OVERALL",
    "MIN_BBE_HANDED",
    "SOURCE",
]
