"""Integration tests for GET /predictions/{player_id} (prediction detail)."""
from __future__ import annotations

import httpx
import pytest

SECTIONS = (
    "player",
    "game",
    "pitcher",
    "matchup",
    "hitter_metrics",
    "pitcher_metrics",
    "pitch_mix",
    "statcast_metrics",
    "recent_performance",
    "park_factors",
    "weather",
    "model_features",
    "prediction",
    "explanation",
)


@pytest.fixture(scope="module")
def sample_player_id(client: httpx.Client) -> int:
    res = client.get("/players", params={"limit": 1})
    if res.status_code != 200 or not res.json():
        pytest.skip("no players available")
    return res.json()[0]["id"]


@pytest.fixture(scope="module")
def detail(client: httpx.Client, sample_player_id: int) -> dict:
    res = client.get(f"/predictions/{sample_player_id}")
    assert res.status_code == 200, res.text
    return res.json()


def test_detail_returns_200(require_db, detail: dict) -> None:
    assert isinstance(detail, dict)


def test_detail_contains_all_sections(require_db, detail: dict) -> None:
    for section in SECTIONS:
        assert section in detail, f"missing section: {section}"
    assert "data_availability" in detail


def test_player_section_structure(require_db, detail: dict, sample_player_id: int) -> None:
    player = detail["player"]
    assert player["id"] == sample_player_id
    assert isinstance(player["full_name"], str) and player["full_name"]


def test_game_section_is_null_or_structured(require_db, detail: dict) -> None:
    game = detail["game"]
    if game is None:
        return
    assert isinstance(game["game_id"], int)
    assert game["home_away"] in ("home", "away")
    assert game["opponent_team"] in (game["home_team"], game["away_team"])


def test_no_fabricated_probability(require_db, detail: dict) -> None:
    pred = detail["prediction"]
    assert pred["status"] == "unavailable"
    assert pred["hr_probability"] is None
    assert pred["confidence"] is None
    assert pred["model_version"] is None
    assert isinstance(pred["reason"], str) and pred["reason"]


def test_no_fabricated_explanation(require_db, detail: dict) -> None:
    exp = detail["explanation"]
    assert exp["status"] == "unavailable"
    assert exp["factors"] == []
    for factor in exp["factors"]:
        assert factor.get("contribution") is None


def test_model_features_are_named_and_nullable(require_db, detail: dict) -> None:
    features = detail["model_features"]
    assert isinstance(features["values"], dict) and features["values"]
    assert isinstance(features["missing"], list)
    for key, value in features["values"].items():
        assert isinstance(key, str)
        assert value is None or isinstance(value, (int, float))
    for key in features["missing"]:
        assert features["values"][key] is None


def test_unavailable_sections_are_reported(require_db, detail: dict) -> None:
    availability = detail["data_availability"]
    assert isinstance(availability["available"], list)
    assert isinstance(availability["unavailable"], list)
    assert set(availability["available"]).isdisjoint(availability["unavailable"])
    # park factors, weather, prediction and explanation have no data source yet
    for section in ("park_factors", "weather", "prediction", "explanation"):
        assert section in availability["unavailable"]
        assert section in availability["notes"]


def test_park_and_weather_values_are_null(require_db, detail: dict) -> None:
    park = detail["park_factors"]
    assert park["hr_factor"] is None
    assert park["hr_factor_lhb"] is None
    assert park["hr_factor_rhb"] is None
    weather = detail["weather"]
    assert all(weather[k] is None for k in ("temperature_f", "wind_speed_mph", "conditions"))


def test_statcast_sections_are_null_or_numeric(require_db, detail: dict) -> None:
    hm = detail["hitter_metrics"]
    if hm is not None:
        assert isinstance(hm["batted_balls"], int)
        for k in ("barrel_rate", "hard_hit_rate"):
            assert hm[k] is None or 0.0 <= hm[k] <= 1.0
    mix = detail["pitch_mix"]
    if mix is not None:
        assert sum(p["count"] for p in mix["pitches"]) == mix["total_pitches"]


def test_unknown_player_returns_404(client: httpx.Client) -> None:
    res = client.get("/predictions/999999999")
    assert res.status_code == 404
    body = res.json()
    assert "error" in body and "detail" in body


@pytest.mark.parametrize("bad", ["abc", "0", "-5"])
def test_invalid_player_id_is_validated(client: httpx.Client, bad: str) -> None:
    res = client.get(f"/predictions/{bad}")
    assert res.status_code == 422
    assert res.json()["error"] == "ValidationError"


def test_predictions_today_still_works(client: httpx.Client) -> None:
    res = client.get("/predictions/today")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
