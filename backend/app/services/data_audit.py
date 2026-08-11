"""Read-only audit of player/pitcher identity quality and Statcast coverage.

Answers two questions with plain SQL (no fabrication, no writes):

1. Identity: do our players / games / statcast rows carry reliable MLB person
   ids, and do the ids actually join?
2. Statcast: exactly how much pitch-level data do we hold — rows, date span,
   games, seasons, per-month coverage and field completeness?
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from sqlalchemy import text

from app.database.session import session_scope


def _rows(session, sql: str, **params) -> List[Dict[str, Any]]:
    res = session.execute(text(sql), params)
    return [dict(r) for r in res.mappings().all()]


def _one(session, sql: str, **params) -> Dict[str, Any]:
    out = _rows(session, sql, **params)
    return out[0] if out else {}


def _pct(part: Any, whole: Any) -> float | None:
    try:
        whole = float(whole or 0)
        if whole <= 0:
            return None
        return round(float(part or 0) / whole, 4)
    except (TypeError, ValueError):
        return None


def audit_identity(session) -> Dict[str, Any]:
    players = _one(
        session,
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE full_name IS NULL OR full_name = '') AS missing_name,
               count(*) FILTER (WHERE team_id IS NULL) AS missing_team,
               count(*) FILTER (WHERE position IS NULL OR position = '') AS missing_position,
               count(*) FILTER (WHERE bats IS NULL OR bats = '') AS missing_bats,
               count(*) FILTER (WHERE throws IS NULL OR throws = '') AS missing_throws,
               count(DISTINCT id) AS distinct_ids
        FROM players
        """,
    )
    dupes = _rows(
        session,
        """
        SELECT full_name, count(*) AS n
        FROM players
        WHERE full_name <> ''
        GROUP BY full_name HAVING count(*) > 1
        ORDER BY n DESC, full_name
        LIMIT 25
        """,
    )
    games = _one(
        session,
        """
        SELECT count(*) AS games,
               count(home_probable_pitcher) + count(away_probable_pitcher) AS probable_names,
               count(home_probable_pitcher_id) + count(away_probable_pitcher_id) AS probable_ids
        FROM games
        WHERE game_date >= :today
        """,
        today=date.today(),
    )
    unresolved = _rows(
        session,
        """
        SELECT DISTINCT pid AS pitcher_id
        FROM (
            SELECT home_probable_pitcher_id AS pid FROM games WHERE game_date >= :today
            UNION ALL
            SELECT away_probable_pitcher_id FROM games WHERE game_date >= :today
        ) t
        WHERE pid IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM players p WHERE p.id = t.pid)
        LIMIT 50
        """,
        today=date.today(),
    )
    statcast_ids = _one(
        session,
        """
        SELECT count(DISTINCT batter_id) AS distinct_batters,
               count(DISTINCT pitcher_id) AS distinct_pitchers,
               count(DISTINCT batter_id) FILTER (
                   WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = s.batter_id)
               ) AS batters_not_in_players,
               count(DISTINCT pitcher_id) FILTER (
                   WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = s.pitcher_id)
               ) AS pitchers_not_in_players
        FROM statcast_pitches s
        """,
    )

    probable_names = games.get("probable_names") or 0
    probable_ids = games.get("probable_ids") or 0
    issues: List[str] = []
    if probable_names and probable_ids < probable_names:
        issues.append(
            "some probable starters have a name but no MLB id "
            "(run POST /admin/sync to refresh the schedule)"
        )
    if unresolved:
        issues.append("some probable-pitcher ids have no row in players")
    if (players.get("missing_bats") or 0) or (players.get("throws") or 0):
        pass
    if players.get("missing_bats"):
        issues.append("players.bats is not populated for every player")
    if players.get("missing_throws"):
        issues.append("players.throws is not populated for every player")

    return {
        "players": {
            **players,
            "duplicate_names": dupes,
            "bats_coverage": _pct(
                (players.get("total") or 0) - (players.get("missing_bats") or 0),
                players.get("total"),
            ),
            "throws_coverage": _pct(
                (players.get("total") or 0) - (players.get("missing_throws") or 0),
                players.get("total"),
            ),
        },
        "games": {
            **games,
            "probable_pitcher_id_coverage": _pct(probable_ids, probable_names),
            "unresolved_probable_pitcher_ids": [r["pitcher_id"] for r in unresolved],
        },
        "statcast_ids": statcast_ids,
        "issues": issues,
        "identity_ok": not issues,
    }


