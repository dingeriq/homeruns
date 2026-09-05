"""Today's slate scoring — turns stored lineups + features into HR probabilities.

Reuses the existing pieces end to end:
``game_lineups`` -> ``feature_builder.build_features`` -> ``hr_model.predict``.

When no model artifact is registered, or no lineup is stored for the slate, the
result is an explicit *unavailable* payload with a reason. Nothing is invented.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import models
from app.services.feature_builder import FEATURE_SET_VERSION, build_features
from app.services.hr_model import load_artifact, predict
from app.services.lineups import CONFIRMED, game_lineup_confirmation

logger = logging.getLogger("dingeriq.scoring")


def _lineup_rows(session: Session, day: date) -> List[models.GameLineup]:
    """Official confirmed starters only.

    Projected lineups are deliberately excluded: a projected hitter is not an
    official starter and must never receive a final prediction.
    """
    L = models.GameLineup
    return list(
        session.execute(
            select(L)
            .where(L.game_date == day, L.is_starter.is_(True), L.status == CONFIRMED)
            .order_by(L.game_id, L.side, L.batting_order)
        ).scalars()
    )


def first_pitch_passed(game: Optional[models.Game], now=None) -> bool:
    """True when the game's timezone-aware scheduled first pitch is in the past.

    Uses the backend's stored schedule timestamp only — never a client clock or
    a local calendar date. An unknown first pitch is treated as *not* started so
    a missing timestamp can never silently discard a pregame prediction.
    """
    from datetime import datetime, timezone

    dt = getattr(game, "game_datetime", None)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= dt


def _game_context(session: Session, game_id: int) -> Optional[models.Game]:
    return session.get(models.Game, game_id)


def _opposing_pitcher_id(game: Optional[models.Game], side: str) -> Optional[int]:
    if game is None:
        return None
    return (
        game.away_probable_pitcher_id if side == "home" else game.home_probable_pitcher_id
    )


def score_slate(
    session: Session,
    day: Optional[date] = None,
    *,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Score every stored starter for ``day``.

    Returns ``{"status", "reason", "model_version", "predictions": [...]}`` where
    each prediction matches the ``Prediction`` API contract plus scoring metadata.
    """
    day = day or date.today()
    artifact = load_artifact()
    if artifact is None:
        return {
            "status": "unavailable",
            "reason": "model_not_available: no trained model artifact is registered for scoring.",
            "model_version": None,
            "game_date": str(day),
            "predictions": [],
        }

    confirmation = game_lineup_confirmation(session, day)
    confirmed_games = {gid for gid, info in confirmation.items() if info["is_confirmed"]}
    awaiting = [
        {"game_id": gid, "reason": info["reason"], "confirmed_sides": info["confirmed_sides"]}
        for gid, info in sorted(confirmation.items())
        if not info["is_confirmed"]
    ]
    for entry in awaiting:
        logger.info(
            "Skipping game %s for %s: %s", entry["game_id"], day, entry["reason"]
        )

    rows = [r for r in _lineup_rows(session, day) if r.game_id in confirmed_games]
    if not rows:
        return {
            "status": "unavailable",
            "reason": (
                "no_confirmed_lineups: no game on this slate has official MLB starting "
                "lineups posted for both teams yet. Projected lineups are never scored "
                "as final predictions."
            ),
            "model_version": artifact.get("model_version"),
            "game_date": str(day),
            "predictions": [],
            "games_confirmed": 0,
            "games_awaiting_confirmed_lineups": awaiting,
        }

    games: Dict[int, Optional[models.Game]] = {}
    predictions: List[Dict[str, Any]] = []
    for row in rows:
        if row.game_id not in games:
            games[row.game_id] = _game_context(session, row.game_id)
        game = games[row.game_id]
        pitcher_id = _opposing_pitcher_id(game, row.side)
        try:
            fr = build_features(
                session,
                batter_id=row.player_id,
                target_game_date=day,
                pitcher_id=pitcher_id,
                game_id=row.game_id,
                venue_id=getattr(game, "venue_id", None),
                venue_name=getattr(game, "venue", None),
                include_label=False,
            )
            scored = predict(artifact, fr.features)
        except Exception:
            logger.exception("scoring failed for player=%s game=%s", row.player_id, row.game_id)
            continue

        player = session.get(models.Player, row.player_id)
        predictions.append(
            {
                "player_id": row.player_id,
                "player_name": row.player_name
                or (player.full_name if player else str(row.player_id)),
                "game_id": row.game_id,
                "hr_probability": scored["hr_probability"],
                "confidence": scored["confidence"],
                "model_version": scored["model_version"],
                "features_used": scored["features_used"],
                "features_total": scored["features_total"],
                "imputed_features": scored["imputed_features"],
                "lineup_slot": row.batting_order,
                "team": row.team_abbreviation,
            }
        )

    predictions.sort(key=lambda p: p["hr_probability"], reverse=True)
    if limit:
        predictions = predictions[:limit]

    return {
        "status": "ok" if predictions else "unavailable",
        "reason": None if predictions else "no_scoreable_hitters: features could not be built for this slate.",
        "model_version": artifact.get("model_version"),
        "game_date": str(day),
        "predictions": predictions,
        "games_confirmed": len(confirmed_games),
        "games_awaiting_confirmed_lineups": awaiting,
    }


