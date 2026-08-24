"""V1 HR model pipeline: training, scoring, and honest unavailable fallbacks.

Fully isolated — in-memory SQLite, a temp artifact path, no network, no
production calls, no ingestion.
"""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import models  # noqa: E402
from app.database.session import Base  # noqa: E402
from app.services import hr_model  # noqa: E402
from app.services.feature_builder import FEATURE_NAMES  # noqa: E402
from app.services.scoring import score_player, score_slate  # noqa: E402

DAY = date(2026, 8, 15)


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
def artifact_file(tmp_path, monkeypatch):
    path = tmp_path / "hr_model_test.json"
    monkeypatch.setenv("MODEL_ARTIFACT_PATH", str(path))
    monkeypatch.setenv("MODEL_ARTIFACT_DB_PERSIST", "0")
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})
    yield path
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})


def synthetic_rows(n: int = 600) -> list[dict]:
    rng = random.Random(7)
    rows = []
    start = date(2026, 4, 1)
    for i in range(n):
        barrel = rng.uniform(0.0, 0.25)
        row = {name: None for name in FEATURE_NAMES}
        row.update(
            {
                "game_date": start + timedelta(days=i // 8),
                "game_id": 800000 + i,
                "batter_id": 1000 + (i % 40),
            }
        )
        for name in FEATURE_NAMES:
            row[name] = rng.uniform(0.0, 1.0)
        if "batter_barrel_rate_30d" in row:
            row["batter_barrel_rate_30d"] = barrel
        p = 0.02 + barrel  # signal the model can learn
        row["hit_hr"] = 1 if rng.random() < p else 0
        rows.append(row)
    # guarantee both classes
    rows[0]["hit_hr"] = 1
    rows[-1]["hit_hr"] = 0
    return rows


# ---------------------------------------------------------------------------
# Training / artifact
# ---------------------------------------------------------------------------

def test_training_produces_artifact(artifact_file):
    artifact = hr_model.train_model(synthetic_rows(), min_rows=200)
    assert artifact["model_version"] == hr_model.MODEL_VERSION
    assert len(artifact["coefficients"]) == len(FEATURE_NAMES)
    saved = hr_model.save_artifact(artifact)
    assert Path(saved).exists()


def test_load_and_predict_probability_in_range(artifact_file):
    hr_model.save_artifact(hr_model.train_model(synthetic_rows(), min_rows=200))
    loaded = hr_model.load_artifact()
    assert loaded is not None
    scored = hr_model.predict(loaded, {name: 0.5 for name in FEATURE_NAMES})
    assert 0.0 < scored["hr_probability"] < 1.0
    assert 0.0 <= scored["confidence"] <= 1.0
    assert scored["features_used"] == len(FEATURE_NAMES)


def test_predict_with_missing_features_reports_imputation(artifact_file):
    hr_model.save_artifact(hr_model.train_model(synthetic_rows(), min_rows=200))
    loaded = hr_model.load_artifact()
    scored = hr_model.predict(loaded, {})
    assert 0.0 <= scored["hr_probability"] <= 1.0
    assert scored["features_used"] == 0
    assert scored["confidence"] == 0.0


def test_training_rejects_tiny_corpus(artifact_file):
    with pytest.raises(ValueError):
        hr_model.train_model(synthetic_rows(20), min_rows=200)


def test_model_status_unavailable_without_artifact(artifact_file):
    status = hr_model.model_status()
    assert status["status"] == "unavailable"
    assert "model_not_available" in status["reason"]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _lineup(session, player_id: int, game_id: int = 700001):
    session.add(
        models.GameLineup(
            game_id=game_id,
            game_date=DAY,
            side="home",
            player_id=player_id,
            player_name=f"Player {player_id}",
            team_abbreviation="NYY",
            batting_order=1,
            is_starter=True,
        )
    )
    session.flush()


def test_score_slate_no_model_is_unavailable(session, artifact_file):
    _lineup(session, 1001)
    result = score_slate(session, DAY)
    assert result["status"] == "unavailable"
    assert result["reason"].startswith("model_not_available")
    assert result["predictions"] == []


def test_score_slate_no_lineup_is_unavailable(session, artifact_file):
    hr_model.save_artifact(hr_model.train_model(synthetic_rows(), min_rows=200))
    result = score_slate(session, DAY)
    assert result["status"] == "unavailable"
    assert result["reason"].startswith("no_lineups")
    assert result["predictions"] == []


def test_score_slate_with_model_and_lineup(session, artifact_file):
    hr_model.save_artifact(hr_model.train_model(synthetic_rows(), min_rows=200))
    _lineup(session, 1001)
    _lineup(session, 1002)
    result = score_slate(session, DAY)
    assert result["status"] == "ok"
    assert len(result["predictions"]) == 2
    probs = [p["hr_probability"] for p in result["predictions"]]
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert probs == sorted(probs, reverse=True)
    assert result["model_version"] == hr_model.MODEL_VERSION


def test_score_player_ok_and_fallback(session, artifact_file):
    assert score_player(session, 1001, game_date=DAY)["status"] == "unavailable"
    hr_model.save_artifact(hr_model.train_model(synthetic_rows(), min_rows=200))
    scored = score_player(session, 1001, game_date=DAY)
    assert scored["status"] == "ok"
    assert 0.0 <= scored["hr_probability"] <= 1.0
    assert scored["model_version"] == hr_model.MODEL_VERSION


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def test_predictions_today_response_shape(client, require_db):
    resp = client.get("/predictions/today")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    for item in body:
        assert set(
            ["player_id", "player_name", "game_id", "hr_probability", "confidence"]
        ).issubset(item.keys())
        assert 0.0 <= item["hr_probability"] <= 1.0


def test_model_status_endpoint(client):
    resp = client.get("/admin/model-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "model" in body
    assert body["model"]["status"] in {"ok", "unavailable"}


def test_snapshot_building_is_not_triggered_on_startup(client, monkeypatch):
    """Startup / scheduler must never build snapshots or train."""
    from app.services import scheduler

    src = Path(scheduler.__file__).read_text(encoding="utf-8")
    assert "build_snapshots" not in src
    assert "train_model" not in src

    # And the endpoint exists but only responds to an explicit POST.
    routes = {r.path for r in client.app.routes} if hasattr(client, "app") else set()
    if routes:
        assert "/admin/build-snapshots" in routes
        assert "/admin/train-model" in routes
