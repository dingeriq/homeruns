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


PREGAME_STATUS = {"abstractGameState": "Preview", "detailedState": "Pre-Game"}
LIVE_STATUS = {"abstractGameState": "Live", "detailedState": "In Progress"}
FINAL_STATUS = {"abstractGameState": "Final", "detailedState": "Final"}


def _seq(base: int, n: int = 9):
    return [base + i for i in range(1, n + 1)]


def _people(ids, prefix="Hitter", position="OF"):
    return [
        {"id": pid, "fullName": f"{prefix} {pid}", "primaryPosition": {"abbreviation": position}}
        for pid in ids
    ]


def _schedule(
    with_lineups: bool,
    game_id: int = 700001,
    status=None,
    home_ids=None,
    away_ids=None,
):
    game = {
        "gamePk": game_id,
        "status": dict(status or PREGAME_STATUS),
        "teams": {
            "home": {"team": {"id": 147, "abbreviation": "NYY"}},
            "away": {"team": {"id": 111, "abbreviation": "BOS"}},
        },
    }
    if with_lineups:
        game["lineups"] = {
            "homePlayers": _people(_seq(1000) if home_ids is None else home_ids, "Home Hitter", "OF"),
            "awayPlayers": _people(_seq(2000) if away_ids is None else away_ids, "Away Hitter", "1B"),
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
                "battingOrder": _seq(3000),
                "players": {
                    f"ID{pid}": {
                        "person": {"id": pid, "fullName": f"Box {pid}"},
                        "position": {"abbreviation": "SS"},
                    }
                    for pid in _seq(3000)
                },
            },
            "away": {"battingOrder": [], "players": {}},
        }
    }
    asyncio.run(
        sync_lineups(on=date(2025, 6, 2), client=FakeClient(_schedule(False, 700002), box))
    )
    rows = lineups_for_game(700002)
    assert [r["player_id"] for r in rows if r["side"] == "home"] == _seq(3000)
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


# ---------------------------------------------------------------------------
# Lineup-ingestion hardening regressions
# ---------------------------------------------------------------------------


def _malformed_schedule(game_id: int = 700101):
    """Truthy schedule lineups whose entries carry no usable person id."""
    return {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": game_id,
                        "status": dict(PREGAME_STATUS),
                        "teams": {
                            "home": {"team": {"id": 147, "abbreviation": "NYY"}},
                            "away": {"team": {"id": 111, "abbreviation": "BOS"}},
                        },
                        "lineups": {
                            "homePlayers": [{"fullName": "Placeholder"}, {"id": None}],
                            "awayPlayers": [{"fullName": "Placeholder"}],
                        },
                    }
                ]
            }
        ]
    }


def _boxscore(home_ids, away_ids=()):
    def team(ids):
        return {
            "battingOrder": list(ids),
            "players": {
                f"ID{pid}": {
                    "person": {"id": pid, "fullName": f"Box {pid}"},
                    "position": {"abbreviation": "SS"},
                }
                for pid in ids
            },
        }

    return {"teams": {"home": team(home_ids), "away": team(away_ids)}}


def test_absent_schedule_lineup_uses_boxscore_fallback(sqlite_db):
    """A — no schedule lineup at all falls back to the boxscore."""
    from app.services.lineups import lineups_for_game, sync_lineups

    result = asyncio.run(
        sync_lineups(
            on=date(2025, 6, 10),
            client=FakeClient(_schedule(False, 700110), _boxscore(_seq(8000))),
        )
    )
    assert result["confirmed_sides"] == 1  # away has no batting order
    home = [r for r in lineups_for_game(700110) if r["side"] == "home"]
    assert [r["player_id"] for r in home] == _seq(8000)
    assert all(r["status"] == "confirmed" for r in home)
    assert all(r["source"] == "mlb_stats_api:boxscore.battingOrder" for r in home)


def test_malformed_truthy_schedule_lineup_uses_boxscore_fallback(sqlite_db):
    """B — truthy but unusable schedule lineup must not block the fallback."""
    from app.services.lineups import lineups_for_game, sync_lineups

    result = asyncio.run(
        sync_lineups(
            on=date(2025, 6, 11),
            client=FakeClient(_malformed_schedule(700111), _boxscore(_seq(8100), _seq(8200))),
        )
    )
    assert result["confirmed_sides"] == 2
    rows = lineups_for_game(700111)
    assert [r["player_id"] for r in rows if r["side"] == "home"] == _seq(8100)
    assert [r["player_id"] for r in rows if r["side"] == "away"] == _seq(8200)
    assert all(r["status"] == "confirmed" for r in rows)
    assert all(r["source"] == "mlb_stats_api:boxscore.battingOrder" for r in rows)


def test_zero_normalised_rows_never_delete_confirmed_lineup(sqlite_db):
    """C — a refresh that yields nothing usable leaves confirmed rows intact."""
    from app.services.lineups import lineups_for_game, sync_lineups

    asyncio.run(sync_lineups(on=date(2025, 6, 12), client=FakeClient(_schedule(True, 700112))))
    before = lineups_for_game(700112)
    assert len(before) == 18

    result = asyncio.run(
        sync_lineups(on=date(2025, 6, 12), client=FakeClient(_malformed_schedule(700112), None))
    )
    assert result["confirmed_sides"] == 0
    assert result["preserved_confirmed_sides"] == 2
    after = lineups_for_game(700112)
    assert [r["player_id"] for r in after] == [r["player_id"] for r in before]
    assert all(r["status"] == "confirmed" for r in after)


