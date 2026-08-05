"""Process-wide startup state (non-blocking readiness reporting)."""
from __future__ import annotations

from typing import Any, Dict


class StartupState:
    def __init__(self) -> None:
        self.schema_ready: bool = False
        self.scheduler_started: bool = False
        self.initial_sync: str = "pending"  # pending | running | complete | failed
        self.last_error: str | None = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema_ready": self.schema_ready,
            "scheduler_started": self.scheduler_started,
            "initial_sync": self.initial_sync,
            "last_error": self.last_error,
        }


startup_state = StartupState()
