"""Application configuration loaded from environment variables.

IMPORTANT: every value is resolved when ``Settings()`` is instantiated (via
``default_factory``), never baked in at class-definition time, and empty
strings are treated as "not set" so a blank Railway variable can't shadow a
valid one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlsplit

LOCAL_FALLBACK_DATABASE_URL = "postgresql+psycopg://dingeriq:dingeriq@localhost:5432/dingeriq"

# Railway/Heroku/Supabase style variables, in priority order.
DATABASE_URL_ENV_VARS = (
    "DATABASE_URL",
    "DATABASE_PRIVATE_URL",
    "DATABASE_PUBLIC_URL",
    "POSTGRES_URL",
    "POSTGRESQL_URL",
    "PG_URL",
)

SAFE_DATABASE_ENV_VARS = (
    "DATABASE_URL",
    "DATABASE_PRIVATE_URL",
    "DATABASE_PUBLIC_URL",
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "TEST_RAILWAY",
)


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env(name: str) -> Optional[str]:
    """Return a stripped env var, or None when unset/blank."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip().strip('"').strip("'")
    return raw or None


def _database_url_from_parts() -> Optional[str]:
    """Build a URL from discrete PG* vars when no full URL is provided."""
    host = _env("PGHOST") or _env("POSTGRES_HOST")
    user = _env("PGUSER") or _env("POSTGRES_USER")
    password = _env("PGPASSWORD") or _env("POSTGRES_PASSWORD")
    db = _env("PGDATABASE") or _env("POSTGRES_DB")
    port = _env("PGPORT") or _env("POSTGRES_PORT") or "5432"
    if not (host and user and db):
        return None
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"


def resolve_database_url() -> str:
    for name in DATABASE_URL_ENV_VARS:
        value = _env(name)
        if value:
            return value
    built = _database_url_from_parts()
    if built:
        return built
    return LOCAL_FALLBACK_DATABASE_URL


def database_source() -> str:
    """Which env var (if any) supplied the connection string."""
    for name in DATABASE_URL_ENV_VARS:
        if _env(name):
            return name
    if _database_url_from_parts():
        return "PG* environment parts"
    return "local fallback default"


def database_environment_presence() -> dict[str, bool]:
    """Report only whether safe database variables are non-empty.

    Values are read directly from the running process environment on every
    call. Passwords and connection-string contents are never returned.
    """
    return {name: _env(name) is not None for name in SAFE_DATABASE_ENV_VARS}


def safe_database_target(url: Optional[str] = None) -> dict:
    """Host/port/database only — never user, password or the full URL."""
    url = url or resolve_database_url()
    try:
        parts = urlsplit(url)
        return {
            "host": parts.hostname or "unknown",
            "port": parts.port or 5432,
            "database": (parts.path or "/").lstrip("/") or "unknown",
        }
    except Exception:
        return {"host": "unparseable", "port": None, "database": None}


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "DingerIQ API"))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    database_url: str = field(default_factory=resolve_database_url)
    mlb_api_base: str = field(
        default_factory=lambda: os.getenv("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")
    )
    mlb_season: int = field(default_factory=lambda: int(os.getenv("MLB_SEASON", "2026")))
    savant_api_base: str = field(
        default_factory=lambda: os.getenv("SAVANT_API_BASE", "https://baseballsavant.mlb.com")
    )
    statcast_lookback_days: int = field(
        default_factory=lambda: int(os.getenv("STATCAST_LOOKBACK_DAYS", "2"))
    )
    statcast_refresh_hour: int = field(
        default_factory=lambda: int(os.getenv("STATCAST_REFRESH_HOUR", "9"))
    )
    statcast_refresh_minute: int = field(
        default_factory=lambda: int(os.getenv("STATCAST_REFRESH_MINUTE", "30"))
    )
    daily_refresh_hour: int = field(default_factory=lambda: int(os.getenv("DAILY_REFRESH_HOUR", "8")))
    daily_refresh_minute: int = field(
        default_factory=lambda: int(os.getenv("DAILY_REFRESH_MINUTE", "0"))
    )
    cors_origins: List[str] = field(
        default_factory=lambda: _split_csv(
            os.getenv(
                "CORS_ORIGINS",
                "http://localhost:3000,http://localhost:5173,http://localhost:8080",
            )
        )
    )

    @property
    def database_url_is_configured(self) -> bool:
        return database_source() != "local fallback default"


settings = Settings()
