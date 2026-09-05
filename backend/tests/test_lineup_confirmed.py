"""Official-lineup-only final predictions.

Guarantees under test:

* a final prediction is produced only for hitters in an MLB-posted (confirmed)
  starting lineup, for games where both teams have posted;
* projected lineups never produce a final prediction;
* an unconfirmed game never blocks the rest of the slate;
* predictions are locked at first pitch and never rewritten afterwards.

Fully isolated: in-memory SQLite, temp model artifact, no network, no ingestion,
no training against production data.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import models  # noqa: E402
from app.database.session import Base  # noqa: E402
from app.services import hr_model  # noqa: E402
from app.services.lineups import game_lineup_confirmation  # noqa: E402
from app.services.scoring import (  # noqa: E402
    score_slate,
    store_slate_predictions,
    stored_predictions,
)
from tests.test_hr_model_pipeline import synthetic_rows  # noqa: E402

DAY = date(2026, 8, 23)
GAME_A = 910001
GAME_B = 910002


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
    monkeypatch.setenv("MODEL_ARTIFACT_PATH", str(tmp_path / "hr_model_test.json"))
    monkeypatch.setenv("MODEL_ARTIFACT_DB_PERSIST", "0")
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})
    artifact = hr_model.train_model(synthetic_rows(), min_rows=200)
    hr_model.save_artifact(artifact)
    yield artifact
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})


def _game(session, game_id: int, *, first_pitch: datetime | None = None) -> None:
    session.add(
        models.Game(
            game_id=game_id,
            game_date=DAY,
            game_datetime=first_pitch
            or datetime.now(timezone.utc) + timedelta(hours=6),
            status="Scheduled",
            home_team_id=147,
            away_team_id=111,
            venue="Yankee Stadium",
        )
    )
    session.flush()


def _lineup(
    session,
    game_id: int,
    side: str,
    player_ids,
    *,
    status: str = "confirmed",
) -> None:
    for i, pid in enumerate(player_ids, start=1):
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


def _confirmed_game(session, game_id: int, base: int, **kw) -> None:
    _game(session, game_id, **kw)
    _lineup(session, game_id, "home", [base + 1, base + 2])
    _lineup(session, game_id, "away", [base + 3, base + 4])


def _scored_player_ids(result) -> set[int]:
    return {p["player_id"] for p in result["predictions"]}


# ---------------------------------------------------------------------------
# 1-3. Confirmed vs projected
# ---------------------------------------------------------------------------

def test_projected_only_game_is_not_scored(session, trained):
    _game(session, GAME_A)
    _lineup(session, GAME_A, "home", [1, 2], status="projected")
    _lineup(session, GAME_A, "away", [3, 4], status="projected")

    result = score_slate(session, DAY)
    assert result["status"] == "unavailable"
    assert "no_confirmed_lineups" in result["reason"]
    assert result["predictions"] == []


def test_confirmed_game_scores_only_official_starters(session, trained):
    _confirmed_game(session, GAME_A, 100)
    # A projected bench alternative for the same game must never be scored.
    _lineup(session, GAME_A, "home", [199], status="projected")

    result = score_slate(session, DAY)
    assert result["status"] == "ok"
    assert _scored_player_ids(result) == {101, 102, 103, 104}
    assert 199 not in _scored_player_ids(result)


def test_partially_confirmed_game_is_awaiting(session, trained):
    _game(session, GAME_A)
    _lineup(session, GAME_A, "home", [1, 2])           # posted
    _lineup(session, GAME_A, "away", [3, 4], status="projected")  # not posted

    info = game_lineup_confirmation(session, DAY)[GAME_A]
    assert info["is_confirmed"] is False
    assert info["lineup_status"] == "awaiting_confirmed_lineup"
    assert score_slate(session, DAY)["predictions"] == []


# ---------------------------------------------------------------------------
# 4-5. Slate independence and lineup changes
# ---------------------------------------------------------------------------

def test_unconfirmed_game_does_not_block_the_slate(session, trained):
    _confirmed_game(session, GAME_A, 100)
    _game(session, GAME_B)
    _lineup(session, GAME_B, "home", [201, 202], status="projected")

    result = score_slate(session, DAY)
    assert {p["game_id"] for p in result["predictions"]} == {GAME_A}
    assert result["games_confirmed"] == 1
    awaiting = {g["game_id"] for g in result["games_awaiting_confirmed_lineups"]}
    assert awaiting == {GAME_B}


def test_lineup_change_before_first_pitch_recalculates(session, trained):
    _confirmed_game(session, GAME_A, 100)
    first = store_slate_predictions(session, DAY)
    assert first["inserted"] == 4

    # MLB replaces a posted starter — restore the official set for the side.
    session.query(models.GameLineup).filter(
        models.GameLineup.game_id == GAME_A,
        models.GameLineup.player_id == 101,
    ).delete()
    _lineup(session, GAME_A, "home", [105])
    session.flush()

    second = store_slate_predictions(session, DAY)
    assert second["status"] == "ok"
    assert second["inserted"] >= 1  # the replacement starter is now scored
    stored = stored_predictions(session, DAY)
    assert 105 in {p["player_id"] for p in stored["predictions"]}


def test_doubleheader_games_are_gated_independently(session, trained):
    _confirmed_game(session, GAME_A, 100)                     # game 1 posted
    _game(session, GAME_B)                                    # game 2 same teams
    _lineup(session, GAME_B, "home", [301, 302], status="projected")
    _lineup(session, GAME_B, "away", [303, 304], status="projected")

    result = store_slate_predictions(session, DAY)
    assert result["games_scored"] == 1
    assert {g["game_id"] for g in result["games_awaiting_confirmed_lineups"]} == {GAME_B}


# ---------------------------------------------------------------------------
# 6-8. First-pitch locking
# ---------------------------------------------------------------------------

def test_predictions_lock_at_first_pitch(session, trained):
    _confirmed_game(session, GAME_A, 100)
    store_slate_predictions(session, DAY)
    locked = {
        p["player_id"]: p["hr_probability"] for p in stored_predictions(session, DAY)["predictions"]
    }

    # First pitch passes, then a late lineup edit arrives.
    game = session.get(models.Game, GAME_A)
    game.game_datetime = datetime.now(timezone.utc) - timedelta(minutes=5)
    _lineup(session, GAME_A, "home", [150])
    session.flush()

    after = store_slate_predictions(session, DAY)
    assert after["locked_games"] == [GAME_A]
    assert after["inserted"] == 0 and after["updated"] == 0
    still = {
        p["player_id"]: p["hr_probability"] for p in stored_predictions(session, DAY)["predictions"]
    }
    assert still == locked  # untouched, and no post-first-pitch row added


def test_started_game_never_receives_a_new_prediction(session, trained):
    _confirmed_game(
        session, GAME_A, 100, first_pitch=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    result = store_slate_predictions(session, DAY)
    assert result["status"] == "unavailable"
    assert "all_games_locked" in result["reason"]
    assert stored_predictions(session, DAY)["predictions"] == []


def test_locking_is_per_game(session, trained):
    _confirmed_game(
        session, GAME_A, 100, first_pitch=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    _confirmed_game(session, GAME_B, 200)  # starts later today

    result = store_slate_predictions(session, DAY)
    assert result["locked_games"] == [GAME_A]
    assert {p["game_id"] for p in stored_predictions(session, DAY)["predictions"]} == {GAME_B}


# ---------------------------------------------------------------------------
# 9-11. Provenance, evaluation input and manual/scheduled entry points
# ---------------------------------------------------------------------------

def test_stored_rows_are_stamped_confirmed(session, trained):
    _confirmed_game(session, GAME_A, 100)
    store_slate_predictions(session, DAY)
    rows = stored_predictions(session, DAY)["predictions"]
    assert rows and all(r["lineup_status"] == "confirmed" for r in rows)


def test_evaluation_resolves_the_locked_prediction(session, trained):
    from app.services.evaluation import resolve_slate

    _confirmed_game(session, GAME_A, 100)
    store_slate_predictions(session, DAY)
    before = {
        p["player_id"]: p["hr_probability"] for p in stored_predictions(session, DAY)["predictions"]
    }

    resolve_slate(session, DAY)
    after = {
        p["player_id"]: p["hr_probability"] for p in stored_predictions(session, DAY)["predictions"]
    }
    assert after == before  # resolution never rewrites the locked probability


def test_manual_and_scheduled_scoring_share_the_gate(session, trained):
    _confirmed_game(session, GAME_A, 100)
    _game(session, GAME_B)
    _lineup(session, GAME_B, "home", [401, 402], status="projected")

    manual = store_slate_predictions(session, DAY)
    assert manual["status"] == "ok" and manual["games_scored"] == 1
    # Re-running (as the scheduler does) is idempotent and still skips GAME_B.
    again = store_slate_predictions(session, DAY)
    assert again["inserted"] == 0 and again["updated"] == manual["inserted"]
    assert {p["game_id"] for p in stored_predictions(session, DAY)["predictions"]} == {GAME_A}
