"""Daily refresh scheduler."""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.services.sync import run_full_sync

logger = logging.getLogger("dingeriq.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _job() -> None:
    logger.info("Daily MLB refresh starting")
    try:
        counts = await run_full_sync()
        logger.info("Daily refresh complete: %s", counts)
    except Exception as exc:
        logger.exception("Daily refresh failed: %s", exc)


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
    sched.start()
    _scheduler = sched
    logger.info(
        "Scheduler started (daily at %02d:%02d UTC)",
        settings.daily_refresh_hour,
        settings.daily_refresh_minute,
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def initial_sync_in_background() -> None:
    """Kick off a startup sync without blocking app boot."""
    async def _runner() -> None:
        try:
            counts = await run_full_sync()
            logger.info("Initial sync complete: %s", counts)
        except Exception as exc:
            logger.exception("Initial sync failed: %s", exc)

    asyncio.create_task(_runner())
