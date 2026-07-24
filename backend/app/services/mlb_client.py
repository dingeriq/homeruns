"""Thin async client for the public MLB Stats API."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger("dingeriq.mlb")

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MLBStatsClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.mlb_api_base

    async def _get(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    logger.debug("GET %s params=%s", url, params)
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    return resp.json()
        raise RuntimeError("unreachable")

    async def teams(self, season: int | None = None) -> Dict[str, Any]:
        return await self._get(
            "/teams",
            params={"sportId": 1, "season": season or settings.mlb_season},
        )

    async def schedule(self, on: date) -> Dict[str, Any]:
        return await self._get(
            "/schedule",
            params={
                "sportId": 1,
                "date": on.isoformat(),
                "hydrate": "probablePitcher,team,venue",
            },
        )

    async def roster(self, team_id: int, season: int | None = None) -> Dict[str, Any]:
        return await self._get(
            f"/teams/{team_id}/roster",
            params={"rosterType": "active", "season": season or settings.mlb_season},
        )

    async def person(self, person_id: int) -> Dict[str, Any]:
        return await self._get(f"/people/{person_id}")