def test_boxscore_failure_does_not_replace_confirmed_with_projected(sqlite_db):
    """D — a boxscore outage must not downgrade a stored confirmed lineup."""
    from app.services.lineups import lineups_for_game, sync_lineups

    asyncio.run(sync_lineups(on=date(2025, 6, 13), client=FakeClient(_schedule(True, 700113))))
    result = asyncio.run(
        sync_lineups(on=date(2025, 6, 13), client=FakeClient(_schedule(False, 700113), None))
    )
    assert result["projected_sides"] == 0
    assert result["preserved_confirmed_sides"] == 2
    rows = lineups_for_game(700113)
    assert len(rows) == 18
    assert all(r["status"] == "confirmed" for r in rows)


def test_boxscore_failure_without_history_keeps_projected_behaviour(sqlite_db):
    """E — with no confirmed rows the projected fallback still applies."""
    from app.services.lineups import lineups_for_game, sync_lineups

    asyncio.run(sync_lineups(on=date(2025, 6, 14), client=FakeClient(_schedule(True, 700114))))
    result = asyncio.run(
        sync_lineups(on=date(2025, 6, 15), client=FakeClient(_schedule(False, 700115), None))
    )
    assert result["projected_sides"] == 2
    assert result["preserved_confirmed_sides"] == 0
    assert all(r["status"] == "projected" for r in lineups_for_game(700115))


def test_weaker_refresh_never_downgrades_confirmed_lineup(sqlite_db):
    """F — confirmed survives repeated weak refreshes."""
    from app.services.lineups import lineups_for_game, sync_lineups

    asyncio.run(sync_lineups(on=date(2025, 6, 16), client=FakeClient(_schedule(True, 700116))))
    for client in (
        FakeClient(_malformed_schedule(700116), None),
        FakeClient(_schedule(False, 700116), None),
        FakeClient(_malformed_schedule(700116), _boxscore([], [])),
    ):
        asyncio.run(sync_lineups(on=date(2025, 6, 16), client=client))
    rows = lineups_for_game(700116)
    assert len(rows) == 18
    assert all(r["status"] == "confirmed" for r in rows)
    assert all(r["source"] == "mlb_stats_api:schedule.lineups" for r in rows)


def test_sides_are_independent(sqlite_db):
    """G — one side confirming does not disturb the other side."""
    from app.services.lineups import lineups_for_game, sync_lineups

    # Home confirms from the boxscore; away has nothing anywhere.
    asyncio.run(
        sync_lineups(
            on=date(2025, 6, 17),
            client=FakeClient(_schedule(False, 700117), _boxscore(_seq(8300))),
        )
    )
    rows = lineups_for_game(700117)
    assert {r["side"] for r in rows} == {"home"} and len(rows) == 9

    # A later away-only boxscore must not touch the stored home rows.
    asyncio.run(
        sync_lineups(
            on=date(2025, 6, 17),
            client=FakeClient(_schedule(False, 700117), _boxscore([], _seq(8400))),
        )
    )
    rows = lineups_for_game(700117)
    assert [r["player_id"] for r in rows if r["side"] == "home"] == _seq(8300)
    assert [r["player_id"] for r in rows if r["side"] == "away"] == _seq(8400)
    assert all(r["status"] == "confirmed" for r in rows)


def test_doubleheader_games_persist_independently(sqlite_db):
    """H — game 2's weak refresh cannot disturb game 1's confirmed lineup."""
    from app.services.lineups import lineups_for_game, sync_lineups

    day = date(2025, 6, 18)
    game_one = _schedule(True, 700118)["dates"][0]["games"][0]
    game_two = _malformed_schedule(700119)["dates"][0]["games"][0]
    asyncio.run(
        sync_lineups(on=day, client=FakeClient({"dates": [{"games": [game_one, game_two]}]}, None))
    )
    assert len(lineups_for_game(700118)) == 18
    assert lineups_for_game(700119) == []

    # Second pass: game 2 gets a boxscore, game 1 has none — both hold their own.
    game_one_weak = _malformed_schedule(700118)["dates"][0]["games"][0]

    class PerGameClient(FakeClient):
        async def boxscore(self, game_pk):
            if game_pk != 700119:
                raise RuntimeError("boxscore not published")
            return _boxscore(_seq(8500), [8601])

    asyncio.run(
        sync_lineups(
            on=day,
            client=PerGameClient({"dates": [{"games": [game_one_weak, game_two]}]}, None),
        )
    )
    assert len(lineups_for_game(700118)) == 18
    assert all(r["status"] == "confirmed" for r in lineups_for_game(700118))
    two = lineups_for_game(700119)
    assert [r["player_id"] for r in two if r["side"] == "home"] == _seq(8500)
    assert all(r["source"] == "mlb_stats_api:boxscore.battingOrder" for r in two)


def test_store_rejects_empty_rows(sqlite_db):
    """Unit-level guard: _store() with no rows deletes nothing."""
    from app.services.lineups import _store, lineups_for_game, sync_lineups

    asyncio.run(sync_lineups(on=date(2025, 6, 19), client=FakeClient(_schedule(True, 700120))))
    with sqlite_db.session_scope() as s:
        assert _store(s, 700120, [], "home") == 0
    assert len(lineups_for_game(700120)) == 18
