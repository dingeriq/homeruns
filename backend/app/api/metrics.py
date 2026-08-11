"""Metrics endpoints: Prometheus scrape target + JSON summary."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app.monitoring import render_prometheus, summary
from app.state import startup_state

router = APIRouter(tags=["system"])


@router.get("/metrics", include_in_schema=True)
async def metrics() -> Response:
    """Prometheus text exposition format (scrape this endpoint)."""
    payload, content_type = render_prometheus()
    return Response(content=payload, media_type=content_type)


@router.get("/metrics/summary")
async def metrics_summary() -> JSONResponse:
    """Compact JSON snapshot for dashboards and quick health checks."""
    return JSONResponse({**summary(), "startup": startup_state.snapshot()})
