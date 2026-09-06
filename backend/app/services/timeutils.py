"""Canonical time handling for DingerIQ.

Rules enforced here (and nowhere else):

* UTC is the only canonical backend/database time standard.
* Every datetime produced or normalised here is timezone-aware. Naive values
  read back from a database driver that dropped the offset are interpreted as
  UTC — never as a local wall clock.
* The *baseball slate date* is a calendar date in an IANA timezone
  (``America/New_York`` by default, matching MLB's own schedule date), never
  the server's UTC calendar date. A fixed EST/EDT offset is never used: the
  IANA database handles daylight saving automatically.
* Frontend display time is never used to decide whether a game has started.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

#: IANA zone whose local calendar date defines the MLB slate ("baseball day").
#: Overridable with SLATE_TIMEZONE; must be an IANA name, never an offset.
SLATE_TIMEZONE = os.environ.get("SLATE_TIMEZONE", "America/New_York").strip() or "America/New_York"

try:
    SLATE_TZ = ZoneInfo(SLATE_TIMEZONE)
except Exception:  # pragma: no cover - misconfigured env falls back safely
    SLATE_TZ = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    """Current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Return ``value`` as an aware UTC datetime (naive input is assumed UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_slate_tz(value: Optional[datetime]) -> Optional[datetime]:
    """Convert an instant to the slate timezone (DST-aware)."""
    aware = ensure_utc(value)
    return None if aware is None else aware.astimezone(SLATE_TZ)


def slate_date_for(value: Optional[datetime]) -> Optional[date]:
    """The baseball slate date an absolute instant belongs to."""
    local = to_slate_tz(value)
    return None if local is None else local.date()


def slate_today(now: Optional[datetime] = None) -> date:
    """Today's slate date.

    A 9:40pm ET first pitch on Sept 3 is already Sept 4 in UTC; using the UTC
    calendar date here would silently roll the slate forward while users on the
    US mainland are still on the previous baseball day.
    """
    return slate_date_for(now or utcnow())  # type: ignore[return-value]
