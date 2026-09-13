"""Shared fixtures for DingerIQ endpoint integration tests.

Two modes:

* In-process (default) — the FastAPI app is exercised through httpx's
  ASGITransport. No network, no server required.
* Live — set ``API_BASE_URL`` (e.g. the Railway URL) to run the exact same
  tests against a deployed instance.

Tests that need real MLB rows are skipped automatically when the database is
unreachable, so the suite is safe to run anywhere (CI, laptop, container).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API_BASE_URL = os.environ.get("API_BASE_URL", "").rstrip("/")
LIVE = bool(API_BASE_URL)

# Admin endpoints require a shared secret. In-process tests use a fixed test
# key; live runs take the real key from the environment.
TEST_ADMIN_KEY = "test-admin-key"
if not LIVE:
    os.environ.setdefault("ADMIN_API_KEY", TEST_ADMIN_KEY)
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", TEST_ADMIN_KEY)


@pytest.fixture(scope="session")
def client() -> Iterator[httpx.Client]:
    headers = {"X-Admin-Key": ADMIN_KEY}
    if LIVE:
        with httpx.Client(base_url=API_BASE_URL, timeout=30.0, headers=headers) as c:
            yield c
        return

    from fastapi.testclient import TestClient

    from app.main import app  # imported lazily so sys.path is set first

    # TestClient runs the ASGI app in-process and works across httpx versions.
    with TestClient(app, base_url="http://testserver", headers=headers) as c:
        yield c



@pytest.fixture(scope="session")
def ready_payload(client: httpx.Client) -> dict:
    return client.get("/ready").json()


@pytest.fixture(scope="session")
def db_up(ready_payload: dict) -> bool:
    return ready_payload.get("database") == "up"


@pytest.fixture()
def require_db(db_up: bool) -> None:
    if not db_up:
        pytest.skip("database unreachable — skipping data-dependent test")
