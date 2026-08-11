"""Lineup ingestion tests — identity joins, projection fallback, empirical PA."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path/'lineups.db'}")
    import app.database.session as session_mod

    session_mod._engine = None
    session_mod._session_factory = None
    from app.database import models  # noqa: F401

    session_mod.Base.metadata.create_all(bind=session_mod.get_engine())
    yield session_mod
    session_mod._engine = None
    session_mod._session_factory = None


class FakeClient:
    """Stands in for MLBStatsClient with controllable payloads."""

    def __init__(self, schedule_payload, boxscore_payload=None):
        self._schedule = schedule_payload
        self._boxscore = boxscore_payload

    async def schedule(self, on):
        return self._schedule

    async def boxscore(self, game_pk):
        if self._boxscore is None:
            raise RuntimeError("boxscore not published")
        return self._boxscore


def _schedule(with_lineups: bool, game_id: int = 700001):
    game = {
        "gamePk": game_id,
        "teams": {
            "home": {"team": {"id": 147, "abbreviation": "NYY"}},
            "away": {"team": {"id": 111, "abbreviation": "BOS"}},
        },
    }
    if with_lineups:
        game["lineups"] = {
            "homePlayers": [
                {"id": 1000 + i, "fullName": f"Home Hitter {i}", "primaryPosition": {"abbreviation": "OF"}}
                for i in range(1, 10)
            ],
            "awayPlayers": [
                {"id": 2000 + i, "fullName": f"Away Hitter {i}", "primaryPosition": {"abbreviation": "1B"}}
                for i in range(1, 10)
            ],
        }
    return {"dates": [{"games": [game]}]}


def _seed_players(session_mod, ids):
    from app.database import models

    with session_mod.session_scope() as s:
        for pid in ids:
            s.add(models.Player(id=pid, full_name=f"Player {pid}", team_abbreviation="NYY", bats="R"))


def test_confirmed_lineup_ingested_and_ordered(sqlite_db):
    from app.services.lineups import lineups_for_game, sync_lineups

    _seed_players(sqlite_db, [1000 + i for i in range(1, 10)])
    result = asyncio.run(
        sync_lineups(on=date(2025, 6, 1), client=FakeClient(_schedule(True)))
    )
    assert result["confirmed_sides"] == 2
    assert result["lineup_slots_stored"] == 18
    # Away hitters aren't in players yet — reported, never silently dropped.
    assert 2001 in result["players_not_in_canonical_table"]

    rows = lineups_for_game(700001)
    home = [r for r in rows if r["side"] == "home"]
    assert [r["batting_order"] for r in home] == list(range(1, 10))
    assert home[0]["player_id"] == 1001
    assert home[0]["status"] == "confirmed"
    assert all(r["is_starter"] for r in home)


def test_sync_is_idempotent(sqlite_db):
    from app.services.lineups import lineups_for_game, sync_lineups

    for _ in range(2):
        asyncio.run(sync_lineups(on=date(2025, 6, 1), client=FakeClient(_schedule(True))))
    assert len(lineups_for_game(700001)) == 18


def test_boxscore_fallback_supplies_batting_order(sqlite_db):
    from app.services.lineups import lineups_for_game, sync_lineups

    box = {
        "teams": {
            "home": {
                "battingOrder": [3001, 3002, 3003],
                "players": {
                    f"ID{pid}": {
                        "person": {"id": pid, "fullName": f"Box {pid}"},
                        "position": {"abbreviation": "SS"},
                    }
                    for pid in (3001, 3002, 3003)
                },
            },
            "away": {"battingOrder": [], "players": {}},
        }
    }
    asyncio.run(
        sync_lineups(on=date(2025, 6, 2), client=FakeClient(_schedule(False, 700002), box))
    )
    rows = lineups_for_game(700002)
    assert [r["player_id"] for r in rows if r["side"] == "home"] == [3001, 3002, 3003]
    assert all(r["source"].endswith("battingOrder") for r in rows)


def test_projection_falls_back_to_stored_confirmed_history(sqlite_db):
    from app.services.lineups import sync_lineups, lineups_for_game

    # Day 1: confirmed lineup stored.
    asyncio.run(sync_lineups(on=date(2025, 6, 1), client=FakeClient(_schedule(True, 700001))))
    # Day 2: MLB has nothing and no boxscore exists -> project from history.
    result = asyncio.run(
        sync_lineups(on=date(2025, 6, 2), client=FakeClient(_schedule(False, 700003), None))
    )
    assert result["projected_sides"] == 2
    rows = lineups_for_game(700003)
    home = [r for r in rows if r["side"] == "home"]
    assert [r["batting_order"] for r in home] == list(range(1, 10))
    assert all(r["status"] == "projected" for r in home)
    assert all("projected:" in r["source"] for r in home)


def test_no_history_means_unavailable_not_invented(sqlite_db):
    from app.services.lineups import lineups_for_game, sync_lineups

    result = asyncio.run(
        sync_lineups(on=date(2025, 6, 5), client=FakeClient(_schedule(False, 700009), None))
    )
    assert result["unavailable_sides"] == 2
    assert result["lineup_slots_stored"] == 0
    assert lineups_for_game(700009) == []


def test_expected_pa_withheld_below_sample_threshold(sqlite_db):
    from app.services.lineups import expected_pa_by_slot, sync_lineups

    asyncio.run(sync_lineups(on=date(2025, 6, 1), client=FakeClient(_schedule(True))))
    with sqlite_db.session_scope() as s:
        slots = expected_pa_by_slot(s)
    assert all(v["expected_pa"] is None for v in slots.values())
    assert all("insufficient sample" in (v["note"] or "") for v in slots.values())


def test_expected_pa_measured_from_statcast_at_bats(sqlite_db):
    from app.database import models
    from app.services.lineups import MIN_PA_SAMPLES, expected_pa_by_slot

    with sqlite_db.session_scope() as s:
        uid = 0
        for g in range(1, MIN_PA_SAMPLES + 1):
            s.add(
                models.GameLineup(
                    game_id=g,
                    game_date=date(2025, 5, 1),
                    side="home",
                    team_abbreviation="NYY",
                    player_id=500 + g,
                    batting_order=1,
                    is_starter=True,
                    status="confirmed",
                    source="test",
                )
            )
            for ab in range(5):  # exactly 5 plate appearances per leadoff hitter
                uid += 1
                s.add(
                    models.StatcastPitch(
                        pitch_uid=f"u{uid}",
                        game_id=g,
                        game_date=date(2025, 5, 1),
                        at_bat_number=ab,
                        pitch_number=1,
                        pitcher_id=9,
                        batter_id=500 + g,
                    )
                )
    with sqlite_db.session_scope() as s:
        slots = expected_pa_by_slot(s)
    assert slots[1]["expected_pa"] == pytest.approx(5.0)
    assert slots[1]["samples"] >= MIN_PA_SAMPLES
    assert slots[2]["expected_pa"] is None  # no data for slot 2 — never extrapolated


def test_prediction_detail_exposes_lineup_slot_feature(sqlite_db):
    from app.database import models
    from app.services.lineups import sync_lineups
    from app.services.prediction_detail import build_prediction_detail

    with sqlite_db.session_scope() as s:
        s.add(models.Player(id=1001, full_name="Home Hitter 1", team_abbreviation="NYY", bats="R"))
        s.add(
            models.Game(
                game_id=700001,
                game_date=date.today(),
                game_datetime=datetime.now(timezone.utc),
                home_team="NYY",
                away_team="BOS",
                venue="Yankee Stadium",
                status="Scheduled",
            )
        )
    asyncio.run(sync_lineups(on=date.today(), client=FakeClient(_schedule(True))))

    with sqlite_db.session_scope() as s:
        detail = build_prediction_detail(s, 1001)
    assert detail is not None
    assert detail.lineup is not None
    assert detail.lineup.batting_order == 1
    assert detail.lineup.status == "confirmed"
    assert detail.model_features.values["lineup_slot"] == 1.0
    # No PA history yet -> withheld, and no probability invented anywhere.
    assert detail.model_features.values["expected_plate_appearances"] is None
    assert detail.prediction.hr_probability is None
    assert "lineup" in detail.data_availability.available


def test_prediction_detail_reports_missing_lineup(sqlite_db):
    from app.database import models
    from app.services.prediction_detail import build_prediction_detail

    with sqlite_db.session_scope() as s:
        s.add(models.Player(id=4242, full_name="No Lineup", team_abbreviation="NYY", bats="L"))
        s.add(
            models.Game(
                game_id=700055,
                game_date=date.today(),
                game_datetime=datetime.now(timezone.utc),
                home_team="NYY",
                away_team="BOS",
                venue="Yankee Stadium",
                status="Scheduled",
            )
        )
    with sqlite_db.session_scope() as s:
        detail = build_prediction_detail(s, 4242)
    assert detail.lineup.status == "unavailable"
    assert detail.model_features.values["lineup_slot"] is None
    assert "lineup" in detail.data_availability.unavailable
