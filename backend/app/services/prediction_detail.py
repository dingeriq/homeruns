"""Assembles the Prediction Detail payload from whatever the database holds.

Design rules:

* Never fabricate a probability, confidence, weight or park/weather constant.
* Anything that cannot be sourced from the database is returned as ``None`` and
  listed in ``data_availability.unavailable``.
* Only descriptive aggregates over stored Statcast rows are computed here — no
  modelling. The real DingerIQ model plugs into ``prediction`` / ``explanation``
  and consumes ``model_features``.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import models
from app.services.lineups import lineup_slot_for_player
from app.services.odds_service import market_for_player
from app.services.park_factors import park_factors_for_venue
from app.services.weather_service import weather_for_game
from app.models.prediction_detail import (
    DataAvailability,
    Explanation,
    GameInfo,
    HitterMetrics,
    LineupInfo,
    MarketOdds,
    MatchupInfo,
    ModelFeatures,
    ParkFactors,
    PitchMix,
    PitchTypeUsage,
    PitcherInfo,
    PitcherMetrics,
    PlayerInfo,
    PredictionDetail,
    PredictionResult,
    RecentPerformance,
    RecentPerformanceGame,
    StatcastMetrics,
    WeatherFactors,
)

# Feature slots the model will eventually consume. Values are only populated
# from real stored data; everything else is reported as missing.
FEATURE_SLOTS: List[str] = [
    "batter_barrel_rate",
    "batter_hard_hit_rate",
    "batter_avg_exit_velocity",
    "batter_max_exit_velocity",
    "batter_avg_launch_angle",
    "batter_xwoba",
    "batter_hr_rate_recent",
    "pitcher_barrel_rate_allowed",
    "pitcher_hard_hit_rate_allowed",
    "pitcher_avg_exit_velocity_allowed",
    "pitcher_avg_velocity",
    "pitcher_hr_allowed_recent",
    "platoon_advantage",
    "park_hr_factor",
    "park_hr_factor_handedness",
    "weather_temperature_f",
    "weather_wind_out_component",
    "vegas_implied_team_total",
    "lineup_slot",
    "expected_plate_appearances",
]

DEFAULT_WINDOW_DAYS = 30
RECENT_WINDOW_DAYS = 15


def _rate(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if not denominator:
        return None
    return round((numerator or 0) / denominator, 4)


def _round(value, digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _statcast_window(session: Session, days: int) -> date:
    latest = session.execute(select(func.max(models.StatcastPitch.game_date))).scalar()
    anchor = latest or date.today()
    return anchor - timedelta(days=days)


def _hitter_metrics(session: Session, batter_id: int, since: date) -> Optional[HitterMetrics]:
    P = models.StatcastPitch
    row = session.execute(
        select(
            func.count(P.pitch_uid),
            func.count(P.exit_velocity),
            func.avg(P.exit_velocity),
            func.max(P.exit_velocity),
            func.avg(P.launch_angle),
            func.sum(func.cast(P.barrel_flag, models.Integer)),
            func.sum(func.cast(P.hard_hit_flag, models.Integer)),
            func.avg(P.estimated_woba),
        ).where(P.batter_id == batter_id, P.game_date >= since)
    ).one()
    pitches, bbe, ev, max_ev, la, barrels, hard_hit, xwoba = row
    if not pitches:
        return None
    hrs = session.execute(
        select(func.count(P.pitch_uid)).where(
            P.batter_id == batter_id, P.game_date >= since, P.events == "home_run"
        )
    ).scalar()
    return HitterMetrics(
        window_days=DEFAULT_WINDOW_DAYS,
        batted_balls=int(bbe or 0),
        avg_exit_velocity=_round(ev, 1),
        max_exit_velocity=_round(max_ev, 1),
        avg_launch_angle=_round(la, 1),
        barrel_rate=_rate(int(barrels or 0), int(bbe or 0)),
        hard_hit_rate=_rate(int(hard_hit or 0), int(bbe or 0)),
        avg_estimated_woba=_round(xwoba, 3),
        home_runs=int(hrs or 0),
    )


def _pitcher_metrics(session: Session, pitcher_id: int, since: date) -> Optional[PitcherMetrics]:
    P = models.StatcastPitch
    row = session.execute(
        select(
            func.count(P.pitch_uid),
            func.count(P.exit_velocity),
            func.avg(P.velocity),
            func.avg(P.spin_rate),
            func.avg(P.exit_velocity),
            func.sum(func.cast(P.barrel_flag, models.Integer)),
            func.sum(func.cast(P.hard_hit_flag, models.Integer)),
        ).where(P.pitcher_id == pitcher_id, P.game_date >= since)
    ).one()
    pitches, bbe, velo, spin, ev, barrels, hard_hit = row
    if not pitches:
        return None
    hrs = session.execute(
        select(func.count(P.pitch_uid)).where(
            P.pitcher_id == pitcher_id, P.game_date >= since, P.events == "home_run"
        )
    ).scalar()
    return PitcherMetrics(
        window_days=DEFAULT_WINDOW_DAYS,
        pitches=int(pitches),
        batted_balls_allowed=int(bbe or 0),
        avg_velocity=_round(velo, 1),
        avg_spin_rate=_round(spin, 0),
        avg_exit_velocity_allowed=_round(ev, 1),
        barrel_rate_allowed=_rate(int(barrels or 0), int(bbe or 0)),
        hard_hit_rate_allowed=_rate(int(hard_hit or 0), int(bbe or 0)),
        home_runs_allowed=int(hrs or 0),
    )


def _pitch_mix(session: Session, pitcher_id: int, since: date) -> Optional[PitchMix]:
    P = models.StatcastPitch
    rows = session.execute(
        select(
            P.pitch_type,
            P.pitch_name,
            func.count(P.pitch_uid),
            func.avg(P.velocity),
            func.avg(P.spin_rate),
        )
        .where(P.pitcher_id == pitcher_id, P.game_date >= since)
        .group_by(P.pitch_type, P.pitch_name)
        .order_by(func.count(P.pitch_uid).desc())
    ).all()
    if not rows:
        return None
    total = sum(r[2] for r in rows)
    return PitchMix(
        window_days=DEFAULT_WINDOW_DAYS,
        total_pitches=total,
        pitches=[
            PitchTypeUsage(
                pitch_type=r[0],
                pitch_name=r[1],
                count=int(r[2]),
                usage_rate=_rate(int(r[2]), total),
                avg_velocity=_round(r[3], 1),
                avg_spin_rate=_round(r[4], 0),
            )
            for r in rows
        ],
    )


def _statcast_coverage(
    session: Session, batter_id: int, pitcher_id: Optional[int]
) -> Optional[StatcastMetrics]:
    P = models.StatcastPitch
    batter_count, first_d, last_d = session.execute(
        select(func.count(P.pitch_uid), func.min(P.game_date), func.max(P.game_date)).where(
            P.batter_id == batter_id
        )
    ).one()
    pitcher_count = None
    if pitcher_id is not None:
        pitcher_count = session.execute(
            select(func.count(P.pitch_uid)).where(P.pitcher_id == pitcher_id)
        ).scalar()
    if not batter_count and not pitcher_count:
        return None
    return StatcastMetrics(
        batter_pitches_tracked=int(batter_count or 0),
        pitcher_pitches_tracked=None if pitcher_count is None else int(pitcher_count),
        first_tracked_date=first_d.isoformat() if first_d else None,
        last_tracked_date=last_d.isoformat() if last_d else None,
    )


def _recent_performance(session: Session, batter_id: int) -> Optional[RecentPerformance]:
    P = models.StatcastPitch
    since = _statcast_window(session, RECENT_WINDOW_DAYS)
    rows = session.execute(
        select(
            P.game_date,
            func.count(P.exit_velocity),
            func.max(P.exit_velocity),
            func.sum(func.cast(P.barrel_flag, models.Integer)),
        )
        .where(P.batter_id == batter_id, P.game_date >= since)
        .group_by(P.game_date)
        .order_by(P.game_date.desc())
    ).all()
    if not rows:
        return None
    hr_rows = dict(
        session.execute(
            select(P.game_date, func.count(P.pitch_uid))
            .where(P.batter_id == batter_id, P.game_date >= since, P.events == "home_run")
            .group_by(P.game_date)
        ).all()
    )
    return RecentPerformance(
        window_days=RECENT_WINDOW_DAYS,
        games=[
            RecentPerformanceGame(
                game_date=r[0].isoformat(),
                batted_balls=int(r[1] or 0),
                home_runs=int(hr_rows.get(r[0], 0)),
                max_exit_velocity=_round(r[2], 1),
                barrels=int(r[3] or 0),
            )
            for r in rows
        ],
    )


def _head_to_head(session: Session, batter_id: int, pitcher_id: int) -> Dict[str, int]:
    P = models.StatcastPitch
    pitches, bbe = session.execute(
        select(func.count(P.pitch_uid), func.count(P.exit_velocity)).where(
            P.batter_id == batter_id, P.pitcher_id == pitcher_id
        )
    ).one()
    hrs = session.execute(
        select(func.count(P.pitch_uid)).where(
            P.batter_id == batter_id, P.pitcher_id == pitcher_id, P.events == "home_run"
        )
    ).scalar()
    return {
        "head_to_head_pitches": int(pitches or 0),
        "head_to_head_batted_balls": int(bbe or 0),
        "head_to_head_home_runs": int(hrs or 0),
    }


class _LineupGame:
    """Minimal stand-in for a ``games`` row, built from stored lineup data.

    Used only when the schedule table has no row for today but ``game_lineups``
    already identifies the game. Exposes the same attribute surface the caller
    reads; unknown schedule fields stay ``None``.
    """

    def __init__(self, game_id: int, game_day: date, home_team, away_team, side: str):
        self.game_id = game_id
        self.game_date = game_day
        self.game_datetime = datetime.combine(game_day, time.min)
        self.home_team = home_team
        self.away_team = away_team
        self.venue = None
        self.venue_id = None
        self.status = "lineup-only"
        self.home_probable_pitcher = None
        self.away_probable_pitcher = None
        self.home_probable_pitcher_id = None
        self.away_probable_pitcher_id = None
        self.side = side


def _game_from_lineups(session: Session, team_abbr: str, day: date):
    """Fallback game resolution from ``game_lineups`` (schedule row missing)."""
    L = models.GameLineup
    row = session.execute(
        select(L)
        .where(L.game_date == day, L.team_abbreviation == team_abbr)
        .order_by(L.game_id)
    ).scalars().first()
    if row is None:
        return None
    opponent = session.execute(
        select(L.team_abbreviation)
        .where(L.game_id == row.game_id, L.side != row.side)
        .limit(1)
    ).scalars().first()
    opponent = opponent or "TBD"
    is_home = row.side == "home"
    return _LineupGame(
        game_id=row.game_id,
        game_day=row.game_date or day,
        home_team=team_abbr if is_home else opponent,
        away_team=opponent if is_home else team_abbr,
        side=row.side,
    )


def _find_todays_game(session: Session, team_abbr: Optional[str]):
    if not team_abbr:
        return None
    today = date.today()
    G = models.Game
    row = session.execute(
        select(G)
        .where(
            G.game_date == today,
            (G.home_team == team_abbr) | (G.away_team == team_abbr),
        )
        .order_by(G.game_datetime)
    ).scalars().first()
    if row is not None:
        return row
    return _game_from_lineups(session, team_abbr, today)



def _resolve_pitcher(
    session: Session, name: Optional[str], pitcher_id: Optional[int] = None
) -> Optional[PitcherInfo]:
    if not name and pitcher_id is None:
        return None
    row = None
    source = "game.probable_pitcher_id"
    if pitcher_id is not None:
        row = session.get(models.Player, pitcher_id)
    if row is None and name:
        source = "game.probable_pitcher (name match)"
        row = session.execute(
            select(models.Player).where(models.Player.full_name == name)
        ).scalars().first()
    if row is None:
        return PitcherInfo(
            id=pitcher_id,
            full_name=name or "",
            source="game.probable_pitcher (unmatched in players)",
        )
    return PitcherInfo(
        id=row.id,
        full_name=row.full_name,
        team_abbreviation=row.team_abbreviation,
        throws=row.throws,
        source=source,
    )



def build_prediction_detail(session: Session, player_id: int) -> Optional[PredictionDetail]:
    player_row = session.get(models.Player, player_id)
    if player_row is None:
        return None

    player = PlayerInfo(
        id=player_row.id,
        full_name=player_row.full_name,
        team_id=player_row.team_id,
        team_abbreviation=player_row.team_abbreviation,
        position=player_row.position,
        bats=player_row.bats,
        throws=player_row.throws,
    )

    available: List[str] = ["player"]
    unavailable: List[str] = []
    notes: Dict[str, str] = {}

    game_row = _find_todays_game(session, player_row.team_abbreviation)
    game: Optional[GameInfo] = None
    pitcher: Optional[PitcherInfo] = None
    if game_row is not None:
        is_home = game_row.home_team == player_row.team_abbreviation
        game = GameInfo(
            game_id=game_row.game_id,
            game_date=game_row.game_datetime,
            home_team=game_row.home_team,
            away_team=game_row.away_team,
            venue=game_row.venue,
            venue_id=game_row.venue_id,
            status=game_row.status,
            home_away="home" if is_home else "away",
            opponent_team=game_row.away_team if is_home else game_row.home_team,
        )
        available.append("game")
        opposing_name = (
            game_row.away_probable_pitcher if is_home else game_row.home_probable_pitcher
        )
        opposing_id = (
            game_row.away_probable_pitcher_id if is_home else game_row.home_probable_pitcher_id
        )
        pitcher = _resolve_pitcher(session, opposing_name, opposing_id)
        if pitcher is not None:
            available.append("pitcher")
        else:
            unavailable.append("pitcher")
            notes["pitcher"] = "No probable starter published yet for this game."
    else:
        unavailable.extend(["game", "pitcher"])
        notes["game"] = "No game scheduled today for this player's team (or schedule not synced)."

    since = _statcast_window(session, DEFAULT_WINDOW_DAYS)
    hitter_metrics = _hitter_metrics(session, player_id, since)
    (available if hitter_metrics else unavailable).append("hitter_metrics")
    if hitter_metrics is None:
        notes["hitter_metrics"] = "No Statcast rows for this batter in the rolling window."

    pitcher_id = pitcher.id if pitcher else None
    pitcher_metrics = _pitcher_metrics(session, pitcher_id, since) if pitcher_id else None
    (available if pitcher_metrics else unavailable).append("pitcher_metrics")
    if pitcher_metrics is None:
        notes["pitcher_metrics"] = "Opposing pitcher unknown or no Statcast rows in the window."

    pitch_mix = _pitch_mix(session, pitcher_id, since) if pitcher_id else None
    (available if pitch_mix else unavailable).append("pitch_mix")
    if pitch_mix is None:
        notes["pitch_mix"] = "Requires Statcast pitch rows for the opposing starter."

    statcast_metrics = _statcast_coverage(session, player_id, pitcher_id)
    (available if statcast_metrics else unavailable).append("statcast_metrics")
    if statcast_metrics is None:
        notes["statcast_metrics"] = "statcast_pitches has no rows for this batter/pitcher."

    recent = _recent_performance(session, player_id)
    (available if recent else unavailable).append("recent_performance")
    if recent is None:
        notes["recent_performance"] = "Requires Statcast rows in the recent window."

    matchup: Optional[MatchupInfo] = None
    if pitcher is not None:
        h2h = _head_to_head(session, player_id, pitcher.id) if pitcher.id else {}
        stands, throws = player_row.bats, pitcher.throws
        matchup = MatchupInfo(
            batter_stands=stands,
            pitcher_throws=throws,
            platoon_split=f"{stands} vs {throws}" if stands and throws else None,
            **h2h,
        )
        available.append("matchup")
    else:
        unavailable.append("matchup")
        notes["matchup"] = "Requires a resolved opposing starter."

    park_row = (
        park_factors_for_venue(session, venue_id=game.venue_id, venue_name=game.venue)
        if game
        else None
    )
    if park_row and any(
        park_row.get(k) is not None for k in ("hr_factor", "hr_factor_lhb", "hr_factor_rhb")
    ):
        park_factors = ParkFactors(
            venue=park_row.get("venue") or (game.venue if game else None),
            hr_factor=park_row.get("hr_factor"),
            hr_factor_lhb=park_row.get("hr_factor_lhb"),
            hr_factor_rhb=park_row.get("hr_factor_rhb"),
            season=park_row.get("season"),
            batted_balls=park_row.get("batted_balls"),
            home_runs=park_row.get("home_runs"),
            hr_rate=park_row.get("hr_rate"),
            league_hr_rate=park_row.get("league_hr_rate"),
            sample_note=park_row.get("sample_note"),
            source=park_row.get("source"),
        )
        available.append("park_factors")
    else:
        park_factors = ParkFactors(venue=game.venue if game else None, source=None)
        unavailable.append("park_factors")
        notes["park_factors"] = (
            park_row.get("sample_note")
            if park_row and park_row.get("sample_note")
            else "No park factor computed for this venue yet. Run POST /admin/park-factors-sync "
            "once enough Statcast batted balls are ingested."
        )

    weather_row = weather_for_game(game.game_id) if game else None
    if weather_row:
        weather = WeatherFactors(
            temperature_f=weather_row.get("temperature_f"),
            wind_speed_mph=weather_row.get("wind_speed_mph"),
            wind_direction=weather_row.get("wind_direction"),
            humidity_pct=weather_row.get("humidity_pct"),
            conditions=weather_row.get("conditions"),
            roof_status=weather_row.get("roof_status"),
            source=weather_row.get("source"),
        )
        available.append("weather")
    else:
        weather = WeatherFactors()
        unavailable.append("weather")
        notes["weather"] = (
            "No OpenWeather record stored for this game. Requires a game with a venue "
            "that has coordinates and a completed weather sync."
        )

    lineup_row = (
        lineup_slot_for_player(session, player_row.id, game.game_id) if game else None
    )
    if lineup_row:
        lineup = LineupInfo(
            status=lineup_row.get("status"),
            batting_order=lineup_row.get("batting_order"),
            position=lineup_row.get("position"),
            is_starter=lineup_row.get("is_starter"),
            team_abbreviation=lineup_row.get("team_abbreviation"),
            expected_plate_appearances=lineup_row.get("expected_plate_appearances"),
            expected_pa_samples=lineup_row.get("expected_pa_samples"),
            expected_pa_note=lineup_row.get("expected_pa_note"),
            source=lineup_row.get("source"),
        )
        available.append("lineup")
    else:
        lineup = LineupInfo(
            status="unavailable",
            reason=(
                "No confirmed or projected lineup stored for this game/hitter. "
                "Run POST /admin/lineups-sync once MLB posts the batting order."
            ),
        )
        unavailable.append("lineup")
        notes["lineup"] = lineup.reason or ""

    try:
        market_row = market_for_player(
            session, player_row.id, game.game_id if game else None
        )
    except Exception:  # odds tables absent / unreachable — never fatal
        market_row = None
    if market_row:
        market_odds = MarketOdds(
            status="available",
            market=market_row.get("market"),
            best_price=market_row.get("price"),
            odds_format=market_row.get("odds_format"),
            sportsbook=market_row.get("bookmaker"),
            sportsbook_title=market_row.get("bookmaker_title"),
            implied_probability=market_row.get("implied_probability"),
            no_vig_probability=market_row.get("no_vig_probability"),
            consensus_implied_probability=market_row.get("consensus_implied_probability"),
            under_no_price=market_row.get("under_no_price"),
            sportsbook_count=market_row.get("sportsbook_count"),
            last_updated=market_row.get("last_updated"),
            # No trained model -> no edge. Market data never becomes the model.
            model_vs_market_edge=None,
        )
        available.append("market_odds")
    else:
        market_odds = MarketOdds(
            status="unavailable",
            reason=(
                "No sportsbook HR market stored for this hitter. Run POST /admin/odds-sync "
                "once books post player props for the slate."
            ),
        )
        unavailable.append("market_odds")
        notes["market_odds"] = market_odds.reason or ""

    feature_values: Dict[str, Optional[float]] = {slot: None for slot in FEATURE_SLOTS}
    if lineup_row:
        if lineup_row.get("batting_order") is not None:
            feature_values["lineup_slot"] = float(lineup_row["batting_order"])
        if lineup_row.get("expected_plate_appearances") is not None:
            feature_values["expected_plate_appearances"] = float(
                lineup_row["expected_plate_appearances"]
            )
    if park_factors.hr_factor is not None:
        feature_values["park_hr_factor"] = park_factors.hr_factor
    hand_factor = (
        park_factors.hr_factor_lhb
        if player_row.bats == "L"
        else park_factors.hr_factor_rhb
        if player_row.bats == "R"
        else None
    )
    if hand_factor is not None:
        feature_values["park_hr_factor_handedness"] = hand_factor
    if weather_row:
        feature_values["weather_temperature_f"] = weather_row.get("temperature_f")
        feature_values["weather_wind_out_component"] = weather_row.get("wind_out_mph")
    if hitter_metrics:
        feature_values["batter_barrel_rate"] = hitter_metrics.barrel_rate
        feature_values["batter_hard_hit_rate"] = hitter_metrics.hard_hit_rate
        feature_values["batter_avg_exit_velocity"] = hitter_metrics.avg_exit_velocity
        feature_values["batter_max_exit_velocity"] = hitter_metrics.max_exit_velocity
        feature_values["batter_avg_launch_angle"] = hitter_metrics.avg_launch_angle
        feature_values["batter_xwoba"] = hitter_metrics.avg_estimated_woba
        feature_values["batter_hr_rate_recent"] = _rate(
            hitter_metrics.home_runs, hitter_metrics.batted_balls
        )
    if pitcher_metrics:
        feature_values["pitcher_barrel_rate_allowed"] = pitcher_metrics.barrel_rate_allowed
        feature_values["pitcher_hard_hit_rate_allowed"] = pitcher_metrics.hard_hit_rate_allowed
        feature_values["pitcher_avg_exit_velocity_allowed"] = (
            pitcher_metrics.avg_exit_velocity_allowed
        )
        feature_values["pitcher_avg_velocity"] = pitcher_metrics.avg_velocity
        feature_values["pitcher_hr_allowed_recent"] = _rate(
            pitcher_metrics.home_runs_allowed, pitcher_metrics.batted_balls_allowed
        )

    missing = [k for k, v in feature_values.items() if v is None]
    model_features = ModelFeatures(
        feature_set_version=None,
        computed_at=None,
        values=feature_values,
        missing=missing,
    )
    (available if len(missing) < len(feature_values) else unavailable).append("model_features")

    # Model scoring is delegated to the shared scoring service — no model logic
    # is duplicated here.
    scored = score_player(
        session,
        player_id,
        game_id=game.game_id if game else None,
        game_date=(game.game_date.date() if game and game.game_date else None),
        pitcher_id=pitcher_id,
        venue_id=game.venue_id if game else None,
        venue_name=game.venue if game else None,
    )
    if scored.get("status") == "ok":
        available.extend(["prediction"])
        unavailable.append("explanation")
        prediction = PredictionResult(
            hr_probability=scored.get("hr_probability"),
            confidence=scored.get("confidence"),
            model_version=scored.get("model_version"),
            generated_at=datetime.now(timezone.utc),
            status="ok",
        )
        notes["prediction"] = (
            f"Scored with {scored.get('model_version')} using "
            f"{scored.get('features_used')}/{scored.get('features_total')} real feature values; "
            "confidence is the share of inputs that were not median-imputed."
        )
        notes["explanation"] = (
            "Per-factor attributions (SHAP) are not served yet; no weights are invented."
        )
        explanation = Explanation(
            status="unavailable",
            summary=(
                "Model probability is available; per-factor attribution is not served yet."
            ),
        )
    else:
        unavailable.extend(["prediction", "explanation"])
        notes["prediction"] = scored.get(
            "reason",
            "No trained DingerIQ model is registered. Probability and confidence stay null "
            "rather than being fabricated.",
        )
        notes["explanation"] = "Explanations (SHAP) become available once the model is served."
        prediction = PredictionResult(
            status="unavailable",
            reason=scored.get(
                "reason",
                "model_not_available: no trained model artifact is registered for scoring.",
            ),
        )
        explanation = Explanation(
            status="unavailable",
            summary="Explanations require a served model; no factor weights are invented here.",
        )


    return PredictionDetail(
        player=player,
        game=game,
        pitcher=pitcher,
        matchup=matchup,
        hitter_metrics=hitter_metrics,
        pitcher_metrics=pitcher_metrics,
        pitch_mix=pitch_mix,
        statcast_metrics=statcast_metrics,
        recent_performance=recent,
        park_factors=park_factors,
        weather=weather,
        lineup=lineup,
        market_odds=market_odds,
        model_features=model_features,
        prediction=prediction,
        explanation=explanation,
        data_availability=DataAvailability(
            available=sorted(set(available)),
            unavailable=sorted(set(unavailable)),
            notes=notes,
        ),
    )


__all__ = ["build_prediction_detail", "FEATURE_SLOTS"]
