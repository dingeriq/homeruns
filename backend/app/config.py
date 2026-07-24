"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "DingerIQ API")
    environment: str = os.getenv("ENVIRONMENT", "development")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://dingeriq:dingeriq@localhost:5432/dingeriq",
    )
    mlb_api_base: str = os.getenv("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")
    mlb_season: int = int(os.getenv("MLB_SEASON", "2026"))
    daily_refresh_hour: int = int(os.getenv("DAILY_REFRESH_HOUR", "8"))
    daily_refresh_minute: int = int(os.getenv("DAILY_REFRESH_MINUTE", "0"))
    cors_origins: List[str] = field(
        default_factory=lambda: _split_csv(
            os.getenv(
                "CORS_ORIGINS",
                "http://localhost:3000,http://localhost:5173,http://localhost:8080",
            )
        )
    )


settings = Settings()
