"""DingerIQ FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import admin, games, health, players, predictions, teams
from app.config import settings
from app.database.session import init_db
from app.logging_config import configure_logging
from app.models.schemas import ErrorResponse
from app.services.scheduler import (
    initial_sync_in_background,
    shutdown_scheduler,
    start_scheduler,
)

configure_logging()
logger = logging.getLogger("dingeriq.api")

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="DingerIQ backend — MLB home run prediction platform.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    # Lovable preview + published domains (e.g. https://homeruns.lovable.app)
    allow_origin_regex=r"https://.*\.(lovable\.app|lovableproject\.com)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.__class__.__name__, detail=str(exc.detail)).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(error="ValidationError", detail=str(exc)).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="InternalServerError", detail="An unexpected error occurred.").model_dump(),
    )


app.include_router(health.router)
app.include_router(games.router)
app.include_router(players.router)
app.include_router(teams.router)
app.include_router(predictions.router)
app.include_router(admin.router)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Starting %s (env=%s)", settings.app_name, settings.environment)
    try:
        init_db()
        logger.info("Database schema ready")
    except Exception as exc:
        logger.exception("init_db failed: %s", exc)
    start_scheduler()
    await initial_sync_in_background()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    shutdown_scheduler()