def audit_statcast(session) -> Dict[str, Any]:
    totals = _one(
        session,
        """
        SELECT count(*) AS pitches,
               count(DISTINCT game_id) AS games,
               count(DISTINCT game_date) AS days,
               min(game_date) AS first_date,
               max(game_date) AS last_date,
               count(DISTINCT batter_id) AS batters,
               count(DISTINCT pitcher_id) AS pitchers
        FROM statcast_pitches
        """,
    )
    if not totals.get("pitches"):
        return {
            "pitches": 0,
            "status": "empty",
            "note": "no Statcast rows ingested yet — run POST /admin/statcast-sync",
            "by_month": [],
            "field_completeness": {},
            "batted_balls": {},
        }

    by_month = _rows(
        session,
        """
        SELECT to_char(date_trunc('month', game_date), 'YYYY-MM') AS month,
               count(*) AS pitches,
               count(DISTINCT game_id) AS games,
               count(DISTINCT game_date) AS days
        FROM statcast_pitches
        GROUP BY 1 ORDER BY 1
        """,
    )
    completeness_cols = (
        "pitch_type",
        "velocity",
        "spin_rate",
        "horizontal_break",
        "vertical_break",
        "extension",
        "plate_x",
        "plate_z",
        "exit_velocity",
        "launch_angle",
        "hit_distance",
        "estimated_woba",
        "stand",
        "p_throws",
    )
    sel = ", ".join(f"count({c}) AS {c}" for c in completeness_cols)
    filled = _one(session, f"SELECT {sel} FROM statcast_pitches")
    total_pitches = totals["pitches"]
    field_completeness = {c: _pct(filled.get(c), total_pitches) for c in completeness_cols}

    batted = _one(
        session,
        """
        SELECT count(*) FILTER (WHERE exit_velocity IS NOT NULL) AS batted_balls,
               count(*) FILTER (WHERE barrel_flag) AS barrels,
               count(*) FILTER (WHERE hard_hit_flag) AS hard_hits,
               count(*) FILTER (WHERE events = 'home_run') AS home_runs
        FROM statcast_pitches
        """,
    )

    # Do we have enough per-hitter history to build rolling features?
    depth = _one(
        session,
        """
        WITH bb AS (
            SELECT batter_id, count(*) AS n
            FROM statcast_pitches
            WHERE exit_velocity IS NOT NULL
            GROUP BY batter_id
        )
        SELECT count(*) AS hitters_with_batted_balls,
               count(*) FILTER (WHERE n >= 50) AS hitters_with_50plus,
               count(*) FILTER (WHERE n >= 100) AS hitters_with_100plus,
               coalesce(round(avg(n)::numeric, 1), 0) AS avg_batted_balls_per_hitter
        FROM bb
        """,
    )

    gaps = _rows(
        session,
        """
        WITH d AS (SELECT DISTINCT game_date FROM statcast_pitches),
        ordered AS (
            SELECT game_date, lag(game_date) OVER (ORDER BY game_date) AS prev
            FROM d
        )
        SELECT prev AS gap_start, game_date AS gap_end, (game_date - prev) AS missing_days
        FROM ordered
        WHERE prev IS NOT NULL AND game_date - prev > 1
        ORDER BY missing_days DESC
        LIMIT 10
        """,
    )

    span_days = None
    if totals.get("first_date") and totals.get("last_date"):
        span_days = (totals["last_date"] - totals["first_date"]).days + 1

    return {
        **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in totals.items()},
        "status": "populated",
        "span_days": span_days,
        "date_coverage": _pct(totals.get("days"), span_days),
        "by_month": by_month,
        "field_completeness": field_completeness,
        "batted_balls": {
            **batted,
            "barrel_rate": _pct(batted.get("barrels"), batted.get("batted_balls")),
            "hard_hit_rate": _pct(batted.get("hard_hits"), batted.get("batted_balls")),
        },
        "modeling_depth": depth,
        "date_gaps": [
            {
                "gap_start": g["gap_start"].isoformat(),
                "gap_end": g["gap_end"].isoformat(),
                "missing_days": int(g["missing_days"]) - 1,
            }
            for g in gaps
        ],
    }


def audit_lineups(session) -> Dict[str, Any]:
    """Coverage of stored batting orders and how well they join to players."""
    try:
        totals = _one(
            session,
            """
            SELECT count(*) AS slots,
                   count(*) FILTER (WHERE status = 'confirmed') AS confirmed_slots,
                   count(*) FILTER (WHERE status = 'projected') AS projected_slots,
                   count(*) FILTER (WHERE batting_order IS NULL) AS missing_batting_order,
                   count(DISTINCT game_id) AS games,
                   count(DISTINCT player_id) AS players,
                   min(game_date) AS first_date,
                   max(game_date) AS last_date
            FROM game_lineups
            """,
        )
        unmatched = _one(
            session,
            """
            SELECT count(*) AS n
            FROM game_lineups l
            LEFT JOIN players p ON p.id = l.player_id
            WHERE p.id IS NULL
            """,
        )
        today = _one(
            session,
            """
            SELECT count(DISTINCT game_id) AS games_with_lineups,
                   count(*) AS slots
            FROM game_lineups WHERE game_date = :d
            """,
            d=date.today(),
        )
    except Exception as exc:
        return {"status": "unavailable", "reason": f"{type(exc).__name__}"}
    slots = totals.get("slots") or 0
    from app.services.lineups import expected_pa_by_slot

    return {
        "status": "ok" if slots else "empty",
        **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in totals.items()},
        "players_not_in_canonical_table": unmatched.get("n", 0),
        "id_join_rate": _pct(slots - (unmatched.get("n") or 0), slots),
        "today": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in today.items()},
        "expected_pa_by_slot": expected_pa_by_slot(session),
    }


def run_audit() -> Dict[str, Any]:
    with session_scope() as s:
        identity = audit_identity(s)
        statcast = audit_statcast(s)
        lineups = audit_lineups(s)
    return {
        "generated_at": date.today().isoformat(),
        "identity": identity,
        "statcast": statcast,
        "lineups": lineups,
        "summary": {
            "identity_ok": identity["identity_ok"],
            "statcast_pitches": statcast.get("pitches", 0),
            "statcast_status": statcast.get("status"),
            "statcast_first_date": statcast.get("first_date"),
            "statcast_last_date": statcast.get("last_date"),
            "lineup_slots": lineups.get("slots", 0),
            "lineup_status": lineups.get("status"),
        },
    }
