"""SQLAlchemy engine + session factory (lazily created so env vars always win)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import (
    database_environment_presence,
    database_source,
    pg_parts_presence,
    resolve_database_url,
    safe_database_target,
)

from app.monitoring import record_database_up

logger = logging.getLogger("dingeriq.db")


def _normalize_url(url: str) -> str:
    """Railway/Heroku hand out postgres:// or postgresql:// URLs; force psycopg3."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def get_engine() -> Engine:
    """Create the engine on first use, reading DATABASE_URL at that moment."""
    global _engine, _session_factory
    if _engine is None:
        url = resolve_database_url()
        target = safe_database_target(url)
        logger.info(
            "Creating database engine from %s -> host=%s port=%s db=%s",
            database_source(),
            target["host"],
            target["port"],
            target["database"],
        )
        _engine = create_engine(
            _normalize_url(url),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=10,
            future=True,
            connect_args={"connect_timeout": 5},
        )
        _session_factory = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False, future=True
        )
    return _engine


def get_session_factory() -> sessionmaker:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def SessionLocal() -> Session:  # noqa: N802 - preserves existing call sites
    return get_session_factory()()


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_diagnostics() -> dict:
    """Runtime-safe diagnostic — never exposes credentials or full URLs."""
    resolved_url = resolve_database_url()
    source = database_source()
    target = safe_database_target(resolved_url)
    parts = pg_parts_presence()
    return {
        "database_url_present": source != "local fallback default",
        "database_url_source": source,
        "database_driver": _normalize_url(resolved_url).split("://", 1)[0],
        "database_host": target["host"],
        "database_port": target["port"],
        "database_name": target["database"],
        "pg_parts_present": parts,
        "pg_parts_complete": all(parts[k] for k in ("host", "user", "database")),
        "environment_present": database_environment_presence(),
    }


async def check_connection() -> bool:
    def _ping() -> bool:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    try:
        ok = await asyncio.wait_for(asyncio.to_thread(_ping), timeout=8)
        record_database_up(True)
        return ok
    except Exception as exc:
        record_database_up(False, operation="ping")
        target = safe_database_target()
        logger.warning(
            "Database ping failed (host=%s port=%s source=%s): %s",
            target["host"],
            target["port"],
            database_source(),
            exc,
        )
        return False


def init_db() -> None:
    from app.database import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=get_engine())
    _ensure_columns()


# Additive, idempotent column migrations for tables that already exist in
# deployed environments (create_all never alters an existing table).
_COLUMN_MIGRATIONS = (
    ("games", "home_probable_pitcher_id", "INTEGER"),
    ("games", "away_probable_pitcher_id", "INTEGER"),
)


def _ensure_columns() -> None:
    try:
        with get_engine().begin() as conn:
            for table, column, ddl_type in _COLUMN_MIGRATIONS:
                conn.execute(
                    text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "{column}" {ddl_type}')
                )
    except Exception as exc:  # pragma: no cover - never block startup
        logger.warning("Column migration skipped: %s", exc)
