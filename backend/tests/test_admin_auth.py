"""Admin shared-secret gate: every /admin endpoint requires X-Admin-Key."""
from __future__ import annotations

import os

import httpx
import pytest

from tests.conftest import ADMIN_KEY

# /admin/model-status is read-only and never touches the database, the Odds
# API, or any external service, so it is a safe probe for the auth gate.
PROBE = "/admin/model-status"


def test_missing_key_is_rejected(client: httpx.Client) -> None:
    res = client.get(PROBE, headers={"X-Admin-Key": ""})
    assert res.status_code == 401


def test_incorrect_key_is_rejected(client: httpx.Client) -> None:
    res = client.get(PROBE, headers={"X-Admin-Key": "definitely-wrong-key"})
    assert res.status_code == 401


def test_correct_key_reaches_endpoint(client: httpx.Client) -> None:
    res = client.get(PROBE, headers={"X-Admin-Key": ADMIN_KEY})
    assert res.status_code == 200
    assert "model" in res.json()


def test_gate_applies_to_all_admin_routes(client: httpx.Client) -> None:
    for path in (
        "/admin/data-audit",
        "/admin/db-usage",
        "/admin/prediction-status",
        "/admin/model-performance",
    ):
        res = client.get(path, headers={"X-Admin-Key": ""})
        assert res.status_code == 401, path


@pytest.mark.skipif(
    os.environ.get("API_BASE_URL") is not None,
    reason="env mutation only applies to the in-process app",
)
def test_unconfigured_server_key_denies_access(client: httpx.Client) -> None:
    original = os.environ.pop("ADMIN_API_KEY", None)
    try:
        res = client.get(PROBE, headers={"X-Admin-Key": ADMIN_KEY})
        assert res.status_code == 401
    finally:
        if original is not None:
            os.environ["ADMIN_API_KEY"] = original
