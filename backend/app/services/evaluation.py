"""Post-game resolution of stored predictions and V1 performance metrics.

Two responsibilities, both strictly additive to the existing pipeline:

1. ``resolve_slate`` — join persisted ``daily_predictions`` rows to realized
   Statcast outcomes on ``(game_id, batter_id)`` and stamp ``actual_hr``.
2. ``performance`` — read-only scoring of resolved rows only.

Nothing here touches the model, the feature set, scoring, prediction
probabilities, or ingestion. A game whose Statcast rows have not been ingested
yet stays ``actual_hr = NULL`` — a missing pitch row is never a zero.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import models
from app.services.feature_builder import label_for_target_game

logger = logging.getLogger("dingeriq.evaluation")

RESOLUTION_SOURCE = "statcast"

#: Probabilities are clamped before log loss so a 0/1 prediction cannot produce
#: an infinite penalty. Purely a metric guard — stored values are untouched.
_EPS = 1e-15


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------
def games_with_statcast(session: Session, game_ids: Iterable[int]) -> Set[int]:
    """Subset of ``game_ids`` that already have Statcast pitch rows stored.

    This is the ingestion gate: only these games may be resolved, because for
    any other game the absence of a home run is indistinguishable from the
    absence of data.
    """
    ids = [int(g) for g in game_ids]
    if not ids:
        return set()
    P = models.StatcastPitch
    rows = session.execute(
        select(P.game_id).where(P.game_id.in_(ids)).group_by(P.game_id)
    ).scalars()
    return {int(g) for g in rows}


def resolve_slate(
    session: Session,
    game_date: date,
    model_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Stamp realized HR outcomes onto the predictions for one slate date.

    Idempotent: re-running recomputes the same label from the same Statcast
    rows and writes it in place. Rows whose game has no Statcast data are left
    untouched (``actual_hr`` stays NULL) and counted as ``unresolved``.
    """
    D = models.DailyPrediction
    where = [D.game_date == game_date]
    if model_version:
        where.append(D.model_version == model_version)

    predictions: List[models.DailyPrediction] = list(
        session.execute(select(D).where(*where)).scalars()
    )
    if not predictions:
        return {
            "status": "no_predictions",
            "game_date": str(game_date),
            "model_version": model_version,
            "predictions": 0,
            "resolved": 0,
            "unresolved": 0,
            "home_runs": 0,
            "games_total": 0,
            "games_with_statcast": 0,
        }

    game_ids = {int(p.game_id) for p in predictions}
    ingested = games_with_statcast(session, game_ids)

    now = datetime.now(timezone.utc)
    resolved = 0
    unresolved = 0
    changed = 0
    home_runs = 0
    # Cache per (game_id, player_id) so a duplicated key across model versions
    # is only queried once. Doubleheaders differ by game_id, so the cache key
    # keeps them separate by construction.
    label_cache: Dict[Tuple[int, int], int] = {}

    for pred in predictions:
        gid = int(pred.game_id)
        if gid not in ingested:
            unresolved += 1
            continue
        key = (gid, int(pred.player_id))
        if key not in label_cache:
            label_cache[key] = int(
                label_for_target_game(session, int(pred.player_id), gid)
            )
        label = label_cache[key]
        if pred.actual_hr != label or pred.resolution_source != RESOLUTION_SOURCE:
            changed += 1
        pred.actual_hr = label
        pred.resolved_at = now
        pred.resolution_source = RESOLUTION_SOURCE
        resolved += 1
        home_runs += label

    session.flush()

    return {
        "status": "ok" if resolved else "awaiting_statcast",
        "game_date": str(game_date),
        "model_version": model_version,
        "predictions": len(predictions),
        "resolved": resolved,
        "unresolved": unresolved,
        "updated": changed,
        "home_runs": home_runs,
        "games_total": len(game_ids),
        "games_with_statcast": len(ingested & game_ids),
        "resolution_source": RESOLUTION_SOURCE,
        "resolved_at": now.isoformat(),
    }


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def _log_loss(pairs: Sequence[Tuple[float, int]]) -> Optional[float]:
    if not pairs:
        return None
    total = 0.0
    for prob, label in pairs:
        p = min(max(float(prob), _EPS), 1.0 - _EPS)
        total += -(label * math.log(p) + (1 - label) * math.log(1.0 - p))
    return total / len(pairs)


def _brier(pairs: Sequence[Tuple[float, int]]) -> Optional[float]:
    if not pairs:
        return None
    return sum((float(p) - label) ** 2 for p, label in pairs) / len(pairs)


