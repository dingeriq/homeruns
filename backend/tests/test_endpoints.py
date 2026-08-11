"""Endpoint integration tests: health, readiness, DB connectivity, MLB data."""
from __future__ import annotations

import re
from datetime import datetime

import httpx
import pytest

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------

def test_health_returns_200(client: httpx.Client) -> None:
    res = client.get("/health")
    assert res.status_code == 200


def test_health_response_structure(client: httpx.Client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert isinstance(body["service"], str) and body["service"]


def test_health_declares_utf8_json(client: httpx.Client) -> None:
    ctype = client.get("/health").headers.get("content-type", "").lower()
    assert "application/json" in ctype
    assert "charset=utf-8" in ctype


# --------------------------------------------------------------------------
# /ready + database connectivity
# --------------------------------------------------------------------------

def test_ready_status_code(client: httpx.Client) -> None:
    # 200 when the database is up, 503 when it is down — never a 5xx crash.
    assert client.get("/ready").status_code in (200, 503)


def test_ready_response_structure(ready_payload: dict) -> None:
    for key in (
        "status",
        "database",
        "database_url_present",
        "database_url_source",
        "database_driver",
        "database_host",
        "database_name",
        "pg_parts_present",
        "environment_present",
    ):
        assert key in ready_payload, f"missing diagnostic key: {key}"

    assert ready_payload["status"] in ("ready", "degraded")
    assert ready_payload["database"] in ("up", "down")
    assert isinstance(ready_payload["pg_parts_present"], dict)
    assert isinstance(ready_payload["environment_present"], dict)


def test_ready_never_leaks_credentials(ready_payload: dict) -> None:
    blob = str(ready_payload).lower()
    assert "://" not in blob, "a full connection URL must never be exposed"
    assert "@" not in ready_payload.get("database_host", "")
    # presence diagnostics must be booleans, never values
    assert all(isinstance(v, bool) for v in ready_payload["environment_present"].values())
    assert all(isinstance(v, bool) for v in ready_payload["pg_parts_present"].values())


def test_database_connectivity(require_db, ready_payload: dict) -> None:
    assert ready_payload["database"] == "up"
    assert ready_payload["status"] == "ready"
    assert ready_payload["database_driver"].startswith("postgresql")
    assert ready_payload.get("schema_ready") in (True, False)


# --------------------------------------------------------------------------
# /games/today
# --------------------------------------------------------------------------

def test_games_today_returns_list(client: httpx.Client) -> None:
    res = client.get("/games/today")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_games_today_structure(require_db, client: httpx.Client) -> None:
    games = client.get("/games/today").json()
    if not games:
        pytest.skip("no games scheduled today")
    for g in games:
        assert isinstance(g["game_id"], int)
        assert ISO_DATE.match(g["game_date"])
        datetime.fromisoformat(g["game_date"].replace("Z", "+00:00"))
        assert g["home_team"] and g["away_team"]
        assert isinstance(g["venue"], str)
        assert isinstance(g["status"], str)
        for k in ("home_probable_pitcher", "away_probable_pitcher"):
            assert g[k] is None or isinstance(g[k], str)


# --------------------------------------------------------------------------
# /teams
# --------------------------------------------------------------------------

def test_teams_returns_list(client: httpx.Client) -> None:
    res = client.get("/teams")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_teams_structure_and_mlb_data(require_db, client: httpx.Client) -> None:
    teams = client.get("/teams").json()
    assert len(teams) >= 30, "expected the full MLB team set"
    for t in teams:
        assert isinstance(t["id"], int)
        assert t["abbreviation"] and t["name"]
        assert isinstance(t["league"], str)
        assert isinstance(t["division"], str)

    abbrs = {t["abbreviation"] for t in teams}
    assert {"NYY", "LAD", "BOS"} <= abbrs
    assert len(abbrs) == len(teams), "duplicate team abbreviations"


# --------------------------------------------------------------------------
# /players
# --------------------------------------------------------------------------

def test_players_returns_list(client: httpx.Client) -> None:
    res = client.get("/players")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_players_structure(require_db, client: httpx.Client) -> None:
    players = client.get("/players").json()
    assert len(players) > 0, "expected MLB players from the sync"
    for p in players[:50]:
        assert isinstance(p["id"], int)
        assert isinstance(p["full_name"], str) and p["full_name"]
        assert isinstance(p["team_id"], int)
        for k in ("team_abbreviation", "position", "bats", "throws"):
            assert isinstance(p[k], str)


def test_players_limit_is_respected(require_db, client: httpx.Client) -> None:
    players = client.get("/players", params={"limit": 5}).json()
    assert len(players) <= 5


def test_players_names_are_utf8_decodable(require_db, client: httpx.Client) -> None:
    res = client.get("/players", params={"limit": 500})
    names = " ".join(p["full_name"] for p in res.json())
    # mojibake signature from a latin-1 decode of UTF-8 bytes
    assert "Ã" not in names and "Â" not in names


# --------------------------------------------------------------------------
# /predictions/today (placeholder until the model ships)
# --------------------------------------------------------------------------

def test_predictions_today_returns_list(client: httpx.Client) -> None:
    res = client.get("/predictions/today")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------

def test_unknown_route_returns_error_envelope(client: httpx.Client) -> None:
    res = client.get("/does-not-exist")
    assert res.status_code == 404
    body = res.json()
    assert "error" in body and "detail" in body


def test_wrong_method_returns_405(client: httpx.Client) -> None:
    res = client.post("/teams")
    assert res.status_code == 405


@pytest.mark.parametrize("limit", ["0", "abc", "99999"])
def test_players_invalid_limit_is_validated(client: httpx.Client, limit: str) -> None:
    res = client.get("/players", params={"limit": limit})
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "ValidationError"
    assert isinstance(body["detail"], str)
