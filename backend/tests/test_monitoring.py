"""Unit + integration tests for the monitoring/metrics layer.

These run fully in-process and never need a database or network, so they are
safe in CI. Endpoint-level assertions live in ``test_endpoints.py``.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import monitoring  # noqa: E402
from app.services.mlb_client import MLBStatsClient  # noqa: E402


def _metric_lines(name: str) -> list[str]:
    body = monitoring.generate_latest(monitoring.REGISTRY).decode()
    return [line for line in body.splitlines() if line.startswith(name)]


def _sample(name: str, labels: str = "") -> float:
    for line in _metric_lines(name):
        head, _, value = line.rpartition(" ")
        if head == f"{name}{labels}":
            return float(value)
    return 0.0


# --- request metrics --------------------------------------------------------


def test_record_request_counts_and_latency():
    before = _sample('dingeriq_http_requests_total{method="GET",path="/t1",status="200"}')
    monitoring.record_request("GET", "/t1", 200, 0.05)
    after = _sample('dingeriq_http_requests_total{method="GET",path="/t1",status="200"}')
    assert after == before + 1
    assert _sample('dingeriq_http_request_duration_seconds_count{method="GET",path="/t1"}') >= 1


def test_status_classes_are_bucketed():
    for status in (200, 404, 500):
        monitoring.record_request("GET", "/t2", status, 0.01)
    classes = monitoring.summary()["status_classes"]
    assert classes["2xx"] >= 1
    assert classes["4xx"] >= 1
    assert classes["5xx"] >= 1


def test_per_endpoint_summary_stats():
    monitoring.record_request("GET", "/t3", 200, 0.2)
    monitoring.record_request("GET", "/t3", 500, 0.4)
    stats = monitoring.summary()["endpoints"]["GET /t3"]
    assert stats["count"] == 2
    assert stats["errors"] == 1
    assert stats["avg_latency_ms"] == pytest.approx(300.0, rel=0.05)
    assert stats["max_latency_ms"] == pytest.approx(400.0, rel=0.05)


def test_path_normalization_collapses_ids():
    assert monitoring.normalize_path("/players/12345") == "/players/:id"
    assert monitoring.normalize_path("/players/12345", "/players/{player_id}") == (
        "/players/{player_id}"
    )


# --- database metrics -------------------------------------------------------


def test_database_failure_metrics():
    before = _sample('dingeriq_database_connection_failures_total{operation="ping"}')
    monitoring.record_database_up(False)
    assert _sample('dingeriq_database_connection_failures_total{operation="ping"}') == before + 1
    assert _sample("dingeriq_database_up") == 0.0
    assert monitoring.summary()["database"]["connection_failures"] >= 1

    monitoring.record_database_up(True)
    assert _sample("dingeriq_database_up") == 1.0
    assert _sample('dingeriq_database_connection_checks_total{outcome="success"}') >= 1


# --- MLB API metrics --------------------------------------------------------


def test_mlb_api_success_and_failure_metrics():
    monitoring.record_mlb_request("/schedule", 0.3)
    assert _sample('dingeriq_mlb_api_requests_total{endpoint="/schedule",outcome="success"}') >= 1

    before = monitoring.summary()["mlb_api"]["request_failures"]
    monitoring.record_mlb_request("/schedule", 0.3, TimeoutError("boom"))
    assert (
        _sample(
            'dingeriq_mlb_api_request_failures_total{endpoint="/schedule",error="TimeoutError"}'
        )
        >= 1
    )
    assert monitoring.summary()["mlb_api"]["request_failures"] == before + 1


def test_mlb_endpoint_label_collapses_team_ids():
    assert MLBStatsClient._endpoint_label("/teams/147/roster") == "/teams/:id/roster"
    assert MLBStatsClient._endpoint_label("/schedule") == "/schedule"


# --- sync / job metrics -----------------------------------------------------


def test_track_job_records_start_end_duration_and_success():
    with monitoring.track_job("unit_sync"):
        time.sleep(0.01)
    job = monitoring.summary()["jobs"]["unit_sync"]
    assert job["last_outcome"] == "success"
    assert job["running"] is False
    assert job["last_start_at"] <= job["last_end_at"]
    assert job["last_duration_seconds"] >= 0.01
    assert _sample('dingeriq_job_runs_total{job="unit_sync",outcome="success"}') >= 1
    assert _sample('dingeriq_sync_last_start_timestamp_seconds{job="unit_sync"}') > 0
    assert _sample('dingeriq_sync_last_end_timestamp_seconds{job="unit_sync"}') > 0
    assert _sample('dingeriq_job_last_success_timestamp_seconds{job="unit_sync"}') > 0
    assert _sample('dingeriq_sync_in_progress{job="unit_sync"}') == 0.0


def test_track_job_records_failure_and_reraises():
    with pytest.raises(ValueError):
        with monitoring.track_job("unit_sync_fail"):
            raise ValueError("secret-connection-string")
    job = monitoring.summary()["jobs"]["unit_sync_fail"]
    assert job["last_outcome"] == "failure"
    # only the exception class is retained — never the message
    assert job["last_error"] == "ValueError"
    assert "secret-connection-string" not in str(job)
    assert _sample('dingeriq_job_runs_total{job="unit_sync_fail",outcome="failure"}') >= 1


def test_entity_counts_recorded_for_games_teams_players():
    monitoring.record_synced_counts({"teams": 30, "games": 15, "players": 780})
    for entity, value in (("teams", 30), ("games", 15), ("players", 780)):
        assert _sample(f'dingeriq_sync_last_entity_count{{entity="{entity}"}}') == value
        assert _sample(f'dingeriq_records_synced_total{{entity="{entity}"}}') >= value
    counts = monitoring.summary()["sync"]["entity_counts"]
    assert counts["teams"] == 30 and counts["games"] == 15 and counts["players"] == 780


def test_record_synced_counts_ignores_non_numeric():
    monitoring.record_synced_counts({"status": "ok", "flag": True})
    assert monitoring.summary()["sync"]["entity_counts"].get("status") is None


def test_last_successful_sync_timestamp_exposed():
    with monitoring.track_job("daily_sync_probe"):
        pass
    assert monitoring.summary()["sync"]["last_success_at"] > 0


# --- scheduler / startup gauges --------------------------------------------


def test_scheduler_and_initial_sync_gauges_follow_startup_state():
    from app.state import startup_state

    startup_state.scheduler_started = True
    assert _sample("dingeriq_scheduler_up") == 1.0

    startup_state.schema_ready = True
    assert _sample("dingeriq_schema_ready") == 1.0

    for status, expected in (("pending", 0), ("running", 1), ("complete", 2), ("failed", 3)):
        startup_state.initial_sync = status
        assert _sample("dingeriq_initial_sync_status") == expected
        assert startup_state.snapshot()["initial_sync"] == status

    startup_state.initial_sync = "complete"


# --- safety -----------------------------------------------------------------


def test_metrics_never_contain_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:supersecret@db.internal:5432/app")
    body = monitoring.generate_latest(monitoring.REGISTRY).decode().lower()
    for needle in ("supersecret", "postgresql://", "password", "api_key", "token", "@db.internal"):
        assert needle not in body


def test_recording_helpers_never_raise():
    # bad input must be swallowed, never bubble into a request or job
    monitoring.record_synced_counts(None)
    monitoring.record_synced_counts(["not", "a", "dict"])
    monitoring.record_initial_sync_status("nonsense")
    monitoring.record_request("GET", "/t4", 200, 0.0)
