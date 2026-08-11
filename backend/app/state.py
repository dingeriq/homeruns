"""Process-wide startup state (non-blocking readiness reporting)."""
from __future__ import annotations

from typing import Any, Dict


from app.monitoring import (
    record_initial_sync_status,
    record_schema_ready,
    record_scheduler_status,
)


class StartupState:
    """Startup/readiness flags. Setting a flag also updates the matching gauge."""

    def __init__(self) -> None:
        self._schema_ready: bool = False
        self._scheduler_started: bool = False
        self._initial_sync: str = "pending"  # pending | running | complete | failed
        self.last_error: str | None = None

    @property
    def schema_ready(self) -> bool:
        return self._schema_ready

    @schema_ready.setter
    def schema_ready(self, value: bool) -> None:
        self._schema_ready = bool(value)
        record_schema_ready(self._schema_ready)

    @property
    def scheduler_started(self) -> bool:
        return self._scheduler_started

    @scheduler_started.setter
    def scheduler_started(self, value: bool) -> None:
        self._scheduler_started = bool(value)
        record_scheduler_status(self._scheduler_started)

    @property
    def initial_sync(self) -> str:
        return self._initial_sync

    @initial_sync.setter
    def initial_sync(self, value: str) -> None:
        self._initial_sync = value
        record_initial_sync_status(value)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema_ready": self.schema_ready,
            "scheduler_started": self.scheduler_started,
            "initial_sync": self.initial_sync,
            "last_error": self.last_error,
        }


startup_state = StartupState()
