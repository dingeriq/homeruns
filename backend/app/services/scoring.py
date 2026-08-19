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
from app.services.feature_builder import build_features
from app.services.hr_model import load_artifact, predict

logger = logging.getLogger("dingeriq.scoring")


def _lineup_rows(session: Session, day: date) -> List[models.GameLineup]:
    L = models.GameLineup
    return list(
        session.execute(
            select(L)
            .where(L.game_date == day, L.is_starter.is_(True))
            .order_by(L.game_id, L.side, L.batting_order)
        ).scalars()
    )


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

    rows = _lineup_rows(session, day)
    if not rows:
        return {
            "status": "unavailable",
            "reason": (
                "no_lineups: no confirmed or projected lineups are stored for this date. "
                "Run POST /admin/lineups-sync once MLB posts batting orders."
            ),
            "model_version": artifact.get("model_version"),
            "game_date": str(day),
            "predictions": [],
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


__all__ = ["score_player", "score_slate"]
