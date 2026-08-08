"""Liveness + readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database.session import check_connection, database_diagnostics
from app.models.schemas import HealthResponse
from app.state import startup_state

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: always 200 once FastAPI is accepting requests.

    Never blocks on the database, MLB sync, Statcast import or scheduler.
    """
    return HealthResponse(status="healthy", service="DingerIQ API")


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: checks database + startup task progress."""
    db_ok = await check_connection()
    payload = {
        "status": "ready" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        **startup_state.snapshot(),
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)