def _auc(pairs: Sequence[Tuple[float, int]]) -> Optional[float]:
    """Rank-based ROC AUC with tie handling.

    Returns ``None`` when only one outcome class is present — AUC is undefined
    there and a fabricated 0.5 would misrepresent the result.
    """
    positives = [p for p, y in pairs if y == 1]
    negatives = [p for p, y in pairs if y == 0]
    if not positives or not negatives:
        return None
    ordered = sorted(pairs, key=lambda item: item[0])
    ranks: List[float] = [0.0] * len(ordered)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    positive_rank_sum = sum(r for r, (_, y) in zip(ranks, ordered) if y == 1)
    n_pos, n_neg = len(positives), len(negatives)
    return (positive_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _calibration_deciles(pairs: Sequence[Tuple[float, int]]) -> List[Dict[str, Any]]:
    """Fixed-width probability bins (0-10%, 10-20%, ...) with observed rates.

    Fixed width rather than equal-count so a tiny sample produces honest, empty
    bins instead of pretending to have ten populated deciles.
    """
    bins: List[Dict[str, Any]] = []
    for idx in range(10):
        low, high = idx / 10.0, (idx + 1) / 10.0
        if idx == 9:
            members = [(p, y) for p, y in pairs if low <= p <= 1.0]
        else:
            members = [(p, y) for p, y in pairs if low <= p < high]
        count = len(members)
        bins.append(
            {
                "bin": f"{low:.1f}-{high:.1f}",
                "lower": round(low, 2),
                "upper": round(high, 2),
                "count": count,
                "mean_predicted": round(sum(p for p, _ in members) / count, 6)
                if count
                else None,
                "observed_rate": round(sum(y for _, y in members) / count, 6)
                if count
                else None,
                "home_runs": sum(y for _, y in members),
            }
        )
    return bins


def _top_n_rate(pairs: Sequence[Tuple[float, int]], n: int) -> Dict[str, Any]:
    """Hit rate among the n highest-probability resolved predictions."""
    if not pairs:
        return {"n": n, "evaluated": 0, "home_runs": 0, "hit_rate": None}
    ordered = sorted(pairs, key=lambda item: item[0], reverse=True)[:n]
    hrs = sum(y for _, y in ordered)
    return {
        "n": n,
        "evaluated": len(ordered),
        "home_runs": hrs,
        "hit_rate": round(hrs / len(ordered), 6),
    }


def performance(
    session: Session,
    start: date,
    end: date,
    model_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only realized performance over resolved predictions in a window."""
    D = models.DailyPrediction
    where = [
        D.game_date >= start,
        D.game_date <= end,
        D.actual_hr.isnot(None),
    ]
    if model_version:
        where.append(D.model_version == model_version)

    rows = session.execute(
        select(D.hr_probability, D.actual_hr, D.model_version, D.game_date).where(*where)
    ).all()

    unresolved = session.execute(
        select(func.count(D.id)).where(
            D.game_date >= start,
            D.game_date <= end,
            D.actual_hr.is_(None),
            *( [D.model_version == model_version] if model_version else [] ),
        )
    ).scalar()

    pairs: List[Tuple[float, int]] = [
        (float(prob), int(label)) for prob, label, _, _ in rows
    ]
    versions = sorted({str(v) for _, _, v, _ in rows})
    dates = sorted({d for _, _, _, d in rows})
    total = len(pairs)
    home_runs = sum(y for _, y in pairs)

    if not total:
        return {
            "status": "no_resolved_predictions",
            "period": {"start": str(start), "end": str(end)},
            "model_version": model_version,
            "model_versions": [],
            "resolved_predictions": 0,
            "unresolved_predictions": int(unresolved or 0),
            "metrics": None,
        }

    base_rate = home_runs / total
    return {
        "status": "ok",
        "period": {
            "start": str(start),
            "end": str(end),
            "slate_dates_evaluated": [str(d) for d in dates],
        },
        "model_version": model_version or (versions[0] if len(versions) == 1 else None),
        "model_versions": versions,
        "resolved_predictions": total,
        "unresolved_predictions": int(unresolved or 0),
        "metrics": {
            "predictions": total,
            "home_runs": home_runs,
            "base_rate": round(base_rate, 6),
            "mean_predicted_probability": round(
                sum(p for p, _ in pairs) / total, 6
            ),
            "log_loss": round(_log_loss(pairs), 6),
            "brier_score": round(_brier(pairs), 6),
            "auc": (lambda a: round(a, 6) if a is not None else None)(_auc(pairs)),
            "auc_note": None
            if _auc(pairs) is not None
            else "undefined: resolved set contains only one outcome class",
            "top_5": _top_n_rate(pairs, 5),
            "top_10": _top_n_rate(pairs, 10),
            "top_25": _top_n_rate(pairs, 25),
            "calibration_deciles": _calibration_deciles(pairs),
        },
    }
