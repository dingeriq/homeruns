"""Durable model artifact + persisted daily predictions.

Isolated: in-memory SQLite, temp artifact path, no network, no ingestion, no
training against production. The model itself is untouched — these tests only
exercise persistence and the read path.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import models  # noqa: E402
from app.database.session import Base  # noqa: E402
from app.services import hr_model  # noqa: E402
from app.services.feature_builder import FEATURE_NAMES  # noqa: E402
from app.services.scoring import (  # noqa: E402
    prediction_status,
    store_slate_predictions,
    stored_predictions,
)
from tests.test_hr_model_pipeline import synthetic_rows  # noqa: E402

DAY = date(2026, 8, 23)


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


@pytest.fixture()
def trained(artifact_file):
    artifact = hr_model.train_model(synthetic_rows(), min_rows=200)
    hr_model.save_artifact(artifact)
    return artifact


def _lineup(session, player_id: int, game_id: int = 900001):
    session.add(
        models.GameLineup(
            game_id=game_id,
            game_date=DAY,
            side="home",
            player_id=player_id,
            player_name=f"Player {player_id}",
            team_abbreviation="NYY",
            batting_order=3,
            is_starter=True,
        )
    )
    # The opposing side is posted too, so the game counts as officially confirmed.
    if not session.query(models.GameLineup).filter(
        models.GameLineup.game_id == game_id,
        models.GameLineup.side == "away",
    ).first():
        session.add(
            models.GameLineup(
                game_id=game_id,
                game_date=DAY,
                side="away",
                player_id=game_id,
                player_name="Opposing starter",
                team_abbreviation="BOS",
                is_starter=False,
                status="confirmed",
            )
        )
    session.flush()


# ---------------------------------------------------------------------------
# 1-3. Artifact persistence in the database
# ---------------------------------------------------------------------------

def test_artifact_saves_to_database(session, trained):
    result = hr_model.save_artifact_to_db(trained, session)
    assert result["model_version"] == hr_model.MODEL_VERSION
    assert result["is_active"] is True
    rows = list(session.query(models.ModelArtifact))
    assert len(rows) == 1
    assert rows[0].is_active is True
    assert rows[0].payload["coefficients"] == trained["coefficients"]


def test_only_one_artifact_active(session, trained):
    hr_model.save_artifact_to_db(trained, session)
    other = dict(trained)
    other["model_version"] = "v1-logreg-platt-shadow"
    hr_model.save_artifact_to_db(other, session)
    active = [r.model_version for r in session.query(models.ModelArtifact) if r.is_active]
    assert active == ["v1-logreg-platt-shadow"]


def test_loads_from_db_when_local_artifact_missing(session, trained, artifact_file, monkeypatch):
    hr_model.save_artifact_to_db(trained, session)
    artifact_file.unlink()
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})
    monkeypatch.setattr(hr_model, "load_artifact_from_db", lambda: hr_model._load_artifact_from_db(session))
    loaded = hr_model.load_artifact()
    assert loaded is not None
    assert loaded["model_version"] == trained["model_version"]


def test_db_artifact_scores_identically_to_json(session, trained, artifact_file, monkeypatch):
    features = {name: 0.4 for name in FEATURE_NAMES}
    from_json = hr_model.predict(hr_model.load_artifact(), features)

    hr_model.save_artifact_to_db(trained, session)
    artifact_file.unlink()
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})
    monkeypatch.setattr(hr_model, "load_artifact_from_db", lambda: hr_model._load_artifact_from_db(session))
    from_db = hr_model.predict(hr_model.load_artifact(), features)

    assert from_db["hr_probability"] == from_json["hr_probability"]
    assert from_db["confidence"] == from_json["confidence"]
    assert from_db["model_version"] == from_json["model_version"]


def test_corrupt_local_artifact_falls_back_to_db(session, trained, artifact_file, monkeypatch):
    hr_model.save_artifact_to_db(trained, session)
    artifact_file.write_text("{not json", encoding="utf-8")
    hr_model._CACHE.update({"path": None, "artifact": None, "mtime": None})
    monkeypatch.setattr(hr_model, "load_artifact_from_db", lambda: hr_model._load_artifact_from_db(session))
    assert hr_model.load_artifact()["model_version"] == trained["model_version"]


# ---------------------------------------------------------------------------
# 4-6. Daily prediction persistence
# ---------------------------------------------------------------------------

def test_score_slate_persists_predictions(session, trained):
    _lineup(session, 1001)
    _lineup(session, 1002)
    result = store_slate_predictions(session, DAY)
    assert result["status"] == "ok"
    assert result["inserted"] == 2
    assert result["updated"] == 0
    rows = list(session.query(models.DailyPrediction))
    assert len(rows) == 2
    assert all(r.model_version == hr_model.MODEL_VERSION for r in rows)
    assert all(r.feature_set_version == "v1" for r in rows)
    assert all(r.features_total == len(FEATURE_NAMES) for r in rows)


def test_rescoring_updates_in_place(session, trained):
    _lineup(session, 1001)
    store_slate_predictions(session, DAY)
    again = store_slate_predictions(session, DAY)
    assert again["inserted"] == 0
    assert again["updated"] == 1
    assert session.query(models.DailyPrediction).count() == 1


def test_score_slate_without_model_persists_nothing(session, artifact_file):
    _lineup(session, 1001)
    result = store_slate_predictions(session, DAY)
    assert result["status"] == "unavailable"
    assert result["predictions_scored"] == 0
    assert session.query(models.DailyPrediction).count() == 0


def test_prediction_status_reports_inventory(session, trained):
    _lineup(session, 1001)
    _lineup(session, 1002, game_id=900002)
    store_slate_predictions(session, DAY)
    status = prediction_status(session, DAY)
    assert status["predictions_stored"] == 2
    assert status["unique_players"] == 2
    assert status["unique_games"] == 2
    assert status["model_version"] == hr_model.MODEL_VERSION
    assert status["game_date"] == str(DAY)
    assert status["earliest_prediction"] and status["latest_prediction"]


def test_prediction_status_empty_table(session, artifact_file):
    status = prediction_status(session)
    assert status["predictions_stored"] == 0
    assert status["unique_players"] == 0
    assert status["model_version"] is None


# ---------------------------------------------------------------------------
# 7-8. Read path used by /predictions/today
# ---------------------------------------------------------------------------

def test_stored_predictions_ranked_desc(session, trained):
    for pid in (1001, 1002, 1003):
        _lineup(session, pid)
    store_slate_predictions(session, DAY)
    result = stored_predictions(session, DAY)
    assert result["status"] == "ok"
    probs = [p["hr_probability"] for p in result["predictions"]]
    assert probs == sorted(probs, reverse=True)
    assert result["model_version"] == hr_model.MODEL_VERSION


def test_stored_predictions_unavailable_when_empty(session, artifact_file):
    result = stored_predictions(session, DAY)
    assert result["status"] == "unavailable"
    assert result["reason"].startswith("no_stored_predictions")
    assert result["predictions"] == []


def test_stored_predictions_respects_limit(session, trained):
    for pid in (1001, 1002, 1003):
        _lineup(session, pid)
    store_slate_predictions(session, DAY)
    assert len(stored_predictions(session, DAY, limit=2)["predictions"]) == 2


def test_predictions_today_endpoint_shape(client, require_db):
    resp = client.get("/predictions/today")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    for item in body:
        assert 0.0 <= item["hr_probability"] <= 1.0


def test_prediction_status_endpoint(client, require_db):
    resp = client.get("/admin/prediction-status")
    assert resp.status_code in {200, 503}
