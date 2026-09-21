"""MLB lineup ingestion: confirmed lineups, batting order and PA opportunity.

Design rules (identical to the rest of DingerIQ):

* Players are joined to the canonical ``players`` table by **MLB person id**,
  never by name.
* Confirmed lineups come straight from the MLB Stats API. When MLB has not
  posted a lineup yet we may derive a *projected* lineup from the team's own
  recently stored confirmed lineups — that is measured history, not invention.
  If there is no history, nothing is written and the gap is reported.
* Expected plate appearances are **empirical**: average PA per batting-order
  slot measured from our own ingested Statcast at-bats for lineups we already
  stored. Below the sample threshold the value is ``None``, never guessed.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select

from app.database import models
from app.database.session import session_scope
from app.services.mlb_client import MLBStatsClient
from app.services.timeutils import slate_today
from app.services.sync import _upsert  # noqa: F401  (kept for parity/imports)

logger = logging.getLogger("dingeriq.lineups")

CONFIRMED = "confirmed"
PROJECTED = "projected"

# Minimum stored (slot, game) samples before an empirical PA average is exposed.
MIN_PA_SAMPLES = 25
# A posted MLB lineup card is exactly nine distinct batters.
LINEUP_SLOTS = 9
# Only these abstract game states may create a *new* confirmed lineup.
PREGAME_ABSTRACT_STATES = {"preview"}
PREGAME_DETAILED_STATES = {
    "scheduled",
    "pre-game",
    "warmup",
    "delayed start",
    "delayed",
    "postponed",
}
# How many recent confirmed lineups back a projection.
PROJECTION_LOOKBACK_GAMES = 10


# ---------------------------------------------------------------------------
# Expected plate appearances (empirical, from our own data)
# ---------------------------------------------------------------------------

def expected_pa_by_slot(session) -> Dict[int, Dict[str, Any]]:
    """Average plate appearances per batting-order slot, measured from stored
    confirmed lineups joined to Statcast at-bats.

    Returns ``{slot: {"expected_pa": float|None, "samples": int}}`` for 1..9.
    """
    L = models.GameLineup
    P = models.StatcastPitch
    pa_sub = (
        select(
            P.game_id.label("game_id"),
            P.batter_id.label("batter_id"),
            func.count(func.distinct(P.at_bat_number)).label("pa"),
        )
        .group_by(P.game_id, P.batter_id)
        .subquery()
    )
    rows = session.execute(
        select(L.batting_order, func.avg(pa_sub.c.pa), func.count(pa_sub.c.pa))
        .join(
            pa_sub,
            (pa_sub.c.game_id == L.game_id) & (pa_sub.c.batter_id == L.player_id),
        )
        .where(L.status == CONFIRMED, L.batting_order.isnot(None))
        .group_by(L.batting_order)
    ).all()
    measured = {int(slot): (float(avg), int(n)) for slot, avg, n in rows if slot}
    out: Dict[int, Dict[str, Any]] = {}
    for slot in range(1, 10):
        avg, n = measured.get(slot, (None, 0))
        out[slot] = {
            "expected_pa": round(avg, 2) if avg is not None and n >= MIN_PA_SAMPLES else None,
            "samples": n,
            "note": None
            if n >= MIN_PA_SAMPLES
            else f"insufficient sample ({n} < {MIN_PA_SAMPLES}) — expected PA withheld",
        }
    return out


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def _known_player_ids(session, ids: List[int]) -> set[int]:
    if not ids:
        return set()
    return {
        pid
        for (pid,) in session.execute(
            select(models.Player.id).where(models.Player.id.in_(ids))
        ).all()
    }


def _rows_from_players(
    game_id: int,
    game_date: date,
    side: str,
    team_id: Optional[int],
    team_abbr: Optional[str],
    players: List[Dict[str, Any]],
    status: str,
    source: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for idx, person in enumerate(players, start=1):
        pid = person.get("id")
        if pid is None:
            continue
        rows.append(
            {
                "game_id": game_id,
                "game_date": game_date,
                "side": side,
                "team_id": team_id,
                "team_abbreviation": team_abbr,
                "player_id": int(pid),
                "player_name": person.get("fullName"),
                "batting_order": idx if idx <= 9 else None,
                "position": (person.get("primaryPosition") or {}).get("abbreviation"),
                "is_starter": idx <= 9,
                "status": status,
                "source": source,
            }
        )
    return rows


def _normalise_people(people: Any) -> List[Dict[str, Any]]:
    """Keep only entries MLB gave us a usable person id for.

    The schedule payload is sometimes hydrated with a truthy but unusable
    ``lineups`` block (placeholder entries, no ``id``). Counting usable rows —
    rather than trusting truthiness — is what decides whether we fall back to
    the boxscore.
    """
    if not isinstance(people, list):
        return []
    out: List[Dict[str, Any]] = []
    for person in people:
        if not isinstance(person, dict):
            continue
        pid = person.get("id")
        try:
            pid = int(pid)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        entry = dict(person)
        entry["id"] = pid
        out.append(entry)
    return out


def _has_confirmed(session, game_id: int, side: str) -> bool:
    """True when MLB-posted (confirmed) rows are already stored for this side."""
    return (
        session.execute(
            select(func.count(models.GameLineup.id)).where(
                models.GameLineup.game_id == game_id,
                models.GameLineup.side == side,
                models.GameLineup.status == CONFIRMED,
            )
        ).scalar()
        or 0
    ) > 0




def _batting_order_from_boxscore(payload: Dict[str, Any], side: str) -> List[Dict[str, Any]]:
    team = ((payload.get("teams") or {}).get(side)) or {}
    order = team.get("battingOrder") or []
    players = team.get("players") or {}
    out: List[Dict[str, Any]] = []
    for pid in order[:9]:
        entry = players.get(f"ID{pid}") or {}
        person = entry.get("person") or {}
        out.append(
            {
                "id": pid,
                "fullName": person.get("fullName"),
                "primaryPosition": entry.get("position") or {},
            }
        )
    return out


def _projected_rows(
    session,
    game_id: int,
    game_date: date,
    side: str,
    team_id: Optional[int],
    team_abbr: Optional[str],
) -> List[Dict[str, Any]]:
    """Most frequent batting slot per player across the team's recent confirmed lineups."""
    if not team_abbr:
        return []
    L = models.GameLineup
    recent_games = [
        g
        for (g,) in session.execute(
            select(L.game_id)
            .where(
                L.team_abbreviation == team_abbr,
                L.status == CONFIRMED,
                L.game_date < game_date,
            )
            .distinct()
            .order_by(L.game_id.desc())
            .limit(PROJECTION_LOOKBACK_GAMES)
        ).all()
    ]
    if not recent_games:
        return []
    rows = session.execute(
        select(L.player_id, L.player_name, L.batting_order, L.position).where(
            L.team_abbreviation == team_abbr,
            L.status == CONFIRMED,
            L.game_id.in_(recent_games),
            L.batting_order.isnot(None),
        )
    ).all()
    if not rows:
        return []
    slots: Dict[int, Counter] = defaultdict(Counter)
    names: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
    appearances: Counter = Counter()
    for pid, name, order, pos in rows:
        slots[pid][int(order)] += 1
        appearances[pid] += 1
        names[pid] = (name, pos)
    # Rank candidates by how often they started, then fill slots 1..9.
    ranked = sorted(
        slots.items(),
        key=lambda kv: (-appearances[kv[0]], sum(s * c for s, c in kv[1].items()) / sum(kv[1].values())),
    )
    taken: set[int] = set()
    assigned: List[Tuple[int, int]] = []
    for pid, counter in ranked:
        if len(assigned) >= 9:
            break
        for slot, _ in counter.most_common():
            if slot not in taken:
                taken.add(slot)
                assigned.append((slot, pid))
                break
    out: List[Dict[str, Any]] = []
    for slot, pid in sorted(assigned):
        name, pos = names.get(pid, (None, None))
        out.append(
            {
                "game_id": game_id,
                "game_date": game_date,
                "side": side,
                "team_id": team_id,
                "team_abbreviation": team_abbr,
                "player_id": pid,
                "player_name": name,
                "batting_order": slot,
                "position": pos,
                "is_starter": True,
                "status": PROJECTED,
                "source": f"projected:last_{len(recent_games)}_confirmed_lineups",
            }
        )
    return out


