"""Tests for the deep Statcast coverage audit (GET /admin/statcast-audit)."""
from __future__ import annotations

import httpx
import pytest

from app.services.statcast_audit import (
    MIN_HR_FOR_TRAINING,
    PARTIAL_THRESHOLD,
    SUFFICIENT_THRESHOLD,
    _label,
    _pct,
)


def test_pct_handles_zero_and_bad_input() -> None:
    assert _pct(1, 0) is None
    assert _pct(None, None) is None
    assert _pct(1, 4) == 0.25
    assert _pct("x", 4) is None


def test_label_thresholds() -> None:
    assert _label(None) == "no_data"
    assert _label(SUFFICIENT_THRESHOLD) == "sufficient"
    assert _label(PARTIAL_THRESHOLD) == "partial"
    assert _label(PARTIAL_THRESHOLD - 0.01) == "insufficient"


def test_min_label_threshold_is_explicit() -> None:
    assert MIN_HR_FOR_TRAINING > 0


@pytest.fixture(scope="module")
def audit(client: httpx.Client, db_up: bool) -> dict:
    if not db_up:
        pytest.skip("database unreachable — skipping data-dependent test")
    res = client.get("/admin/statcast-audit")
    assert res.status_code == 200, res.text
    return res.json()


def test_audit_shape(require_db, audit: dict) -> None:
    assert audit["status"] in ("empty", "populated")
    assert "generated_at" in audit
    assert "training_readiness" in audit
    assert isinstance(audit["training_readiness"]["ready"], bool)


def test_empty_audit_is_honest(require_db, audit: dict) -> None:
    if audit["status"] != "empty":
        pytest.skip("statcast populated")
    assert audit["totals"]["total_pitches"] == 0
    assert audit["training_readiness"]["ready"] is False
    assert audit["training_readiness"]["reasons"]


def test_populated_audit_internally_consistent(require_db, audit: dict) -> None:
    if audit["status"] != "populated":
        pytest.skip("statcast empty")
    t = audit["totals"]
    assert t["total_pitches"] > 0
    assert t["first_date"] <= t["last_date"]
    assert sum(r["pitches"] for r in audit["by_season"]) == t["total_pitches"]
    assert sum(r["pitches"] for r in audit["by_month"]) == t["total_pitches"]
    assert sum(r["pitches"] for r in audit["pitch_types"]) == t["total_pitches"]
    for side in ("batter", "pitcher"):
        assert sum(r["pitches"] for r in audit["handedness"][side]) == t["total_pitches"]
    assert t["home_runs"] <= t["plate_appearance_results"] <= t["total_pitches"]


def test_coverage_values_are_ratios(require_db, audit: dict) -> None:
    if audit["status"] != "populated":
        pytest.skip("statcast empty")
    for block in ("pitch_level", "contact_level_of_batted_balls", "composite"):
        for field in audit["coverage"][block].values():
            cov = field["coverage"]
            assert cov is None or 0.0 <= cov <= 1.0
            assert field["status"] in ("sufficient", "partial", "insufficient", "no_data")


def test_identity_linkage_reported(require_db, audit: dict) -> None:
    if audit["status"] != "populated":
        pytest.skip("statcast empty")
    link = audit["identity_linkage"]
    for key in ("batter_link_rate", "pitcher_link_rate"):
        assert link[key] is None or 0.0 <= link[key] <= 1.0
    assert isinstance(link["unmatched_batter_sample"], list)


def test_audit_leaks_no_credentials(require_db, client: httpx.Client) -> None:
    body = client.get("/admin/statcast-audit").text.lower()
    for needle in ("password", "postgresql://", "postgres://", "pgpassword", "api_key"):
        assert needle not in body


# --- date-range filtering -------------------------------------------------


def test_audit_rejects_inverted_range(client: httpx.Client) -> None:
    res = client.get("/admin/statcast-audit", params={"start": "2024-06-01", "end": "2024-05-01"})
    assert res.status_code == 400


def test_audit_accepts_date_range(client: httpx.Client, db_up: bool) -> None:
    if not db_up:
        pytest.skip("database unreachable — skipping data-dependent test")
    res = client.get("/admin/statcast-audit", params={"start": "2024-04-01", "end": "2024-04-30"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["date_range"] == {"start": "2024-04-01", "end": "2024-04-30", "scope": "range"}
    if body["status"] == "populated":
        assert body["totals"]["first_date"] >= "2024-04-01"
        assert body["totals"]["last_date"] <= "2024-04-30"


def test_full_audit_reports_scope_all(client: httpx.Client, db_up: bool) -> None:
    if not db_up:
        pytest.skip("database unreachable — skipping data-dependent test")
    body = client.get("/admin/statcast-audit").json()
    assert body["date_range"]["scope"] == "all"
