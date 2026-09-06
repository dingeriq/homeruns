"""Daily refresh scheduler."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database.session import session_scope
from app.services.evaluation import resolve_slate
from app.monitoring import record_scheduler_status, record_synced_counts, track_job
from app.state import startup_state
from app.services.statcast_service import sync_statcast
from app.services.scoring import store_slate_predictions
from app.services.sync import run_full_sync
from app.services.park_factors import compute_park_factors
from app.services.lineups import sync_lineups
from app.services.timeutils import slate_today
from app.services.odds_service import OddsApiNotConfigured, sync_odds
from app.services.weather_service import OpenWeatherNotConfigured, sync_weather

logger = logging.getLogger("dingeriq.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _job() -> None:
    logger.info("Daily MLB refresh starting")
    try:
        with track_job("mlb_daily_refresh"):
            counts = await run_full_sync()
        record_synced_counts(counts)
        logger.info("Daily refresh complete: %s", counts)
    except Exception as exc:
        logger.exception("Daily refresh failed: %s", exc)


async def _resolve_previous_slate() -> None:
    """Stamp realized outcomes on yesterday's predictions.

    Deliberately swallows every failure: evaluation is observational and must
    never break the Statcast ingest or startup.
    """
    day = slate_today() - timedelta(days=1)

    def _run() -> dict:
        with session_scope() as s:
            return resolve_slate(s, day)

    try:
        with track_job("resolve_previous_slate"):
            result = await asyncio.to_thread(_run)
        logger.info("Prediction resolution complete: %s", result)
    except Exception as exc:
        logger.warning("Prediction resolution skipped (%s): %s", type(exc).__name__, exc)


async def _statcast_job() -> None:
    logger.info("Daily Statcast ingest starting")
    try:
        with track_job("statcast_daily_ingest"):
            result = await sync_statcast()
        record_synced_counts(result)
        logger.info("Statcast ingest complete: %s", result)
    except Exception as exc:
        logger.exception("Statcast ingest failed: %s", exc)
    # Runs whether or not the ingest above succeeded, and cannot fail the job.
    await _resolve_previous_slate()



async def _score_slate_job() -> None:
    """Score the current official slate with the registered V1 model.

    Uses the existing ``store_slate_predictions`` service, which is idempotent on
    ``(game_date, game_id, player_id, model_version)`` — repeated runs update the
    stored rows in place. Never trains, never ingests, and never raises: a
    missing slate, missing lineups or missing model artifact is logged as a skip
    so the scheduler keeps running.
    """
    day = slate_today()

    def _run() -> dict:
        with session_scope() as s:
            return store_slate_predictions(s, day)

    try:
        with track_job("daily_slate_scoring"):
            result = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("Slate scoring failed for %s: %s", day, exc)
        return

    for awaiting in result.get("games_awaiting_confirmed_lineups", []) or []:
        logger.info(
            "Slate scoring: game %s not scored — %s",
            awaiting.get("game_id"),
            awaiting.get("reason"),
        )
    for game_id in result.get("locked_games", []) or []:
        logger.info(
            "Slate scoring: game %s locked (first pitch passed) — predictions unchanged.",
            game_id,
        )

    status = result.get("status")
    if status != "ok":
        logger.info(
            "Slate scoring skipped for %s (%s): %s",
            day,
            status,
            result.get("reason"),
        )
        return

    record_synced_counts({"daily_predictions": result.get("predictions_scored", 0)})
    logger.info(
        "Slate scoring complete for %s: %s hitters across %s games "
        "(inserted=%s, updated=%s, model=%s)",
        day,
        result.get("predictions_scored", 0),
        result.get("games_scored", result.get("games")),
        result.get("inserted", 0),
        result.get("updated", 0),
        result.get("model_version"),
    )



async def _park_factor_job() -> None:
    logger.info("Park factor recompute starting")
    try:
        with track_job("park_factor_recompute"):
            result = await asyncio.to_thread(compute_park_factors)
        record_synced_counts({"park_factors": result.get("park_factors_stored", 0)})
        logger.info("Park factor recompute complete: %s", result)
    except Exception as exc:
        logger.exception("Park factor recompute failed: %s", exc)


async def _lineup_job() -> None:
    logger.info("Lineup refresh starting")
    try:
        with track_job("lineup_refresh"):
            result = await sync_lineups()
        record_synced_counts({"lineups": result.get("lineup_slots_stored", 0)})
        logger.info("Lineup refresh complete: %s", result)
    except Exception as exc:
        logger.exception("Lineup refresh failed: %s", exc)


async def _odds_job() -> None:
    if not settings.odds_is_configured:
        logger.info("Odds refresh skipped: ODDS_API_KEY not set")
        return
    logger.info("Odds refresh starting")
    try:
        with track_job("odds_refresh"):
            result = await sync_odds()
        record_synced_counts({"odds": result.get("snapshots_stored", 0)})
        logger.info(
            "Odds refresh complete: %d snapshots stored (quota remaining %s)",
            result.get("snapshots_stored", 0),
            result.get("quota_remaining"),
        )
    except OddsApiNotConfigured as exc:
        logger.warning("Odds refresh skipped: %s", exc)
    except Exception as exc:
        logger.exception("Odds refresh failed: %s", exc)


async def _weather_job() -> None:
    if not settings.openweather_is_configured:
        logger.info("Weather refresh skipped: OPENWEATHER_API_KEY not set")
        return
    logger.info("Weather refresh starting")
    try:
        with track_job("weather_refresh"):
            result = await sync_weather()
        record_synced_counts({"weather": result.get("weather_stored", 0)})
        logger.info("Weather refresh complete: %s", result)
    except OpenWeatherNotConfigured as exc:
        logger.warning("Weather refresh skipped: %s", exc)
    except Exception as exc:
        logger.exception("Weather refresh failed: %s", exc)


def start_scheduler() -> None:
    global _scheduler
    if _scheduler:
        return
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        _job,
        CronTrigger(hour=settings.daily_refresh_hour, minute=settings.daily_refresh_minute),
        id="daily-mlb-refresh",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _statcast_job,
        CronTrigger(
            hour=settings.statcast_refresh_hour,
            minute=settings.statcast_refresh_minute,
        ),
        id="daily-statcast-ingest",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _park_factor_job,
        CronTrigger(
            hour=settings.statcast_refresh_hour,
            minute=(settings.statcast_refresh_minute + 20) % 60,
        ),
        id="daily-park-factors",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _lineup_job,
        CronTrigger(minute="5,35"),
        id="lineup-refresh",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _score_slate_job,
        CronTrigger(minute=settings.score_slate_minute),
        id="daily-slate-scoring",
        max_instances=1,
        coalesce=True,
    )
    # Conservative cadence: the plan is credit-metered, so refresh a few times
    # a day (pre-slate + closing lines) rather than polling continuously.
    sched.add_job(
        _odds_job,
        CronTrigger(hour=f"*/{max(1, settings.odds_refresh_hours)}", minute=25),
        id="odds-refresh",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _weather_job,
        CronTrigger(hour=f"*/{max(1, settings.weather_refresh_hours)}", minute=10),
        id="weather-refresh",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    record_scheduler_status(True)
    logger.info(
        "Scheduler started (MLB %02d:%02d UTC, Statcast %02d:%02d UTC)",
        settings.daily_refresh_hour,
        settings.daily_refresh_minute,
        settings.statcast_refresh_hour,
        settings.statcast_refresh_minute,
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        record_scheduler_status(False)


async def initial_sync_in_background() -> None:
    """Kick off a startup sync without blocking app boot."""
    async def _runner() -> None:
        try:
            with track_job("initial_sync"):
                counts = await run_full_sync()
            record_synced_counts(counts)
            startup_state.initial_sync = "complete"
            logger.info("Initial sync complete: %s", counts)
        except Exception as exc:
            startup_state.initial_sync = "failed"
            startup_state.last_error = f"initial_sync: {type(exc).__name__}"
            logger.exception("Initial sync failed: %s", exc)

    asyncio.create_task(_runner())
