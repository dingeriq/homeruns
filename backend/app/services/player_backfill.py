"""Historical player identity backfill.

Statcast rows carry canonical MLB person ids for every batter and pitcher, but
``players`` only holds current active rosters, so historical seasons cannot be
joined to an identity. This module discovers the missing ids, resolves them
through the MLB Stats API by **id only** (never by name), and upserts them into
the existing ``players`` table.

Guarantees:

* ``players.id`` is the MLB person id and is never modified.
* No name matching, no fuzzy matching, no fabricated values — every field comes
  straight from ``GET /people?personIds=...`` or stays NULL.
* An existing non-null column is never overwritten with NULL (COALESCE upsert).
* Retired players have no ``currentTeam``; their team fields stay NULL rather
  than being assigned a wrong club.
* Idempotent: a second run discovers nothing and changes nothing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from sqlalchemy import func, select, text

from app.database import models
from app.database.session import session_scope
from app.services.mlb_client import MLBStatsClient

logger = logging.getLogger("dingeriq.player_backfill")

BATCH_SIZE = 50
BATCH_PAUSE_SECONDS = 0.25

# Columns the backfill may fill in. ``id`` is excluded on purpose: it is the
# canonical key and must never be written by an UPDATE.
FILLABLE_COLUMNS = (
    "full_name",
    "team_id",
    "team_abbreviation",
    "position",
    "bats",
    "throws",
    "is_active",
    "last_seen_season",
)


def discover_missing_ids(session) -> Dict[int, int | None]:
    """Return ``{mlb_person_id: last_season_seen}`` for Statcast ids absent from players.

    The season comes from the Statcast ``game_date`` the id was last observed
    in — it is observed, never invented.
    """
    dialect = session.get_bind().dialect.name
    # Same semantics either way; SQLite (tests) has no extract()/cast syntax.
    season_expr = (
        "CAST(strftime('%Y', game_date) AS INTEGER)"
        if dialect == "sqlite"
        else "extract(year FROM game_date)::int"
    )
    sql = f"""
        SELECT person_id, max(season) AS last_season
        FROM (
            SELECT batter_id AS person_id,
                   {season_expr} AS season
            FROM statcast_pitches
            WHERE batter_id IS NOT NULL
            UNION ALL
            SELECT pitcher_id AS person_id,
                   {season_expr} AS season
            FROM statcast_pitches
            WHERE pitcher_id IS NOT NULL
        ) s
        WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = s.person_id)
        GROUP BY person_id
        ORDER BY person_id
    """
    rows = session.execute(text(sql)).mappings().all()
    return {int(r["person_id"]): (int(r["last_season"]) if r["last_season"] else None) for r in rows}


def count_statcast_person_ids(session) -> int:
    """Total distinct batter+pitcher ids present in statcast_pitches."""
    sql = """
        SELECT count(*) AS n FROM (
            SELECT batter_id AS person_id FROM statcast_pitches WHERE batter_id IS NOT NULL
            UNION
            SELECT pitcher_id FROM statcast_pitches WHERE pitcher_id IS NOT NULL
        ) s
    """
    return int(session.execute(text(sql)).scalar() or 0)


def person_to_row(person: Dict[str, Any], last_seen_season: int | None) -> Dict[str, Any] | None:
    """Map one MLB Stats API person payload to a ``players`` row.

    Returns ``None`` when the payload has no usable id. Missing attributes stay
    ``None`` — nothing is guessed. Retired players (no ``currentTeam``) keep
    NULL team fields.
    """
    pid = person.get("id")
    if pid is None:
        return None
    team = person.get("currentTeam") or {}
    active = person.get("active")
    return {
        "id": int(pid),
        "full_name": person.get("fullName") or None,
        "team_id": team.get("id"),
        "team_abbreviation": (team.get("abbreviation") or None),
        "position": ((person.get("primaryPosition") or {}).get("abbreviation") or None),
        "bats": ((person.get("batSide") or {}).get("code") or None),
        "throws": ((person.get("pitchHand") or {}).get("code") or None),
        "is_active": bool(active) if isinstance(active, bool) else None,
        "last_seen_season": last_seen_season,
    }


async def resolve_people(
    client: MLBStatsClient,
    ids: Sequence[int],
    batch_size: int = BATCH_SIZE,
    pause_seconds: float = BATCH_PAUSE_SECONDS,
) -> Tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve ids through ``GET /people?personIds=...`` in ~50-id batches.

    A failing batch is logged and recorded in ``errors`` — the remaining
    batches still run.
    """
    resolved: Dict[int, Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []
    batches = [list(ids[i : i + batch_size]) for i in range(0, len(ids), batch_size)]
    for index, batch in enumerate(batches):
        if index and pause_seconds:
            await asyncio.sleep(pause_seconds)
        try:
            payload = await client.people(batch)
        except Exception as exc:  # network/HTTP failure — keep going
            logger.warning("people batch %d failed (%d ids): %s", index, len(batch), exc)
            errors.append(
                {
                    "batch": index,
                    "ids": len(batch),
                    "first_id": batch[0],
                    "last_id": batch[-1],
                    "error": type(exc).__name__,
                }
            )
            continue
        for person in payload.get("people") or []:
            pid = person.get("id")
            if pid is not None:
                resolved[int(pid)] = person
    return resolved, errors


def _coalesce_set(stmt, table):
    """UPDATE map that never replaces a stored non-null value with NULL."""
    return {
        name: func.coalesce(stmt.excluded[name], table.c[name])
        for name in FILLABLE_COLUMNS
        if name in table.c
    }


def upsert_players(session, rows: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
    """Idempotent ``ON CONFLICT (id) DO UPDATE`` upsert. Returns (inserted, updated)."""
    rows = [r for r in rows if r and r.get("id") is not None]
    if not rows:
        return 0, 0

    table = models.Player.__table__
    ids = [r["id"] for r in rows]
    existing = {
        i for (i,) in session.execute(select(models.Player.id).where(models.Player.id.in_(ids)))
    }

    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as ins
    else:
        from sqlalchemy.dialects.postgresql import insert as ins

    stmt = ins(table).values(rows)
    stmt = stmt.on_conflict_do_update(index_elements=[table.c.id], set_=_coalesce_set(stmt, table))
    session.execute(stmt)

    updated = len(existing)
    return len(rows) - updated, updated


async def backfill_players(
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
    pause_seconds: float = BATCH_PAUSE_SECONDS,
    client: MLBStatsClient | None = None,
) -> Dict[str, Any]:
    """Discover, resolve and upsert historical player identities."""
    started = time.perf_counter()
    client = client or MLBStatsClient()

    with session_scope() as s:
        missing = discover_missing_ids(s)
        already_present = count_statcast_person_ids(s) - len(missing)

    ids = sorted(missing)
    if limit is not None:
        ids = ids[:limit]

    resolved, errors = await resolve_people(
        client, ids, batch_size=batch_size, pause_seconds=pause_seconds
    )

    rows: List[Dict[str, Any]] = []
    for pid in ids:
        person = resolved.get(pid)
        if person is None:
            continue
        row = person_to_row(person, missing.get(pid))
        if row is not None:
            rows.append(row)

    inserted = updated = 0
    if rows:
        try:
            with session_scope() as s:
                inserted, updated = upsert_players(s, rows)
        except Exception as exc:
            logger.exception("player upsert failed")
            errors.append({"stage": "upsert", "rows": len(rows), "error": type(exc).__name__})

    unresolved = [pid for pid in ids if pid not in resolved]
    return {
        "discovered_missing_ids": len(missing),
        "attempted_ids": len(ids),
        "resolved_ids": len(resolved),
        "unresolved_ids": len(unresolved),
        "unresolved_sample": unresolved[:25],
        "players_inserted": inserted,
        "players_updated": updated,
        "already_present": max(already_present, 0),
        "errors": errors,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
