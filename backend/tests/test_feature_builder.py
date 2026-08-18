"""Leakage and correctness tests for the V1 feature builder.

Fully isolated: an in-memory SQLite database is created per test, so nothing
touches Postgres, production, or any external API.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import models  # noqa: E402
from app.database.session import Base  # noqa: E402
from app.services import feature_builder as fb  # noqa: E402

TARGET_DATE = date(2026, 8, 15)
BATTER = 111111
PITCHER = 222222
GAME = 900001


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _pitch(
    uid: str,
    game_date: date,
    *,
    game_id: int,
    at_bat: int,
    events: str | None = None,
    ev: float | None = 100.0,
    barrel: bool = False,
    hard_hit: bool = False,
    bb_type: str | None = None,
    batter_id: int = BATTER,
    pitcher_id: int = PITCHER,
) -> models.StatcastPitch:
    return models.StatcastPitch(
        pitch_uid=uid,
        game_id=game_id,
        game_date=game_date,
        at_bat_number=at_bat,
        pitch_number=1,
        pitcher_id=pitcher_id,
        batter_id=batter_id,
        exit_velocity=ev,
        launch_angle=25.0,
        barrel_flag=barrel,
        hard_hit_flag=hard_hit,
        bb_type=bb_type,
        events=events,
    )


def _seed_players(session, bats="R", throws="L"):
    session.add(models.Player(id=BATTER, full_name="Test Batter", bats=bats, throws="R"))
    session.add(models.Player(id=PITCHER, full_name="Test Pitcher", bats="R", throws=throws))
    session.flush()


# --- 1 & 2: window bounds -------------------------------------------------


def test_historical_bounds_exclude_target_date():
    start, end = fb.historical_bounds(TARGET_DATE, 30)
    assert end == date(2026, 8, 14)
    assert end < TARGET_DATE
    assert start == date(2026, 7, 16)


def test_target_game_pitches_are_excluded_from_features(session):
    _seed_players(session)
    # Historical: 1 barrel batted ball, no HR.
    session.add(
        _pitch("h1", date(2026, 8, 10), game_id=800001, at_bat=1, events="double",
               barrel=True, hard_hit=True, bb_type="line_drive")
    )
    # Target game: 1 HR batted ball — must never enter features.
    session.add(
        _pitch("t1", TARGET_DATE, game_id=GAME, at_bat=1, events="home_run",
               barrel=True, hard_hit=True, bb_type="fly_ball")
    )
    session.flush()

    row = fb.build_features(session, BATTER, TARGET_DATE, pitcher_id=PITCHER, game_id=GAME)
    assert row.samples["batter_batted_balls_30d"] == 1
    assert row.samples["batter_home_runs_30d"] == 0
    assert row.features["batter_hr_per_pa_30d"] == 0.0
    assert row.features["batter_fly_ball_rate_30d"] == 0.0


def test_future_games_are_excluded_from_features(session):
    _seed_players(session)
    session.add(_pitch("h1", date(2026, 8, 10), game_id=800001, at_bat=1, events="single"))
    session.add(
        _pitch("f1", date(2026, 8, 20), game_id=800002, at_bat=1, events="home_run",
               barrel=True)
    )
    session.flush()

    row = fb.build_features(session, BATTER, TARGET_DATE, pitcher_id=PITCHER, game_id=GAME)
    assert row.samples["batter_batted_balls_30d"] == 1  # only the 8/10 event
    assert row.samples["batter_home_runs_30d"] == 0
    assert row.features["batter_barrel_rate_30d"] == 0.0


def test_window_start_is_anchored_on_target_not_max_date(session):
    _seed_players(session)
    # Way outside a 30-day window ending 8/14.
    session.add(_pitch("old", date(2026, 6, 1), game_id=700001, at_bat=1, events="single"))
    session.flush()
    row = fb.build_features(session, BATTER, TARGET_DATE, pitcher_id=PITCHER)
    assert row.samples["batter_batted_balls_30d"] == 0
    assert row.window_end == date(2026, 8, 14)


# --- 3 & 4: label ---------------------------------------------------------


def test_label_comes_only_from_target_game(session):
    _seed_players(session)
    session.add(
        _pitch("h1", date(2026, 8, 10), game_id=800001, at_bat=1, events="home_run")
    )
    session.add(_pitch("t1", TARGET_DATE, game_id=GAME, at_bat=1, events="home_run"))
    session.flush()

    row = fb.build_features(
        session, BATTER, TARGET_DATE, pitcher_id=PITCHER, game_id=GAME, include_label=True
    )
    assert row.hit_hr == 1
    # The target-game HR must not be counted in the historical HR total.
    assert row.samples["batter_home_runs_30d"] == 1  # the 8/10 one only
    assert fb.label_for_target_game(session, BATTER, 800001) == 1


def test_label_zero_when_no_hr_in_target_game(session):
    _seed_players(session)
    session.add(_pitch("t1", TARGET_DATE, game_id=GAME, at_bat=1, events="strikeout", ev=None))
    session.flush()
    row = fb.build_features(
        session, BATTER, TARGET_DATE, pitcher_id=PITCHER, game_id=GAME, include_label=True
    )
    assert row.hit_hr == 0


# --- 5: empty samples -----------------------------------------------------


def test_empty_history_returns_null_rates_not_zero(session):
    _seed_players(session)
    row = fb.build_features(session, BATTER, TARGET_DATE, pitcher_id=PITCHER)
    for name in (
        "batter_barrel_rate_30d",
        "batter_hard_hit_rate_30d",
        "batter_fly_ball_rate_30d",
        "batter_hr_per_pa_30d",
        "batter_max_exit_velocity_30d",
        "pitcher_barrel_rate_allowed_30d",
        "pitcher_hr_per_pa_allowed_30d",
    ):
        assert row.features[name] is None, name
    # Denominators are still reported, as zero.
    assert row.samples["batter_batted_balls_30d"] == 0
    assert row.samples["pitcher_pa_30d"] == 0


def test_every_rate_has_a_denominator(session):
    _seed_players(session)
    row = fb.build_features(session, BATTER, TARGET_DATE, pitcher_id=PITCHER)
    for field in fb.SAMPLE_FIELDS:
        assert field in row.samples


# --- 6: platoon -----------------------------------------------------------


@pytest.mark.parametrize(
    "bats,throws,expected",
    [
        ("R", "L", 1.0),
        ("L", "R", 1.0),
        ("R", "R", 0.0),
        ("L", "L", 0.0),
        (None, "R", None),
        ("R", None, None),
    ],
)
def test_platoon_encoding(bats, throws, expected):
    assert fb.platoon_advantage(bats, throws) == expected


def test_platoon_from_stored_hands(session):
    _seed_players(session, bats="L", throws="L")
    row = fb.build_features(session, BATTER, TARGET_DATE, pitcher_id=PITCHER)
    assert row.features["platoon_advantage"] == 0.0


# --- 7 & 8: snapshots -----------------------------------------------------


def test_snapshot_records_feature_set_version(session):
    _seed_players(session)
    row = fb.build_features(
        session, BATTER, TARGET_DATE, pitcher_id=PITCHER, game_id=GAME, include_label=True
    )
    snap = fb.upsert_snapshot(session, row)
    assert snap.feature_set_version == fb.FEATURE_SET_VERSION == "v1"
    assert snap.as_of_date == date(2026, 8, 14) < snap.game_date
    assert snap.window_days == 30


def test_snapshot_uniqueness_blocks_duplicates(session):
    _seed_players(session)
    row = fb.build_features(session, BATTER, TARGET_DATE, pitcher_id=PITCHER, game_id=GAME)
    fb.upsert_snapshot(session, row)
    session.commit()

    # The upsert path is idempotent…
    fb.upsert_snapshot(session, row)
    session.commit()
    assert session.query(models.FeatureSnapshot).count() == 1

    # …and a raw duplicate insert is rejected by the constraint.
    session.add(
        models.FeatureSnapshot(
            batter_id=BATTER,
            pitcher_id=PITCHER,
            game_id=GAME,
            game_date=TARGET_DATE,
            feature_set_version=fb.FEATURE_SET_VERSION,
            as_of_date=date(2026, 8, 14),
            created_at=datetime.utcnow(),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- 9: game-lineup fallback ---------------------------------------------


def test_game_lineup_fallback_resolves_game(session):
    from app.services import prediction_detail as pd

    today = date.today()
    session.add(
        models.GameLineup(
            game_id=GAME, game_date=today, side="away", team_abbreviation="WSH",
            player_id=BATTER, batting_order=3, status="projected",
        )
    )
    session.add(
        models.GameLineup(
            game_id=GAME, game_date=today, side="home", team_abbreviation="NYM",
            player_id=333333, batting_order=1, status="projected",
        )
    )
    session.flush()

    # games table is empty on purpose.
    assert session.query(models.Game).count() == 0
    game = pd._find_todays_game(session, "WSH")
    assert game is not None
    assert game.game_id == GAME
    assert game.away_team == "WSH"
    assert game.home_team == "NYM"
    assert game.status == "lineup-only"


def test_game_lineup_fallback_returns_none_without_lineups(session):
    from app.services import prediction_detail as pd

    assert pd._find_todays_game(session, "WSH") is None
