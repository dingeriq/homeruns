"""Canonical UTC time handling, slate-date stability and pregame prediction lock.

Fully isolated: in-memory SQLite, temp model artifact, no network, no ingestion,
no training against production data, no historical rows touched.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import models  # noqa: E402
from app.database.session import Base  # noqa: E402
from app.services import hr_model  # noqa: E402
from app.services.evaluation import resolve_slate  # noqa: E402
from app.services.lineups import game_lineup_confirmation, slate_game_ids  # noqa: E402
from app.services.scoring import (  # noqa: E402
    first_pitch_passed,
    store_slate_predictions,
    stored_predictions,
)
from app.services.timeutils import (  # noqa: E402
    SLATE_TZ,
    ensure_utc,
    slate_date_for,
    slate_today,
    utcnow,
)
from tests.test_hr_model_pipeline import synthetic_rows  # noqa: E402

DAY = date(2026, 9, 3)
GAME_A = 920001
GAME_B = 920002
EASTERN = ZoneInfo("America/New_York")


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


@pytest.fixture()
def trained(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_ARTIFACT_PATH", str(tmp_path / "hr_model_time_test.json"))
    monkeypatch.setenv("MODEL_ARTIFACT_DB_PERSIST", "0")
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})
    artifact = hr_model.train_model(synthetic_rows(), min_rows=200)
    hr_model.save_artifact(artifact)
    yield artifact
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})


def _game(session, game_id: int, first_pitch: datetime) -> None:
    session.add(
        models.Game(
            game_id=game_id,
            game_date=DAY,
            game_datetime=first_pitch,
            status="Scheduled",
            home_team="New York Yankees",
            away_team="Boston Red Sox",
            home_team_id=147,
            away_team_id=111,
            venue="Yankee Stadium",
        )
    )
    session.flush()


def _lineup(session, game_id: int, side: str, ids, status: str = "confirmed") -> None:
    for i, pid in enumerate(ids, start=1):
        session.add(
            models.GameLineup(
                game_id=game_id,
                game_date=DAY,
                side=side,
                player_id=pid,
                player_name=f"Player {pid}",
                team_abbreviation="NYY" if side == "home" else "BOS",
                batting_order=i,
                is_starter=True,
                status=status,
                source="test",
            )
        )
    session.flush()


def _confirmed_game(session, game_id: int, base: int, first_pitch: datetime) -> None:
    _game(session, game_id, first_pitch)
    _lineup(session, game_id, "home", [base + 1, base + 2])
    _lineup(session, game_id, "away", [base + 3, base + 4])


# ---------------------------------------------------------------------------
# 1. Canonical UTC
# ---------------------------------------------------------------------------

def test_utcnow_and_ensure_utc_are_timezone_aware():
    now = utcnow()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    naive = datetime(2026, 9, 4, 23, 10)
    assert ensure_utc(naive).tzinfo is timezone.utc
    eastern = datetime(2026, 9, 4, 19, 10, tzinfo=EASTERN)
    assert ensure_utc(eastern) == datetime(2026, 9, 4, 23, 10, tzinfo=timezone.utc)


def test_slate_timezone_is_iana_and_dst_aware():
    assert str(SLATE_TZ) == "America/New_York"
    summer = datetime(2026, 9, 4, 23, 10, tzinfo=timezone.utc)
    winter = datetime(2026, 11, 10, 0, 10, tzinfo=timezone.utc)
    # Same UTC clock time, different offsets — no fixed EST offset anywhere.
    assert summer.astimezone(SLATE_TZ).utcoffset() == timedelta(hours=-4)
    assert winter.astimezone(SLATE_TZ).utcoffset() == timedelta(hours=-5)


# ---------------------------------------------------------------------------
# 2. UTC midnight crossing must not roll the baseball slate date
# ---------------------------------------------------------------------------

def test_utc_midnight_crossing_keeps_the_slate_date():
    # 00:30 UTC on Sept 4 is 8:30pm ET on Sept 3 — still the Sept 3 slate.
    after_utc_midnight = datetime(2026, 9, 4, 0, 30, tzinfo=timezone.utc)
    assert after_utc_midnight.date() == date(2026, 9, 4)      # naive UTC view
    assert slate_today(after_utc_midnight) == date(2026, 9, 3)  # baseball day


def test_night_game_first_pitch_belongs_to_the_previous_slate_date():
    first_pitch = datetime(2026, 9, 4, 1, 40, tzinfo=timezone.utc)  # 9:40pm ET Sep 3
    assert slate_date_for(first_pitch) == date(2026, 9, 3)


# ---------------------------------------------------------------------------
# 3. Pregame lock
# ---------------------------------------------------------------------------

def test_first_pitch_passed_uses_backend_timestamp():
    future = models.Game(game_id=1, game_date=DAY, game_datetime=utcnow() + timedelta(hours=2))
    past = models.Game(game_id=2, game_date=DAY, game_datetime=utcnow() - timedelta(minutes=1))
    naive_future = models.Game(
        game_id=3,
        game_date=DAY,
        game_datetime=(utcnow() + timedelta(hours=2)).replace(tzinfo=None),
    )
    assert first_pitch_passed(future) is False
    assert first_pitch_passed(past) is True
    assert first_pitch_passed(naive_future) is False  # naive read-back == UTC
    assert first_pitch_passed(None) is False


def test_upcoming_game_updates_before_first_pitch(session, trained):
    _confirmed_game(session, GAME_A, 100, utcnow() + timedelta(hours=5))
    first = store_slate_predictions(session, DAY)
    assert first["inserted"] == 4 and first["locked_games"] == []
    again = store_slate_predictions(session, DAY)
    assert again["updated"] == 4  # still open, still recalculating


def test_prediction_locks_at_first_pitch(session, trained):
    _confirmed_game(session, GAME_A, 100, utcnow() + timedelta(hours=2))
    store_slate_predictions(session, DAY)
    before = {
        p["player_id"]: p["hr_probability"]
        for p in stored_predictions(session, DAY)["predictions"]
    }

    session.get(models.Game, GAME_A).game_datetime = utcnow() - timedelta(minutes=1)
    session.flush()
    after = store_slate_predictions(session, DAY)
    assert after["locked_games"] == [GAME_A]
    assert after["inserted"] == 0 and after["updated"] == 0
    assert {
        p["player_id"]: p["hr_probability"]
        for p in stored_predictions(session, DAY)["predictions"]
    } == before


def test_doubleheader_games_lock_independently(session, trained):
    _confirmed_game(session, GAME_A, 100, utcnow() - timedelta(hours=1))  # game 1 started
    _confirmed_game(session, GAME_B, 200, utcnow() + timedelta(hours=3))  # game 2 upcoming
    result = store_slate_predictions(session, DAY)
    assert result["locked_games"] == [GAME_A]
    assert result["games_scored"] == 1
    stored = stored_predictions(session, DAY)["predictions"]
    assert {p["game_id"] for p in stored} == {GAME_B}


def test_started_game_is_not_an_actionable_candidate(session, trained):
    _confirmed_game(session, GAME_A, 100, utcnow() - timedelta(hours=1))
    result = store_slate_predictions(session, DAY)
    assert result["status"] == "unavailable"
    assert "all_games_locked" in result["reason"]
    assert stored_predictions(session, DAY)["predictions"] == []


# ---------------------------------------------------------------------------
# 4. Final pregame value preserved for evaluation
# ---------------------------------------------------------------------------

def test_stored_row_is_stamped_as_locked_pregame(session, trained):
    _confirmed_game(session, GAME_A, 100, utcnow() + timedelta(hours=4))
    store_slate_predictions(session, DAY)
    rows = stored_predictions(session, DAY)["predictions"]
    assert rows and all(r["locked_pregame"] for r in rows)
    assert all(r["first_pitch_utc"] for r in rows)


def test_evaluation_uses_the_locked_pregame_probability(session, trained):
    _confirmed_game(session, GAME_A, 100, utcnow() + timedelta(hours=2))
    store_slate_predictions(session, DAY)
    locked = {
        p["player_id"]: (p["hr_probability"], p["confidence"])
        for p in stored_predictions(session, DAY)["predictions"]
    }

    # First pitch passes; a post-first-pitch scoring run must change nothing.
    session.get(models.Game, GAME_A).game_datetime = utcnow() - timedelta(minutes=5)
    session.flush()
    store_slate_predictions(session, DAY)
    resolve_slate(session, DAY)

    after = {
        p["player_id"]: (p["hr_probability"], p["confidence"])
        for p in stored_predictions(session, DAY)["predictions"]
    }
    assert after == locked


# ---------------------------------------------------------------------------
# 5. Slate consistency
# ---------------------------------------------------------------------------

def test_dashboard_and_lineup_status_share_one_slate_definition(session, trained):
    _confirmed_game(session, GAME_A, 100, utcnow() + timedelta(hours=3))
    # A game whose stored game_date rolled to the UTC next day, referenced only
    # by the slate's lineups — the historical source of the count mismatch.
    session.add(
        models.Game(
            game_id=GAME_B,
            game_date=DAY + timedelta(days=1),
            game_datetime=datetime(2026, 9, 4, 1, 40, tzinfo=timezone.utc),
            status="Scheduled",
            home_team="Los Angeles Dodgers",
            away_team="San Francisco Giants",
            venue="Dodger Stadium",
        )
    )
    session.flush()
    _lineup(session, GAME_B, "home", [301, 302], status="projected")
    _lineup(session, GAME_B, "away", [303, 304], status="projected")

    slate = slate_game_ids(session, DAY)
    confirmation = game_lineup_confirmation(session, DAY)
    assert slate == {GAME_A, GAME_B}
    assert set(confirmation) == slate  # identical game set, no 12-vs-15 drift
