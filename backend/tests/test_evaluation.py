"""Post-game resolution + V1 performance metrics.

Isolated: in-memory SQLite, no network, no ingestion, no training. The model,
its features and the stored probabilities are never touched here.
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import models  # noqa: E402
from app.database.session import Base  # noqa: E402
from app.services.evaluation import (  # noqa: E402
    _auc,
    _brier,
    _calibration_deciles,
    _log_loss,
    _top_n_rate,
    performance,
    resolve_slate,
)

DAY = date(2026, 8, 29)


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


def add_prediction(session, *, game_id, player_id, prob, day=DAY, version="v1-logreg-platt"):
    row = models.DailyPrediction(
        game_date=day,
        game_id=game_id,
        player_id=player_id,
        player_name=f"P{player_id}",
        hr_probability=prob,
        model_version=version,
        feature_set_version="v1",
    )
    session.add(row)
    session.flush()
    return row


def add_pitch(session, *, game_id, batter_id, event, uid, day=DAY):
    session.add(
        models.StatcastPitch(
            pitch_uid=uid,
            game_id=game_id,
            game_date=day,
            at_bat_number=1,
            pitch_number=1,
            pitcher_id=900000,
            batter_id=batter_id,
            events=event,
        )
    )
    session.flush()


# ---------------------------------------------------------------- resolution
def test_home_run_resolves_to_one(session):
    add_prediction(session, game_id=1, player_id=11, prob=0.12)
    add_pitch(session, game_id=1, batter_id=11, event="home_run", uid="a1")

    result = resolve_slate(session, DAY)

    assert result["resolved"] == 1
    assert result["home_runs"] == 1
    row = session.query(models.DailyPrediction).one()
    assert row.actual_hr == 1
    assert row.resolution_source == "statcast"
    assert row.resolved_at is not None


def test_non_home_run_resolves_to_zero(session):
    add_prediction(session, game_id=1, player_id=11, prob=0.12)
    add_pitch(session, game_id=1, batter_id=11, event="strikeout", uid="a1")

    resolve_slate(session, DAY)

    row = session.query(models.DailyPrediction).one()
    assert row.actual_hr == 0
    assert row.resolution_source == "statcast"


def test_missing_statcast_game_stays_null(session):
    add_prediction(session, game_id=42, player_id=11, prob=0.12)

    result = resolve_slate(session, DAY)

    assert result["resolved"] == 0
    assert result["unresolved"] == 1
    assert result["status"] == "awaiting_statcast"
    row = session.query(models.DailyPrediction).one()
    assert row.actual_hr is None
    assert row.resolved_at is None
    assert row.resolution_source is None


def test_player_and_game_matching_is_exact(session):
    # Teammate homered, our hitter did not; different game also has a HR.
    add_prediction(session, game_id=1, player_id=11, prob=0.10)
    add_pitch(session, game_id=1, batter_id=99, event="home_run", uid="a1")
    add_pitch(session, game_id=1, batter_id=11, event="single", uid="a2")
    add_pitch(session, game_id=2, batter_id=11, event="home_run", uid="a3")

    resolve_slate(session, DAY)

    row = session.query(models.DailyPrediction).filter_by(game_id=1).one()
    assert row.actual_hr == 0


def test_doubleheader_resolved_per_game_id(session):
    add_prediction(session, game_id=101, player_id=11, prob=0.10)
    add_prediction(session, game_id=102, player_id=11, prob=0.11)
    add_pitch(session, game_id=101, batter_id=11, event="home_run", uid="a1")
    add_pitch(session, game_id=102, batter_id=11, event="flyout", uid="a2")

    resolve_slate(session, DAY)

    g1 = session.query(models.DailyPrediction).filter_by(game_id=101).one()
    g2 = session.query(models.DailyPrediction).filter_by(game_id=102).one()
    assert (g1.actual_hr, g2.actual_hr) == (1, 0)


def test_resolution_is_idempotent(session):
    add_prediction(session, game_id=1, player_id=11, prob=0.12)
    add_prediction(session, game_id=1, player_id=12, prob=0.05)
    add_pitch(session, game_id=1, batter_id=11, event="home_run", uid="a1")
    add_pitch(session, game_id=1, batter_id=12, event="groundout", uid="a2")

    first = resolve_slate(session, DAY)
    labels_first = sorted(
        (r.game_id, r.player_id, r.actual_hr)
        for r in session.query(models.DailyPrediction).all()
    )
    second = resolve_slate(session, DAY)
    labels_second = sorted(
        (r.game_id, r.player_id, r.actual_hr)
        for r in session.query(models.DailyPrediction).all()
    )

    assert session.query(models.DailyPrediction).count() == 2
    assert labels_first == labels_second
    assert first["resolved"] == second["resolved"] == 2
    assert second["updated"] == 0


def test_resolve_slate_without_predictions(session):
    result = resolve_slate(session, DAY)
    assert result["status"] == "no_predictions"
    assert result["predictions"] == 0


# ------------------------------------------------------------------- metrics
def test_log_loss_matches_manual_computation():
    pairs = [(0.9, 1), (0.2, 0)]
    expected = (-math.log(0.9) - math.log(0.8)) / 2
    assert _log_loss(pairs) == pytest.approx(expected)


def test_log_loss_is_finite_at_extremes():
    assert math.isfinite(_log_loss([(0.0, 1), (1.0, 0)]))


def test_brier_score():
    pairs = [(0.8, 1), (0.3, 0)]
    assert _brier(pairs) == pytest.approx(((0.2) ** 2 + (0.3) ** 2) / 2)


def test_auc_perfect_and_undefined():
    assert _auc([(0.9, 1), (0.8, 1), (0.2, 0), (0.1, 0)]) == pytest.approx(1.0)
    assert _auc([(0.9, 0), (0.8, 0), (0.2, 1), (0.1, 1)]) == pytest.approx(0.0)
    # Single class -> undefined, never a fabricated 0.5
    assert _auc([(0.9, 1), (0.4, 1)]) is None
    assert _auc([]) is None


def test_auc_handles_ties():
    assert _auc([(0.5, 1), (0.5, 0)]) == pytest.approx(0.5)


def test_top_n_hit_rates():
    pairs = [(0.9, 1), (0.8, 0), (0.7, 1), (0.6, 0), (0.5, 0), (0.4, 1)]
    top5 = _top_n_rate(pairs, 5)
    assert top5["evaluated"] == 5 and top5["home_runs"] == 2
    assert top5["hit_rate"] == pytest.approx(0.4)
    # Fewer rows than n -> evaluated is the actual count, not padded
    top25 = _top_n_rate(pairs, 25)
    assert top25["evaluated"] == 6 and top25["hit_rate"] == pytest.approx(0.5)
    top10 = _top_n_rate(pairs, 10)
    assert top10["evaluated"] == 6
    assert _top_n_rate([], 5)["hit_rate"] is None


def test_calibration_deciles():
    pairs = [(0.05, 0), (0.05, 1), (0.95, 1)]
    bins = _calibration_deciles(pairs)
    assert len(bins) == 10
    first = bins[0]
    assert first["count"] == 2 and first["observed_rate"] == pytest.approx(0.5)
    last = bins[9]
    assert last["count"] == 1 and last["observed_rate"] == pytest.approx(1.0)
    assert bins[5]["count"] == 0 and bins[5]["observed_rate"] is None


def test_performance_uses_resolved_rows_only(session):
    add_prediction(session, game_id=1, player_id=11, prob=0.30)
    add_prediction(session, game_id=1, player_id=12, prob=0.05)
    add_prediction(session, game_id=7, player_id=13, prob=0.20)  # no statcast
    add_pitch(session, game_id=1, batter_id=11, event="home_run", uid="a1")
    add_pitch(session, game_id=1, batter_id=12, event="lineout", uid="a2")

    resolve_slate(session, DAY)
    report = performance(session, DAY, DAY)

    assert report["status"] == "ok"
    assert report["resolved_predictions"] == 2
    assert report["unresolved_predictions"] == 1
    m = report["metrics"]
    assert m["home_runs"] == 1
    assert m["base_rate"] == pytest.approx(0.5)
    assert m["auc"] == pytest.approx(1.0)
    assert m["top_5"]["evaluated"] == 2
    assert len(m["calibration_deciles"]) == 10


def test_performance_with_no_resolved_rows(session):
    add_prediction(session, game_id=7, player_id=13, prob=0.20)
    report = performance(session, DAY, DAY)
    assert report["status"] == "no_resolved_predictions"
    assert report["metrics"] is None
    assert report["unresolved_predictions"] == 1


def test_performance_single_class_reports_undefined_auc(session):
    add_prediction(session, game_id=1, player_id=11, prob=0.30)
    add_pitch(session, game_id=1, batter_id=11, event="flyout", uid="a1")
    resolve_slate(session, DAY)

    m = performance(session, DAY, DAY)["metrics"]
    assert m["auc"] is None
    assert "one outcome class" in m["auc_note"]
    assert m["log_loss"] is not None and m["brier_score"] is not None
