"""Tests for the automated daily slate scoring job.

The job must reuse ``store_slate_predictions`` (never duplicate scoring logic),
stay idempotent, skip loudly when prerequisites are missing, and never raise
into the scheduler.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services import scheduler


def _run(coro):
    return asyncio.run(coro)


def test_scoring_job_is_registered_after_sync_jobs():
    src = Path(scheduler.__file__).read_text(encoding="utf-8")
    assert "daily-slate-scoring" in src
    # Reuses the existing service, and never trains or builds snapshots.
    assert "store_slate_predictions" in src
    assert "train_model" not in src
    assert "build_snapshots" not in src


def test_scoring_job_scores_and_logs(monkeypatch, caplog):
    calls = []

    def fake_store(session, day, **kwargs):
        calls.append(day)
        return {
            "status": "ok",
            "predictions_scored": 304,
            "games_scored": 17,
            "inserted": 304,
            "updated": 0,
            "model_version": "v1-logreg-platt",
        }

    monkeypatch.setattr(scheduler, "store_slate_predictions", fake_store)
    monkeypatch.setattr(scheduler, "session_scope", _NullScope)

    with caplog.at_level("INFO", logger="dingeriq.scheduler"):
        _run(scheduler._score_slate_job())

    assert len(calls) == 1
    assert "Slate scoring complete" in caplog.text
    assert "304" in caplog.text and "17" in caplog.text


def test_scoring_job_is_idempotent_on_rerun(monkeypatch):
    """A second run updates rows in place — the service reports 0 inserts."""
    results = [
        {"status": "ok", "predictions_scored": 5, "games_scored": 2, "inserted": 5, "updated": 0,
         "model_version": "v1"},
        {"status": "ok", "predictions_scored": 5, "games_scored": 2, "inserted": 0, "updated": 5,
         "model_version": "v1"},
    ]
    seen = []

    def fake_store(session, day, **kwargs):
        out = results[len(seen)]
        seen.append(out)
        return out

    monkeypatch.setattr(scheduler, "store_slate_predictions", fake_store)
    monkeypatch.setattr(scheduler, "session_scope", _NullScope)

    _run(scheduler._score_slate_job())
    _run(scheduler._score_slate_job())

    assert [r["inserted"] for r in seen] == [5, 0]
    assert [r["updated"] for r in seen] == [0, 5]


@pytest.mark.parametrize(
    "reason",
    [
        "no_lineups: no confirmed or projected lineups are stored for this date.",
        "model_not_available: no trained model artifact is registered for scoring.",
    ],
)
def test_scoring_job_skips_without_prerequisites(monkeypatch, caplog, reason):
    monkeypatch.setattr(
        scheduler,
        "store_slate_predictions",
        lambda session, day, **kw: {"status": "unavailable", "reason": reason},
    )
    monkeypatch.setattr(scheduler, "session_scope", _NullScope)

    with caplog.at_level("INFO", logger="dingeriq.scheduler"):
        _run(scheduler._score_slate_job())

    assert "Slate scoring skipped" in caplog.text
    assert reason.split(":")[0] in caplog.text


def test_scoring_job_never_raises_into_the_scheduler(monkeypatch, caplog):
    def boom(session, day, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(scheduler, "store_slate_predictions", boom)
    monkeypatch.setattr(scheduler, "session_scope", _NullScope)

    with caplog.at_level("ERROR", logger="dingeriq.scheduler"):
        _run(scheduler._score_slate_job())  # must not raise

    assert "Slate scoring failed" in caplog.text


class _NullScope:
    """Stand-in for ``session_scope()`` that yields a dummy session."""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False

    def __call__(self):
        return self