def score_player(
    session: Session,
    batter_id: int,
    *,
    game_id: Optional[int] = None,
    game_date: Optional[date] = None,
    pitcher_id: Optional[int] = None,
    venue_id: Optional[int] = None,
    venue_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Score a single hitter for the prediction detail contract."""
    artifact = load_artifact()
    if artifact is None:
        return {
            "status": "unavailable",
            "reason": "model_not_available: no trained model artifact is registered for scoring.",
        }
    day = game_date or date.today()
    try:
        fr = build_features(
            session,
            batter_id=batter_id,
            target_game_date=day,
            pitcher_id=pitcher_id,
            game_id=game_id,
            venue_id=venue_id,
            venue_name=venue_name,
            include_label=False,
        )
        scored = predict(artifact, fr.features)
    except Exception:
        logger.exception("single-player scoring failed for %s", batter_id)
        return {
            "status": "unavailable",
            "reason": "scoring_error: features could not be built for this hitter.",
        }
    scored["status"] = "ok"
    return scored


__all__ = [
    "prediction_status",
    "score_player",
    "score_slate",
    "store_slate_predictions",
    "stored_predictions",
]


# ---------------------------------------------------------------------------
# Persistence — the expensive scoring run happens here, once, on demand.
# ---------------------------------------------------------------------------

def store_slate_predictions(
    session: Session,
    day: Optional[date] = None,
    *,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Score ``day`` with the existing V1 model and upsert ``daily_predictions``.

    Idempotent on ``(game_date, game_id, player_id, model_version)`` — a re-run
    updates the stored row in place instead of duplicating it. No ingestion, no
    odds, no weather calls, no training.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select as _select

    day = day or date.today()
    result = score_slate(session, day, limit=limit)
    if result.get("status") != "ok":
        return {
            "status": result.get("status"),
            "reason": result.get("reason"),
            "game_date": str(day),
            "model_version": result.get("model_version"),
            "predictions_scored": 0,
            "games_scored": 0,
            "inserted": 0,
            "updated": 0,
            "locked_games": [],
            "games_awaiting_confirmed_lineups": result.get(
                "games_awaiting_confirmed_lineups", []
            ),
        }

    D = models.DailyPrediction
    now = datetime.now(timezone.utc)
    inserted = updated = skipped_locked = 0
    locked_games: List[int] = []
    started: Dict[int, bool] = {}
    for p in result["predictions"]:
        game_id = p["game_id"]
        # Prediction locking is per game_id, so doubleheaders lock independently.
        if game_id not in started:
            started[game_id] = first_pitch_passed(_game_context(session, game_id), now)
        if started[game_id]:
            if game_id not in locked_games:
                locked_games.append(game_id)
                logger.info(
                    "Game %s has passed first pitch — final pregame predictions are locked; "
                    "no rows written or overwritten.",
                    game_id,
                )
            skipped_locked += 1
            continue
        version = p["model_version"]
        row = session.execute(
            _select(D).where(
                D.game_date == day,
                D.game_id == game_id,
                D.player_id == p["player_id"],
                D.model_version == version,
            )
        ).scalar_one_or_none()
        values = {
            "player_name": p.get("player_name"),
            "team_abbreviation": p.get("team"),
            "lineup_slot": p.get("lineup_slot"),
            "hr_probability": p["hr_probability"],
            "confidence": p.get("confidence"),
            "feature_set_version": FEATURE_SET_VERSION,
            "features_used": p.get("features_used"),
            "features_total": p.get("features_total"),
            "imputed_features": p.get("imputed_features"),
            "lineup_status": CONFIRMED,
            "updated_at": now,
        }
        if row is None:
            session.add(
                D(
                    game_date=day,
                    game_id=p["game_id"],
                    player_id=p["player_id"],
                    model_version=version,
                    created_at=now,
                    **values,
                )
            )
            inserted += 1
        else:
            for key, value in values.items():
                setattr(row, key, value)
            updated += 1
    session.flush()
    written = inserted + updated
    return {
        "status": "ok" if written else "unavailable",
        "reason": None
        if written
        else "all_games_locked: every confirmed game has passed first pitch; existing pregame predictions are final.",
        "game_date": str(day),
        "model_version": result.get("model_version"),
        "predictions_scored": written,
        "games_scored": len(
            {p["game_id"] for p in result["predictions"] if p["game_id"] not in locked_games}
        ),
        "inserted": inserted,
        "updated": updated,
        "locked_predictions_skipped": skipped_locked,
        "locked_games": locked_games,
        "games_awaiting_confirmed_lineups": result.get("games_awaiting_confirmed_lineups", []),
    }


def stored_predictions(
    session: Session,
    day: Optional[date] = None,
    *,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Read persisted predictions for ``day``, ranked by probability desc.

    This is a single indexed query — no feature building, no model inference.
    """
    from sqlalchemy import select as _select

    day = day or date.today()
    D = models.DailyPrediction
    stmt = (
        _select(D)
        .where(D.game_date == day)
        .order_by(D.hr_probability.desc(), D.player_id)
    )
    if limit:
        stmt = stmt.limit(limit)
    rows = list(session.execute(stmt).scalars())
    if not rows:
        return {
            "status": "unavailable",
            "reason": (
                "no_stored_predictions: no predictions are persisted for this date. "
                "Run POST /admin/score-slate once lineups are stored."
            ),
            "game_date": str(day),
            "model_version": None,
            "predictions": [],
        }
    return {
        "status": "ok",
        "reason": None,
        "game_date": str(day),
        "model_version": rows[0].model_version,
        "predictions": [
            {
                "player_id": r.player_id,
                "player_name": r.player_name or str(r.player_id),
                "game_id": r.game_id,
                "hr_probability": r.hr_probability,
                "confidence": r.confidence,
                "model_version": r.model_version,
                "feature_set_version": r.feature_set_version,
                "features_used": r.features_used,
                "features_total": r.features_total,
                "imputed_features": r.imputed_features,
                "lineup_slot": r.lineup_slot,
                "lineup_status": r.lineup_status,
                "team": r.team_abbreviation,
            }
            for r in rows
        ],
    }


def prediction_status(session: Session, day: Optional[date] = None) -> Dict[str, Any]:
    """Read-only inventory of persisted predictions (whole table or one date)."""
    from sqlalchemy import func, select as _select

    D = models.DailyPrediction
    where = [D.game_date == day] if day else []
    total, players, games, first_ts, last_ts, first_day, last_day = session.execute(
        _select(
            func.count(D.id),
            func.count(func.distinct(D.player_id)),
            func.count(func.distinct(D.game_id)),
            func.min(D.created_at),
            func.max(D.updated_at),
            func.min(D.game_date),
            func.max(D.game_date),
        ).where(*where)
    ).one()
    versions = [
        v
        for v in session.execute(
            _select(D.model_version).where(*where).group_by(D.model_version)
        ).scalars()
    ]
    return {
        "predictions_stored": int(total or 0),
        "game_date": str(day) if day else None,
        "model_versions": versions,
        "model_version": versions[0] if len(versions) == 1 else None,
        "earliest_prediction": str(first_ts) if first_ts else None,
        "latest_prediction": str(last_ts) if last_ts else None,
        "unique_players": int(players or 0),
        "unique_games": int(games or 0),
        "first_game_date": str(first_day) if first_day else None,
        "last_game_date": str(last_day) if last_day else None,
    }
