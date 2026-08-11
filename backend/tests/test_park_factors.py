"""Unit tests for park factor computation (isolated sqlite database)."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path/'pf.db'}")
    import app.database.session as session_mod

    session_mod._engine = None
    session_mod._session_factory = None
    from app.database import models  # noqa: F401

    session_mod.Base.metadata.create_all(bind=session_mod.get_engine())
    yield session_mod
    session_mod._engine = None
    session_mod._session_factory = None


def _seed(session_mod, venue_hr: int, venue_bbe: int, other_hr: int, other_bbe: int) -> None:
    from app.database import models

    with session_mod.session_scope() as s:
        s.add(models.Game(
            game_id=1, game_date=date(2025, 5, 1),
            game_datetime=datetime(2025, 5, 1, 23, tzinfo=timezone.utc),
            home_team="COL", away_team="SFG", venue="Coors Field", venue_id=19, status="Final",
        ))
        s.add(models.Game(
            game_id=2, game_date=date(2025, 5, 1),
            game_datetime=datetime(2025, 5, 1, 23, tzinfo=timezone.utc),
            home_team="SFG", away_team="COL", venue="Oracle Park", venue_id=2395, status="Final",
        ))
        n = 0
        for game_id, hrs, bbe in ((1, venue_hr, venue_bbe), (2, other_hr, other_bbe)):
            for i in range(bbe):
                n += 1
                s.add(models.StatcastPitch(
                    pitch_uid=f"p{n}", game_id=game_id, game_date=date(2025, 5, 1),
                    at_bat_number=i, pitch_number=1, pitcher_id=1, batter_id=2,
                    bb_type="fly_ball", stand="R" if i % 2 else "L",
                    events="home_run" if i < hrs else "field_out",
                ))


def test_factor_is_ratio_of_venue_to_league_rate(sqlite_db):
    from app.services.park_factors import compute_park_factors, list_park_factors

    _seed(sqlite_db, venue_hr=80, venue_bbe=400, other_hr=20, other_bbe=400)
    result = compute_park_factors()
    assert result["status"] == "ok"
    rows = {(r["venue_name"], r["batter_hand"]): r for r in list_park_factors(season=2025)}
    coors = rows[("Coors Field", "ALL")]
    # league rate = 100/800 = 0.125 ; coors = 80/400 = 0.20 -> 1.6
    assert coors["hr_factor"] == pytest.approx(1.6, abs=0.01)
    assert rows[("Oracle Park", "ALL")]["hr_factor"] == pytest.approx(0.4, abs=0.01)
    for hand in ("L", "R"):
        assert rows[("Coors Field", hand)]["hr_factor"] is not None


def test_small_samples_are_null_not_estimated(sqlite_db):
    from app.services.park_factors import compute_park_factors, list_park_factors

    _seed(sqlite_db, venue_hr=5, venue_bbe=20, other_hr=2, other_bbe=20)
    compute_park_factors()
    for row in list_park_factors():
        assert row["hr_factor"] is None
        assert "insufficient sample" in (row["sample_note"] or "")


def test_no_data_reports_unavailable(sqlite_db):
    from app.services.park_factors import compute_park_factors

    result = compute_park_factors()
    assert result["status"] == "unavailable"
    assert result["park_factors_stored"] == 0
