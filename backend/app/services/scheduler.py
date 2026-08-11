"""Daily refresh scheduler."""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.monitoring import record_scheduler_status, record_synced_counts, track_job
from app.state import startup_state
from app.services.statcast_service import sync_statcast
from app.services.sync import run_full_sync
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


async def _statcast_job() -> None:
    logger.info("Daily Statcast ingest starting")
    try:
        with track_job("statcast_daily_ingest"):
            result = await sync_statcast()
        record_synced_counts(result)
        logger.info("Statcast ingest complete: %s", result)
    except Exception as exc:
        logger.exception("Statcast ingest failed: %s", exc)


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