def _store(session, game_id: int, rows: List[Dict[str, Any]], side: str) -> int:
    """Replace the stored lineup for one side of one game (idempotent).

    An empty row list is never destructive: rather than wiping the side, the
    refresh is rejected and whatever is already stored is preserved.
    """
    if not rows:
        logger.info(
            "Lineup refresh rejected (empty rows) for game %s %s — existing rows preserved",
            game_id,
            side,
        )
        return 0
    session.execute(
        delete(models.GameLineup).where(
            models.GameLineup.game_id == game_id, models.GameLineup.side == side
        )
    )
    for row in rows:
        session.add(models.GameLineup(**row))
    return len(rows)


async def sync_lineups(on: Optional[date] = None, client: MLBStatsClient | None = None) -> Dict[str, Any]:
    """Ingest confirmed lineups for a slate, projecting where MLB has none yet.

    Authority hierarchy: ``confirmed > projected > empty/failed``. A lower
    authority refresh never overwrites stored confirmed rows.
    """
    day = on or slate_today()
    client = client or MLBStatsClient()
    payload = await client.schedule(day)

    confirmed_sides = 0
    projected_sides = 0
    unavailable_sides = 0
    preserved_sides = 0
    stored = 0
    unmatched_players: List[int] = []
    games_seen = 0

    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            games_seen += 1
            game_id = g["gamePk"]
            lineups = g.get("lineups") or {}
            teams = g.get("teams") or {}
            for side, key in (("home", "homePlayers"), ("away", "awayPlayers")):
                team = (teams.get(side) or {}).get("team") or {}
                team_id = team.get("id")
                team_abbr = team.get("abbreviation") or team.get("teamCode")
                raw = lineups.get(key)
                people = _normalise_people(raw)
                status = CONFIRMED
                source = "mlb_stats_api:schedule.lineups"
                if not people:
                    if raw:
                        logger.info(
                            "Schedule lineup present but unusable for game %s %s (%d raw entries, 0 usable)",
                            game_id,
                            side,
                            len(raw) if isinstance(raw, list) else 1,
                        )
                    else:
                        logger.debug("Schedule lineup absent for game %s %s", game_id, side)
                    logger.debug("Attempting boxscore fallback for game %s %s", game_id, side)
                    try:
                        box = await client.boxscore(game_id)
                        people = _normalise_people(_batting_order_from_boxscore(box, side))
                        if people:
                            source = "mlb_stats_api:boxscore.battingOrder"
                            logger.info(
                                "Boxscore fallback succeeded for game %s %s (%d starters)",
                                game_id,
                                side,
                                len(people),
                            )
                        else:
                            logger.info(
                                "Boxscore fallback returned no usable batting order for game %s %s",
                                game_id,
                                side,
                            )
                    except Exception as exc:  # boxscore only exists near/after first pitch
                        logger.info("Boxscore fallback failed for game %s %s: %s", game_id, side, exc)
                        people = []
                with session_scope() as s:
                    if people:
                        rows = _rows_from_players(
                            game_id, day, side, team_id, team_abbr, people, status, source
                        )
                        ids = [r["player_id"] for r in rows]
                        known = _known_player_ids(s, ids)
                        unmatched_players.extend(sorted(set(ids) - known))
                        stored += _store(s, game_id, rows, side)
                        confirmed_sides += 1
                        logger.info(
                            "Confirmed lineup persisted for game %s %s (%d slots, %s)",
                            game_id,
                            side,
                            len(rows),
                            source,
                        )
                    elif _has_confirmed(s, game_id, side):
                        preserved_sides += 1
                        logger.info(
                            "Weaker lineup refresh rejected for game %s %s — confirmed lineup already stored",
                            game_id,
                            side,
                        )
                    else:
                        rows = _projected_rows(s, game_id, day, side, team_id, team_abbr)
                        if rows:
                            stored += _store(s, game_id, rows, side)
                            projected_sides += 1
                        else:
                            unavailable_sides += 1

    result = {
        "date": day.isoformat(),
        "games": games_seen,
        "confirmed_sides": confirmed_sides,
        "projected_sides": projected_sides,
        "unavailable_sides": unavailable_sides,
        "preserved_confirmed_sides": preserved_sides,

        "lineup_slots_stored": stored,
        "players_not_in_canonical_table": sorted(set(unmatched_players)),
    }
    logger.info("Lineup sync: %s", result)
    return result


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _serialise(row: models.GameLineup, pa: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    slot_info = pa.get(row.batting_order or 0, {"expected_pa": None, "samples": 0, "note": None})
    return {
        "game_id": row.game_id,
        "game_date": row.game_date.isoformat() if row.game_date else None,
        "side": row.side,
        "team_id": row.team_id,
        "team_abbreviation": row.team_abbreviation,
        "player_id": row.player_id,
        "player_name": row.player_name,
        "batting_order": row.batting_order,
        "position": row.position,
        "is_starter": row.is_starter,
        "status": row.status,
        "source": row.source,
        "expected_plate_appearances": slot_info["expected_pa"],
        "expected_pa_samples": slot_info["samples"],
        "expected_pa_note": slot_info["note"],
    }


def lineups_for_game(game_id: int) -> List[Dict[str, Any]]:
    with session_scope() as s:
        pa = expected_pa_by_slot(s)
        rows = s.execute(
            select(models.GameLineup)
            .where(models.GameLineup.game_id == game_id)
            .order_by(models.GameLineup.side, models.GameLineup.batting_order)
        ).scalars().all()
        return [_serialise(r, pa) for r in rows]


def lineups_for_date(day: Optional[date] = None) -> List[Dict[str, Any]]:
    day = day or slate_today()
    with session_scope() as s:
        pa = expected_pa_by_slot(s)
        rows = s.execute(
            select(models.GameLineup)
            .where(models.GameLineup.game_date == day)
            .order_by(
                models.GameLineup.game_id,
                models.GameLineup.side,
                models.GameLineup.batting_order,
            )
        ).scalars().all()
        return [_serialise(r, pa) for r in rows]


def slate_game_ids(session, day: date) -> set[int]:
    """The authoritative slate membership for ``day``.

    One definition, shared by ``GET /games/today`` and
    ``GET /lineups/confirmation`` so the dashboard's game count and the
    lineup-status game count can never disagree: every game scheduled on the
    slate date, plus every game the slate's lineups or stored predictions
    reference (night games historically stored a UTC first-pitch date that
    rolled to the next calendar day).
    """
    ids: set[int] = {
        int(gid)
        for gid in session.execute(
            select(models.Game.game_id).where(models.Game.game_date == day)
        ).scalars()
        if gid is not None
    }
    for model in (models.GameLineup, models.DailyPrediction):
        ids.update(
            int(gid)
            for gid in session.execute(
                select(model.game_id).where(model.game_date == day).distinct()
            ).scalars()
            if gid is not None
        )
    return ids


def game_lineup_confirmation(session, day: date) -> Dict[int, Dict[str, Any]]:
    """Per-game official-lineup confirmation status for a slate date.

    A game is ``confirmed`` only when **both** sides have at least one MLB-posted
    (``status == 'confirmed'``) starter stored. Anything else — projected rows,
    one side posted, or no rows at all — is *not* an official lineup and is
    reported as ``awaiting``. Confirmation is read from stored MLB Stats API
    data only; it is never inferred.
    """
    L = models.GameLineup
    rows = session.execute(
        select(L.game_id, L.side, L.status, func.count(L.id))
        .where(L.game_date == day)
        .group_by(L.game_id, L.side, L.status)
    ).all()

    out: Dict[int, Dict[str, Any]] = {}
    for game_id, side, status, count in rows:
        entry = out.setdefault(
            int(game_id),
            {"game_id": int(game_id), "confirmed_sides": [], "projected_sides": [], "starters_confirmed": 0},
        )
        if status == CONFIRMED and count:
            if side not in entry["confirmed_sides"]:
                entry["confirmed_sides"].append(side)
            entry["starters_confirmed"] += int(count)
        elif status == PROJECTED and count and side not in entry["projected_sides"]:
            entry["projected_sides"].append(side)

    # Games with no stored lineup rows at all still belong in the report.
    for game_id in slate_game_ids(session, day):
        out.setdefault(
            int(game_id),
            {"game_id": int(game_id), "confirmed_sides": [], "projected_sides": [], "starters_confirmed": 0},
        )

    for entry in out.values():
        confirmed = len(entry["confirmed_sides"]) == 2
        entry["lineup_status"] = CONFIRMED if confirmed else "awaiting_confirmed_lineup"
        entry["is_confirmed"] = confirmed
        entry["reason"] = (
            None
            if confirmed
            else (
                "awaiting_confirmed_lineup: MLB has not posted official starting "
                f"lineups for both teams (confirmed sides: {entry['confirmed_sides'] or 'none'})."
            )
        )
    return out


def confirmed_game_ids(session, day: date) -> set[int]:
    """Game ids whose official MLB starting lineups are confirmed for both sides."""
    return {
        gid for gid, info in game_lineup_confirmation(session, day).items() if info["is_confirmed"]
    }


def lineup_slot_for_player(session, player_id: int, game_id: int) -> Optional[Dict[str, Any]]:
    row = session.execute(
        select(models.GameLineup).where(
            models.GameLineup.game_id == game_id,
            models.GameLineup.player_id == player_id,
        )
    ).scalars().first()
    if row is None:
        return None
    return _serialise(row, expected_pa_by_slot(session))


__all__ = [
    "sync_lineups",
    "confirmed_game_ids",
    "game_lineup_confirmation",
    "lineups_for_game",
    "lineups_for_date",
    "lineup_slot_for_player",
    "expected_pa_by_slot",
    "CONFIRMED",
    "PROJECTED",
]
