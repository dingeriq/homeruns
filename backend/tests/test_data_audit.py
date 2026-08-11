"""Integration tests for GET /admin/data-audit (identity + Statcast coverage)."""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture(scope="module")
def audit(client: httpx.Client, db_up: bool) -> dict:
    if not db_up:
        pytest.skip("database unreachable — skipping data-dependent test")
    res = client.get("/admin/data-audit")
    assert res.status_code == 200, res.text
    return res.json()


def test_audit_top_level_shape(require_db, audit: dict) -> None:
    for key in ("generated_at", "identity", "statcast", "summary"):
        assert key in audit


def test_identity_section(require_db, audit: dict) -> None:
    identity = audit["identity"]
    players = identity["players"]
    assert players["total"] >= 0
    assert players["distinct_ids"] == players["total"], "player MLB ids must be unique"
    assert players["missing_name"] == 0
    assert isinstance(players["duplicate_names"], list)
    assert isinstance(identity["issues"], list)
    assert identity["identity_ok"] == (not identity["issues"])


def test_probable_pitcher_identity(require_db, audit: dict) -> None:
    games = audit["identity"]["games"]
    assert games["probable_ids"] <= games["probable_names"] or games["probable_names"] == 0
    cov = games["probable_pitcher_id_coverage"]
    assert cov is None or 0.0 <= cov <= 1.0
    assert isinstance(games["unresolved_probable_pitcher_ids"], list)


def test_statcast_section(require_db, audit: dict) -> None:
    sc = audit["statcast"]
    assert sc["status"] in ("empty", "populated")
    assert isinstance(sc["pitches"], int)
    if sc["status"] == "empty":
        assert sc["pitches"] == 0
        return
    assert sc["pitches"] > 0
    assert sc["first_date"] <= sc["last_date"]
    assert sc["games"] > 0 and sc["batters"] > 0 and sc["pitchers"] > 0
    assert sum(m["pitches"] for m in sc["by_month"]) == sc["pitches"]
    for value in sc["field_completeness"].values():
        assert value is None or 0.0 <= value <= 1.0
    bb = sc["batted_balls"]
    assert bb["batted_balls"] <= sc["pitches"]
    for key in ("barrel_rate", "hard_hit_rate"):
        assert bb[key] is None or 0.0 <= bb[key] <= 1.0


def test_summary_matches_sections(require_db, audit: dict) -> None:
    summary = audit["summary"]
    assert summary["identity_ok"] == audit["identity"]["identity_ok"]
    assert summary["statcast_pitches"] == audit["statcast"]["pitches"]
    assert summary["statcast_status"] == audit["statcast"]["status"]


def test_audit_leaks_no_credentials(require_db, client: httpx.Client) -> None:
    body = client.get("/admin/data-audit").text.lower()
    for needle in ("password", "postgresql://", "postgres://", "pgpassword"):
        assert needle not in body
