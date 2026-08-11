"""Deep, read-only Statcast coverage audit.

Answers "is there enough pitch-level data to train an HR model?" using plain
SQL only. Nothing is imputed, inferred or written — every number below is a
direct count from `statcast_pitches` (joined to `players` for identity checks).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from sqlalchemy import text

from app.database.session import session_scope

# Column -> minimum share of the relevant denominator we require before we call
# a feature "trainable". Nothing here changes the data; it only labels it.
PITCH_LEVEL_FIELDS = (
    "pitch_type",
    "velocity",
    "spin_rate",
    "horizontal_break",
    "vertical_break",
    "release_pos_x",
    "release_pos_z",
    "extension",
    "plate_x",
    "plate_z",
    "stand",
    "p_throws",
)
CONTACT_LEVEL_FIELDS = (
    "exit_velocity",
    "launch_angle",
    "hit_distance",
    "bb_type",
    "estimated_ba",
    "estimated_woba",
)
SUFFICIENT_THRESHOLD = 0.95
PARTIAL_THRESHOLD = 0.70
MIN_HR_FOR_TRAINING = 2000


def _rows(session, sql: str, **params) -> List[Dict[str, Any]]:
    return [dict(r) for r in session.execute(text(sql), params).mappings().all()]


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


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _label(coverage: float | None) -> str:
    if coverage is None:
        return "no_data"
    if coverage >= SUFFICIENT_THRESHOLD:
        return "sufficient"
    if coverage >= PARTIAL_THRESHOLD:
        return "partial"
    return "insufficient"


def _coverage_block(
    session, fields, denominator_sql: str, denominator: int, params: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    if denominator <= 0:
        return {c: {"filled": 0, "coverage": None, "status": "no_data"} for c in fields}
    sel = ", ".join(f"count({c}) AS {c}" for c in fields)
    filled = _one(
        session, f"SELECT {sel} FROM statcast_pitches {denominator_sql}", **(params or {})
    )
    out: Dict[str, Any] = {}
    for c in fields:
        cov = _pct(filled.get(c), denominator)
        out[c] = {"filled": int(filled.get(c) or 0), "coverage": cov, "status": _label(cov)}
    return out


def run_statcast_audit(start: date | None = None, end: date | None = None) -> Dict[str, Any]:
    """Full-table audit by default; restricted to [start, end] when supplied.

    The range filter is a plain ``game_date`` comparison so PostgreSQL can use
    the existing ``game_date`` index instead of scanning every pitch — which is
    what makes this endpoint safe to call during an active historical ingest.
    Definitions are identical either way; only the row population changes.
    """
    params: Dict[str, Any] = {}
    conds: List[str] = []
    if start is not None:
        conds.append("game_date >= :start")
        params["start"] = start
    if end is not None:
        conds.append("game_date <= :end")
        params["end"] = end
    # WHERE fragment for queries with no other predicate, AND fragment for the rest.
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    andw = (" AND " + " AND ".join(conds)) if conds else ""
    sp_where = where.replace("game_date", "sp.game_date")
    sp_and = andw.replace("game_date", "sp.game_date")
    date_range = {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "scope": "range" if conds else "all",
    }

    with session_scope() as s:
        totals = _one(
            s,
            f"""
            SELECT count(*) AS total_pitches,
                   min(game_date) AS first_date,
                   max(game_date) AS last_date,
                   count(DISTINCT game_id) AS unique_games,
                   count(DISTINCT game_date) AS unique_days,
                   count(DISTINCT batter_id) AS unique_hitters,
                   count(DISTINCT pitcher_id) AS unique_pitchers,
                   count(*) FILTER (WHERE events = 'home_run') AS home_runs,
                   count(*) FILTER (WHERE events IS NOT NULL) AS plate_appearance_results,
                   count(*) FILTER (WHERE exit_velocity IS NOT NULL) AS batted_balls
            FROM statcast_pitches{where}
            """,
            **params,
        )

        total = int(totals.get("total_pitches") or 0)
        if not total:
            return {
                "generated_at": date.today().isoformat(),
                "date_range": date_range,
                "status": "empty",
                "note": (
                    "no statcast_pitches rows in scope — run POST /admin/statcast-sync "
                    "(optionally with start/end/season) before assessing trainability"
                ),
                "totals": {k: _iso(v) for k, v in totals.items()},
                "training_readiness": {
                    "ready": False,
                    "reasons": ["no Statcast rows ingested"],
                },
            }

        batted = int(totals.get("batted_balls") or 0)
        pa = int(totals.get("plate_appearance_results") or 0)
        hrs = int(totals.get("home_runs") or 0)

        by_season = _rows(
            s,
            f"""
            SELECT extract(year FROM game_date)::int AS season,
                   count(*) AS pitches,
                   count(DISTINCT game_id) AS games,
                   count(DISTINCT game_date) AS days,
                   count(DISTINCT batter_id) AS hitters,
                   count(DISTINCT pitcher_id) AS pitchers,
                   count(*) FILTER (WHERE events IS NOT NULL) AS pa_results,
                   count(*) FILTER (WHERE events = 'home_run') AS home_runs,
                   count(*) FILTER (WHERE exit_velocity IS NOT NULL) AS batted_balls
            FROM statcast_pitches{where} GROUP BY 1 ORDER BY 1
            """,
            **params,
        )
        by_month = _rows(
            s,
            f"""
            SELECT to_char(date_trunc('month', game_date), 'YYYY-MM') AS month,
                   count(*) AS pitches,
                   count(DISTINCT game_id) AS games,
                   count(*) FILTER (WHERE events = 'home_run') AS home_runs,
                   count(*) FILTER (WHERE exit_velocity IS NOT NULL) AS batted_balls
            FROM statcast_pitches{where} GROUP BY 1 ORDER BY 1
            """,
            **params,
        )
        pitch_types = _rows(
            s,
            f"""
            SELECT coalesce(pitch_type, '(null)') AS pitch_type,
                   max(pitch_name) AS pitch_name,
                   count(*) AS pitches,
                   count(*) FILTER (WHERE events = 'home_run') AS home_runs
            FROM statcast_pitches{where} GROUP BY 1 ORDER BY pitches DESC
            """,
            **params,
        )
        handedness = {
            "batter": _rows(
                s,
                f"""
                SELECT coalesce(stand, '(null)') AS hand, count(*) AS pitches
                FROM statcast_pitches{where} GROUP BY 1 ORDER BY pitches DESC
                """,
                **params,
            ),
            "pitcher": _rows(
                s,
                f"""
                SELECT coalesce(p_throws, '(null)') AS hand, count(*) AS pitches
                FROM statcast_pitches{where} GROUP BY 1 ORDER BY pitches DESC
                """,
                **params,
            ),
        }
        outcomes = _rows(
            s,
            f"""
            SELECT events, count(*) AS n
            FROM statcast_pitches WHERE events IS NOT NULL{andw}
            GROUP BY 1 ORDER BY n DESC LIMIT 40
            """,
            **params,
        )
        bb_types = _rows(
            s,
            f"""
            SELECT coalesce(bb_type, '(null)') AS bb_type, count(*) AS n,
                   count(*) FILTER (WHERE events = 'home_run') AS home_runs
            FROM statcast_pitches WHERE exit_velocity IS NOT NULL{andw}
            GROUP BY 1 ORDER BY n DESC
            """,
            **params,
        )
        contact = _one(
            s,
            f"""
            SELECT count(*) FILTER (WHERE barrel_flag) AS barrels,
                   count(*) FILTER (WHERE hard_hit_flag) AS hard_hits
            FROM statcast_pitches WHERE exit_velocity IS NOT NULL{andw}
            """,
            **params,
        )

        pitch_coverage = _coverage_block(s, PITCH_LEVEL_FIELDS, where, total, params)
        contact_coverage = _coverage_block(
            s,
            CONTACT_LEVEL_FIELDS,
            f"WHERE exit_velocity IS NOT NULL{andw}",
            batted,
            params,
        )
        release_complete = _one(
            s,
            f"""
            SELECT count(*) FILTER (
                     WHERE release_pos_x IS NOT NULL AND release_pos_z IS NOT NULL
                   ) AS release_point_complete,
                   count(*) FILTER (
                     WHERE plate_x IS NOT NULL AND plate_z IS NOT NULL
                   ) AS location_complete,
                   count(*) FILTER (
                     WHERE horizontal_break IS NOT NULL AND vertical_break IS NOT NULL
                   ) AS movement_complete
            FROM statcast_pitches{where}
            """,
            **params,
        )

        identity = _one(
            s,
            f"""
            SELECT count(DISTINCT batter_id) AS distinct_batters,
                   count(DISTINCT pitcher_id) AS distinct_pitchers,
                   count(DISTINCT batter_id) FILTER (
                     WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = sp.batter_id)
                   ) AS batters_unmatched,
                   count(DISTINCT pitcher_id) FILTER (
                     WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = sp.pitcher_id)
                   ) AS pitchers_unmatched,
                   count(*) FILTER (
                     WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = sp.batter_id)
                   ) AS pitches_with_unmatched_batter
            FROM statcast_pitches sp{sp_where}
            """,
            **params,
        )
        unmatched_sample = _rows(
            s,
            f"""
            SELECT DISTINCT batter_id AS mlb_id
            FROM statcast_pitches sp
            WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = sp.batter_id){sp_and}
            LIMIT 25
            """,
            **params,
        )
        hand_agreement = _one(
            s,
            f"""
            SELECT count(*) AS compared,
                   count(*) FILTER (WHERE p.bats <> sp.stand) AS mismatched
            FROM statcast_pitches sp
            JOIN players p ON p.id = sp.batter_id
            WHERE sp.stand IS NOT NULL AND p.bats IS NOT NULL AND p.bats <> 'S'{sp_and}
            """,
            **params,
        )


        seasons = [int(r["season"]) for r in by_season]
        reasons: List[str] = []
        if hrs < MIN_HR_FOR_TRAINING:
            reasons.append(
                f"only {hrs} home-run outcomes stored (need ~{MIN_HR_FOR_TRAINING}+ "
                "positive labels for a stable player-game classifier)"
            )
        if len(seasons) < 3:
            reasons.append(
                f"{len(seasons)} season(s) available ({seasons}); walk-forward "
                "validation needs at least 3 full seasons"
            )
        if identity.get("batters_unmatched"):
            reasons.append(
                f"{identity['batters_unmatched']} Statcast batter ids have no row in players"
            )
        insufficient = [
            k for k, v in {**pitch_coverage, **contact_coverage}.items()
            if v["status"] in ("insufficient", "no_data")
        ]
        if insufficient:
            reasons.append("low coverage fields: " + ", ".join(sorted(insufficient)))

        return {
            "generated_at": date.today().isoformat(),
            "status": "populated",
            "totals": {
                **{k: _iso(v) for k, v in totals.items()},
                "hr_rate_per_pa": _pct(hrs, pa),
                "hr_rate_per_batted_ball": _pct(hrs, batted),
            },
            "by_season": by_season,
            "by_month": by_month,
            "pitch_types": pitch_types,
            "handedness": handedness,
            "outcomes": outcomes,
            "batted_ball_types": bb_types,
            "contact_quality": {
                **contact,
                "batted_balls": batted,
                "barrel_rate": _pct(contact.get("barrels"), batted),
                "hard_hit_rate": _pct(contact.get("hard_hits"), batted),
            },
            "coverage": {
                "pitch_level": pitch_coverage,
                "contact_level_of_batted_balls": contact_coverage,
                "composite": {
                    k: {"filled": int(v or 0), "coverage": _pct(v, total), "status": _label(_pct(v, total))}
                    for k, v in release_complete.items()
                },
            },
            "identity_linkage": {
                **identity,
                "batter_link_rate": _pct(
                    (identity.get("distinct_batters") or 0) - (identity.get("batters_unmatched") or 0),
                    identity.get("distinct_batters"),
                ),
                "pitcher_link_rate": _pct(
                    (identity.get("distinct_pitchers") or 0) - (identity.get("pitchers_unmatched") or 0),
                    identity.get("distinct_pitchers"),
                ),
                "unmatched_batter_sample": [r["mlb_id"] for r in unmatched_sample],
                "handedness_agreement": {
                    **hand_agreement,
                    "mismatch_rate": _pct(
                        hand_agreement.get("mismatched"), hand_agreement.get("compared")
                    ),
                },
            },
            "training_readiness": {
                "ready": not reasons,
                "seasons_available": seasons,
                "home_run_labels": hrs,
                "non_home_run_pa": max(pa - hrs, 0),
                "minimum_home_run_labels": MIN_HR_FOR_TRAINING,
                "reasons": reasons,
            },
        }
