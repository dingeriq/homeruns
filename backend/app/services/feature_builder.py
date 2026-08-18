"""Leak-safe V1 feature builder for the DingerIQ HR model.

The single hard rule enforced here:

    FEATURES use ``game_date < target_game_date``
    LABEL    uses ``game_date == target_game_date``

The trailing historical window is anchored on ``target_game_date`` itself —
never on ``MAX(game_date)`` — and every Statcast query carries the upper bound
``game_date <= target_game_date - 1 day``. Callers cannot opt out: the bounds
are applied inside this module, not by the caller.

No value is fabricated: when a sample size is zero the rate is ``None`` and the
denominator is reported alongside it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import models
from app.services.lineups import lineup_slot_for_player
from app.services.park_factors import park_factors_for_venue

FEATURE_SET_VERSION = "v1"
DEFAULT_WINDOW_DAYS = 30

#: Feature names, in the order the training pipeline will consume them.
FEATURE_NAMES = (
    "batter_barrel_rate_30d",
    "batter_hard_hit_rate_30d",
    "batter_max_exit_velocity_30d",
    "batter_fly_ball_rate_30d",
    "batter_hr_per_pa_30d",
    "pitcher_barrel_rate_allowed_30d",
    "pitcher_hard_hit_rate_allowed_30d",
    "pitcher_fly_ball_rate_allowed_30d",
    "pitcher_hr_per_pa_allowed_30d",
    "platoon_advantage",
    "park_hr_factor",
    "park_hr_factor_handedness",
    "weather_temperature_f",
    "weather_wind_out_component",
    "expected_plate_appearances",
    "lineup_slot",
)

#: Sample-size / denominator fields that accompany the rate features.
SAMPLE_FIELDS = (
    "batter_pitches_30d",
    "batter_batted_balls_30d",
    "batter_pa_30d",
    "batter_home_runs_30d",
    "pitcher_pitches_30d",
    "pitcher_batted_balls_30d",
    "pitcher_pa_30d",
    "pitcher_home_runs_allowed_30d",
)


def historical_bounds(
    target_game_date: date, window_days: int = DEFAULT_WINDOW_DAYS
) -> tuple[date, date]:
    """Inclusive [start, end] of the leak-safe trailing window.

    ``end`` is always ``target_game_date - 1 day``, so the target game can never
    contribute to its own features.
    """
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    end = target_game_date - timedelta(days=1)
    start = target_game_date - timedelta(days=window_days)
    return start, end


def _rate(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if not denominator:
        return None
    return round((numerator or 0) / denominator, 6)


def _round(value: Any, digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def platoon_advantage(bats: Optional[str], throws: Optional[str]) -> Optional[float]:
    """+1 when the hitter holds the platoon edge, 0 when the pitcher does.

    Switch hitters ('S') always take the opposite side, so they hold the edge.
    Returns ``None`` when either hand is unknown — never a guess.
    """
    b = (bats or "").strip().upper()[:1]
    t = (throws or "").strip().upper()[:1]
    if b not in {"L", "R", "S"} or t not in {"L", "R"}:
        return None
    if b == "S":
        return 1.0
    return 1.0 if b != t else 0.0


def _batter_window_stats(
    session: Session, batter_id: int, start: date, end: date
) -> Dict[str, Any]:
    P = models.StatcastPitch
    bounds = (P.batter_id == batter_id, P.game_date >= start, P.game_date <= end)
    row = session.execute(
        select(
            func.count(P.pitch_uid),
            func.count(P.exit_velocity),
            func.max(P.exit_velocity),
            func.sum(func.cast(P.barrel_flag, models.Integer)),
            func.sum(func.cast(P.hard_hit_flag, models.Integer)),
        ).where(*bounds)
    ).one()
    pitches, bbe, max_ev, barrels, hard_hit = row
    fly_balls = session.execute(
        select(func.count(P.pitch_uid)).where(*bounds, P.bb_type == "fly_ball")
    ).scalar()
    home_runs = session.execute(
        select(func.count(P.pitch_uid)).where(*bounds, P.events == "home_run")
    ).scalar()
    plate_appearances = session.execute(
        select(func.count(func.distinct(P.game_id * 1000 + P.at_bat_number))).where(
            *bounds, P.events.isnot(None)
        )
    ).scalar()
    return {
        "pitches": int(pitches or 0),
        "batted_balls": int(bbe or 0),
        "plate_appearances": int(plate_appearances or 0),
        "home_runs": int(home_runs or 0),
        "max_exit_velocity": _round(max_ev, 1),
        "barrels": int(barrels or 0),
        "hard_hit": int(hard_hit or 0),
        "fly_balls": int(fly_balls or 0),
    }


def _pitcher_window_stats(
    session: Session, pitcher_id: int, start: date, end: date
) -> Dict[str, Any]:
    P = models.StatcastPitch
    bounds = (P.pitcher_id == pitcher_id, P.game_date >= start, P.game_date <= end)
    row = session.execute(
        select(
            func.count(P.pitch_uid),
            func.count(P.exit_velocity),
            func.sum(func.cast(P.barrel_flag, models.Integer)),
            func.sum(func.cast(P.hard_hit_flag, models.Integer)),
        ).where(*bounds)
    ).one()
    pitches, bbe, barrels, hard_hit = row
    fly_balls = session.execute(
        select(func.count(P.pitch_uid)).where(*bounds, P.bb_type == "fly_ball")
    ).scalar()
    home_runs = session.execute(
        select(func.count(P.pitch_uid)).where(*bounds, P.events == "home_run")
    ).scalar()
    plate_appearances = session.execute(
        select(func.count(func.distinct(P.game_id * 1000 + P.at_bat_number))).where(
            *bounds, P.events.isnot(None)
        )
    ).scalar()
    return {
        "pitches": int(pitches or 0),
        "batted_balls": int(bbe or 0),
        "plate_appearances": int(plate_appearances or 0),
        "home_runs": int(home_runs or 0),
        "barrels": int(barrels or 0),
        "hard_hit": int(hard_hit or 0),
        "fly_balls": int(fly_balls or 0),
    }


def label_for_target_game(session: Session, batter_id: int, game_id: int) -> int:
    """1 when the batter homered in THIS game. Label data only, never a feature."""
    P = models.StatcastPitch
    hrs = session.execute(
        select(func.count(P.pitch_uid)).where(
            P.batter_id == batter_id,
            P.game_id == game_id,
            P.events == "home_run",
        )
    ).scalar()
    return 1 if (hrs or 0) > 0 else 0


@dataclass
class FeatureRow:
    batter_id: int
    pitcher_id: Optional[int]
    game_id: Optional[int]
    game_date: date
    feature_set_version: str
    as_of_date: date
    window_days: int
    window_start: date
    window_end: date
    features: Dict[str, Optional[float]] = field(default_factory=dict)
    samples: Dict[str, Optional[int]] = field(default_factory=dict)
    missing: list = field(default_factory=list)
    hit_hr: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "batter_id": self.batter_id,
            "pitcher_id": self.pitcher_id,
            "game_id": self.game_id,
            "game_date": self.game_date,
            "feature_set_version": self.feature_set_version,
            "as_of_date": self.as_of_date,
            "window_days": self.window_days,
            "hit_hr": self.hit_hr,
        }
        payload.update(self.features)
        payload.update(self.samples)
        return payload


def build_features(
    session: Session,
    batter_id: int,
    target_game_date: date,
    pitcher_id: Optional[int] = None,
    game_id: Optional[int] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    venue_id: Optional[int] = None,
    venue_name: Optional[str] = None,
    include_label: bool = False,
) -> FeatureRow:
    """Compute the V1 feature vector strictly from data before the target game."""
    start, end = historical_bounds(target_game_date, window_days)
    as_of = end

    batter = session.get(models.Player, batter_id)
    pitcher = session.get(models.Player, pitcher_id) if pitcher_id else None

    # Resolve game context (venue) without ever reading target-game Statcast.
    game_row = session.get(models.Game, game_id) if game_id else None
    if game_row is not None:
        venue_id = venue_id if venue_id is not None else game_row.venue_id
        venue_name = venue_name or game_row.venue

    b = _batter_window_stats(session, batter_id, start, end)
    p = (
        _pitcher_window_stats(session, pitcher_id, start, end)
        if pitcher_id is not None
        else None
    )

    features: Dict[str, Optional[float]] = {name: None for name in FEATURE_NAMES}
    samples: Dict[str, Optional[int]] = {name: None for name in SAMPLE_FIELDS}

    samples["batter_pitches_30d"] = b["pitches"]
    samples["batter_batted_balls_30d"] = b["batted_balls"]
    samples["batter_pa_30d"] = b["plate_appearances"]
    samples["batter_home_runs_30d"] = b["home_runs"]
    features["batter_barrel_rate_30d"] = _rate(b["barrels"], b["batted_balls"])
    features["batter_hard_hit_rate_30d"] = _rate(b["hard_hit"], b["batted_balls"])
    features["batter_fly_ball_rate_30d"] = _rate(b["fly_balls"], b["batted_balls"])
    features["batter_max_exit_velocity_30d"] = b["max_exit_velocity"]
    features["batter_hr_per_pa_30d"] = _rate(b["home_runs"], b["plate_appearances"])

    if p is not None:
        samples["pitcher_pitches_30d"] = p["pitches"]
        samples["pitcher_batted_balls_30d"] = p["batted_balls"]
        samples["pitcher_pa_30d"] = p["plate_appearances"]
        samples["pitcher_home_runs_allowed_30d"] = p["home_runs"]
        features["pitcher_barrel_rate_allowed_30d"] = _rate(
            p["barrels"], p["batted_balls"]
        )
        features["pitcher_hard_hit_rate_allowed_30d"] = _rate(
            p["hard_hit"], p["batted_balls"]
        )
        features["pitcher_fly_ball_rate_allowed_30d"] = _rate(
            p["fly_balls"], p["batted_balls"]
        )
        features["pitcher_hr_per_pa_allowed_30d"] = _rate(
            p["home_runs"], p["plate_appearances"]
        )

    features["platoon_advantage"] = platoon_advantage(
        batter.bats if batter else None, pitcher.throws if pitcher else None
    )

    # Park factors — existing system only, no new architecture, no shrinkage.
    if venue_id is not None or venue_name:
        park = park_factors_for_venue(
            session, venue_id=venue_id, venue_name=venue_name
        )
        if park:
            features["park_hr_factor"] = park.get("hr_factor")
            hand = (batter.bats if batter else None) or ""
            hand = hand.strip().upper()[:1]
            if hand == "L":
                features["park_hr_factor_handedness"] = park.get("hr_factor_lhb")
            elif hand == "R":
                features["park_hr_factor_handedness"] = park.get("hr_factor_rhb")

    # Weather / lineup — stored rows only, no external calls from here.
    if game_id is not None:
        weather = session.get(models.GameWeather, game_id)
        if weather is not None:
            features["weather_temperature_f"] = weather.temperature_f
            features["weather_wind_out_component"] = weather.wind_out_mph

        lineup = lineup_slot_for_player(session, batter_id, game_id)
        if lineup:
            if lineup.get("batting_order") is not None:
                features["lineup_slot"] = float(lineup["batting_order"])
            if lineup.get("expected_plate_appearances") is not None:
                features["expected_plate_appearances"] = float(
                    lineup["expected_plate_appearances"]
                )

    hit_hr = None
    if include_label and game_id is not None:
        hit_hr = label_for_target_game(session, batter_id, game_id)

    return FeatureRow(
        batter_id=batter_id,
        pitcher_id=pitcher_id,
        game_id=game_id,
        game_date=target_game_date,
        feature_set_version=FEATURE_SET_VERSION,
        as_of_date=as_of,
        window_days=window_days,
        window_start=start,
        window_end=end,
        features=features,
        samples=samples,
        missing=[k for k, v in features.items() if v is None],
        hit_hr=hit_hr,
    )


def upsert_snapshot(session: Session, row: FeatureRow) -> models.FeatureSnapshot:
    """Idempotent write keyed on (batter_id, game_id, feature_set_version)."""
    if row.game_id is None:
        raise ValueError("a snapshot requires a game_id")
    existing = session.execute(
        select(models.FeatureSnapshot).where(
            models.FeatureSnapshot.batter_id == row.batter_id,
            models.FeatureSnapshot.game_id == row.game_id,
            models.FeatureSnapshot.feature_set_version == row.feature_set_version,
        )
    ).scalars().first()
    payload = row.as_dict()
    payload["created_at"] = datetime.utcnow()
    if existing is None:
        existing = models.FeatureSnapshot(**payload)
        session.add(existing)
    else:
        for key, value in payload.items():
            if key != "created_at":
                setattr(existing, key, value)
    session.flush()
    return existing


__all__ = [
    "FEATURE_SET_VERSION",
    "FEATURE_NAMES",
    "SAMPLE_FIELDS",
    "DEFAULT_WINDOW_DAYS",
    "FeatureRow",
    "build_features",
    "historical_bounds",
    "label_for_target_game",
    "platoon_advantage",
    "upsert_snapshot",
]
