"""/games/today must cover the whole slate the predictions belong to."""
from __future__ import annotations

import httpx
import pytest


def test_games_today_covers_prediction_game_ids(client: httpx.Client, require_db: None) -> None:
    games = client.get("/games/today").json()
    preds = client.get("/predictions/today").json()
    if not preds:
        pytest.skip("no scored slate persisted")
    pred_ids = {int(p["game_id"]) for p in preds}
    game_ids = {int(g["game_id"]) for g in games}
    missing = pred_ids - game_ids
    assert not missing, f"slate games missing from /games/today: {sorted(missing)}"


def test_games_today_accepts_explicit_date(client: httpx.Client, require_db: None) -> None:
    res = client.get("/games/today", params={"on": "2024-04-01"})
    assert res.status_code == 200
    for g in res.json():
        assert g["game_date"].startswith("2024-04-0")


def test_sync_uses_official_schedule_date() -> None:
    from datetime import date

    from app.services import sync

    payload = {
        "dates": [
            {
                "date": "2026-08-28",
                "games": [
                    {
                        "gamePk": 1,
                        "gameDate": "2026-08-29T02:15:00Z",
                        "venue": {"id": 1, "name": "Oracle Park"},
                        "status": {"detailedState": "Scheduled"},
                        "teams": {
                            "home": {"team": {"id": 137, "abbreviation": "SF"}},
                            "away": {"team": {"id": 109, "abbreviation": "AZ"}},
                        },
                    }
                ],
            }
        ]
    }

    captured: list[dict] = []

    class FakeClient:
        async def schedule(self, on):  # noqa: ANN001
            return payload

    def fake_upsert(session, model, rows, key):  # noqa: ANN001
        captured.extend(rows)
        return len(rows)

    class FakeScope:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    orig_upsert, orig_scope = sync._upsert, sync.session_scope
    sync._upsert = fake_upsert  # type: ignore[assignment]
    sync.session_scope = lambda: FakeScope()  # type: ignore[assignment]
    try:
        import asyncio

        asyncio.run(sync.sync_games(FakeClient()))
    finally:
        sync._upsert = orig_upsert  # type: ignore[assignment]
        sync.session_scope = orig_scope  # type: ignore[assignment]

    assert captured and captured[0]["game_date"] == date(2026, 8, 28)
