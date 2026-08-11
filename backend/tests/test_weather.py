"""Tests for the OpenWeather integration (pure logic + endpoint contract)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.weather_service import (
    _pick_slot,
    _slot_to_row,
    compass,
    roof_status_for,
    wind_out_component,
)


@pytest.mark.parametrize(
    "deg,expected",
    [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (45, "NE"), (359, "N"), (None, None)],
)
def test_compass(deg, expected) -> None:
    assert compass(deg) == expected


@pytest.mark.parametrize(
    "roof,expected",
    [
        ("Open", "open"),
        ("Dome", "closed"),
        ("Retractable", "retractable"),
        (None, None),
        ("mystery", None),
    ],
)
def test_roof_status(roof, expected) -> None:
    assert roof_status_for(roof) == expected


def test_wind_out_component_signs() -> None:
    # Wind from home plate (azimuth - 180) blows straight out to centre.
    assert wind_out_component(10, 255.0, 75.0) == 10.0
    # Wind from centre field blows straight in.
    assert wind_out_component(10, 75.0, 75.0) == -10.0
    # Crosswind contributes nothing out or in.
    assert abs(wind_out_component(10, 165.0, 75.0)) < 0.01


def test_wind_out_component_requires_all_inputs() -> None:
    assert wind_out_component(None, 90.0, 75.0) is None
    assert wind_out_component(10, None, 75.0) is None
    assert wind_out_component(10, 90.0, None) is None  # venue azimuth unknown


def test_pick_slot_selects_nearest_within_three_hours() -> None:
    target = datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)
    payload = {
        "list": [
            {"dt": int((target - timedelta(hours=4)).timestamp()), "id": "far"},
            {"dt": int((target + timedelta(hours=1)).timestamp()), "id": "near"},
        ]
    }
    assert _pick_slot(payload, target)["id"] == "near"


def test_pick_slot_returns_none_when_outside_window() -> None:
    target = datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)
    payload = {"list": [{"dt": int((target + timedelta(days=7)).timestamp())}]}
    assert _pick_slot(payload, target) is None


def test_slot_to_row_normalizes_payload() -> None:
    row = _slot_to_row(
        {
            "main": {"temp": 81.32, "feels_like": 84.1, "humidity": 55, "pressure": 1011},
            "wind": {"speed": 8.05, "deg": 210, "gust": 14.2},
            "clouds": {"all": 20},
            "pop": 0.15,
            "weather": [{"main": "Clear", "description": "clear sky"}],
        }
    )
    assert row["temperature_f"] == 81.3
    assert row["wind_speed_mph"] == 8.1
    assert row["wind_direction"] == "SSW"
    assert row["precipitation_prob"] == 0.15
    assert row["conditions"] == "Clear"


def test_slot_to_row_tolerates_missing_fields() -> None:
    row = _slot_to_row({})
    assert all(row[k] is None for k in ("temperature_f", "wind_speed_mph", "conditions"))


WEATHER_KEYS = (
    "game_id",
    "status",
    "temperature_f",
    "wind_speed_mph",
    "wind_direction",
    "wind_out_mph",
    "conditions",
    "roof_status",
    "source",
)


def test_weather_today_contract(client: httpx.Client) -> None:
    res = client.get("/weather/today")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    for entry in body:
        for key in WEATHER_KEYS:
            assert key in entry
        assert entry["status"] in ("available", "unavailable")
        if entry["status"] == "unavailable":
            # Never fabricate conditions for a game we have no data for.
            assert entry["temperature_f"] is None
            assert isinstance(entry["reason"], str) and entry["reason"]
        else:
            assert entry["source"].startswith("openweather")


def test_unknown_game_weather_returns_404(require_db, client: httpx.Client) -> None:
    res = client.get("/weather/game/999999999")
    assert res.status_code == 404


@pytest.mark.parametrize("bad", ["abc", "0", "-1"])
def test_invalid_game_id_is_validated(client: httpx.Client, bad: str) -> None:
    res = client.get(f"/weather/game/{bad}")
    assert res.status_code == 422


def test_weather_response_leaks_no_api_key(client: httpx.Client) -> None:
    res = client.get("/weather/today")
    assert "appid" not in res.text.lower()
    assert "e71055" not in res.text
