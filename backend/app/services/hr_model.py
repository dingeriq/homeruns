"""V1 calibrated baseline HR model — pure Python, no new dependencies.

Scope is deliberately minimal:

* Inputs are exactly ``feature_builder.FEATURE_NAMES`` — no parallel feature set.
* Training is L2-regularised logistic regression fitted with batch gradient
  descent, followed by Platt scaling on a **time-held-out** tail so the emitted
  probabilities are calibrated rather than raw scores.
* Missing values are median-imputed from the training split only, and every
  imputed slot is reported, so a scored row always states how complete it was.
* The artifact is a JSON document on disk. When no artifact exists, callers get
  ``None`` and must surface ``model_not_available`` — never a fabricated number.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.feature_builder import FEATURE_NAMES, FEATURE_SET_VERSION

logger = logging.getLogger("dingeriq.model")

MODEL_VERSION = "v1-logreg-platt"
BASE_DIR = Path(__file__).resolve().parents[2]  # backend/


def artifact_path() -> Path:
    override = os.environ.get("MODEL_ARTIFACT_PATH", "").strip()
    if override:
        return Path(override)
    return BASE_DIR / "artifacts" / "hr_model_v1.json"


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _median(values: Sequence[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (float(vals[mid - 1]) + float(vals[mid])) / 2.0


def _mean_std(values: Sequence[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 1.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(var)
    return mean, (std if std > 1e-9 else 1.0)


def log_loss(y: Sequence[int], p: Sequence[float]) -> Optional[float]:
    if not y:
        return None
    total = 0.0
    for yi, pi in zip(y, p):
        pi = min(max(pi, 1e-12), 1 - 1e-12)
        total += -(yi * math.log(pi) + (1 - yi) * math.log(1 - pi))
    return round(total / len(y), 6)


def brier(y: Sequence[int], p: Sequence[float]) -> Optional[float]:
    if not y:
        return None
    return round(sum((pi - yi) ** 2 for yi, pi in zip(y, p)) / len(y), 6)


def roc_auc(y: Sequence[int], p: Sequence[float]) -> Optional[float]:
    pos = [pi for yi, pi in zip(y, p) if yi == 1]
    neg = [pi for yi, pi in zip(y, p) if yi == 0]
    if not pos or not neg:
        return None
    ordered = sorted(zip(p, y), key=lambda t: t[0])
    ranks: Dict[int, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    rank_sum = sum(ranks[idx] for idx, (_, yi) in enumerate(ordered) if yi == 1)
    auc = (rank_sum - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))
    return round(auc, 6)


# ---------------------------------------------------------------------------
# Core fitting
# ---------------------------------------------------------------------------

def _fit_logistic(
    x: List[List[float]],
    y: List[int],
    *,
    l2: float = 1.0,
    iterations: int = 600,
    lr: float = 0.5,
) -> Tuple[List[float], float]:
    n = len(x)
    d = len(x[0]) if n else 0
    w = [0.0] * d
    base = sum(y) / n if n else 0.0
    base = min(max(base, 1e-6), 1 - 1e-6)
    b = math.log(base / (1 - base))
    for _ in range(iterations):
        gw = [0.0] * d
        gb = 0.0
        for xi, yi in zip(x, y):
            z = b + sum(wj * xij for wj, xij in zip(w, xi))
            err = _sigmoid(z) - yi
            gb += err
            for j in range(d):
                gw[j] += err * xi[j]
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j] / n)
        b -= lr * (gb / n)
    return w, b


def _fit_platt(scores: List[float], y: List[int]) -> Tuple[float, float]:
    """1-D logistic calibration mapping raw logits -> calibrated probability."""
    if not scores or len(set(y)) < 2:
        return 1.0, 0.0
    x = [[s] for s in scores]
    w, b = _fit_logistic(x, y, l2=0.0, iterations=400, lr=0.3)
    return w[0], b


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

def train_model(
    rows: Sequence[Dict[str, Any]],
    *,
    holdout_fraction: float = 0.2,
    min_rows: int = 200,
    l2: float = 1.0,
    iterations: int = 600,
) -> Dict[str, Any]:
    """Fit the V1 model on labelled feature rows sorted by ``game_date``.

    Each row must carry ``hit_hr`` plus the ``FEATURE_NAMES`` keys (values may
    be ``None``). Raises ``ValueError`` when the corpus is too small or has a
    single class — we never ship a degenerate model.
    """
    usable = [r for r in rows if r.get("hit_hr") in (0, 1)]
    usable.sort(key=lambda r: (str(r.get("game_date") or ""), r.get("game_id") or 0))
    if len(usable) < min_rows:
        raise ValueError(
            f"insufficient training rows: {len(usable)} < {min_rows}"
        )
    labels = [int(r["hit_hr"]) for r in usable]
    if len(set(labels)) < 2:
        raise ValueError("training corpus contains a single class")

    split = max(1, int(len(usable) * (1 - holdout_fraction)))
    train_rows, holdout_rows = usable[:split], usable[split:]
    if not holdout_rows or len({int(r["hit_hr"]) for r in holdout_rows}) < 2:
        # Fall back to calibrating on the training split rather than failing.
        holdout_rows = usable

    features = list(FEATURE_NAMES)
    medians: Dict[str, Optional[float]] = {}
    for name in features:
        vals = [
            float(r[name])
            for r in train_rows
            if r.get(name) is not None
        ]
        medians[name] = _median(vals) if vals else 0.0

    def raw_vector(row: Dict[str, Any]) -> Tuple[List[float], int]:
        vec: List[float] = []
        imputed = 0
        for name in features:
            v = row.get(name)
            if v is None:
                imputed += 1
                vec.append(float(medians[name] or 0.0))
            else:
                vec.append(float(v))
        return vec, imputed

    raw_train = [raw_vector(r)[0] for r in train_rows]
    means: List[float] = []
    stds: List[float] = []
    for j in range(len(features)):
        m, s = _mean_std([row[j] for row in raw_train])
        means.append(m)
        stds.append(s)

    def scale(vec: List[float]) -> List[float]:
        return [(v - means[j]) / stds[j] for j, v in enumerate(vec)]

    x_train = [scale(v) for v in raw_train]
    y_train = [int(r["hit_hr"]) for r in train_rows]
    w, b = _fit_logistic(x_train, y_train, l2=l2, iterations=iterations)

    hold_scores = []
    y_hold = []
    for r in holdout_rows:
        vec, _ = raw_vector(r)
        z = b + sum(wj * xj for wj, xj in zip(w, scale(vec)))
        hold_scores.append(z)
        y_hold.append(int(r["hit_hr"]))
    a_cal, b_cal = _fit_platt(hold_scores, y_hold)
    calibrated = [_sigmoid(a_cal * s + b_cal) for s in hold_scores]

    artifact = {
        "model_version": MODEL_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_names": features,
        "medians": medians,
        "means": means,
        "stds": stds,
        "coefficients": w,
        "intercept": b,
        "calibration": {"method": "platt", "a": a_cal, "b": b_cal},
        "n_train": len(train_rows),
        "n_holdout": len(holdout_rows),
        "train_date_range": [
            str(train_rows[0].get("game_date")),
            str(train_rows[-1].get("game_date")),
        ],
        "base_rate": round(sum(y_train) / len(y_train), 6),
        "metrics": {
            "holdout_log_loss": log_loss(y_hold, calibrated),
            "holdout_brier": brier(y_hold, calibrated),
            "holdout_auc": roc_auc(y_hold, calibrated),
            "holdout_base_rate": round(sum(y_hold) / len(y_hold), 6),
        },
    }
    return artifact


def save_artifact(
    artifact: Dict[str, Any], path: Optional[Path] = None, *, persist_db: bool = True
) -> Path:
    """Write the artifact to disk and (best effort) to ``model_artifacts``.

    The filesystem copy stays the fast path; the database row is the durable
    fallback that survives a container redeploy. A database failure never blocks
    the local write.
    """
    target = Path(path) if path else artifact_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    _CACHE["path"] = None  # invalidate
    if persist_db and os.environ.get("MODEL_ARTIFACT_DB_PERSIST", "1") != "0":
        try:
            save_artifact_to_db(artifact)
        except Exception:  # pragma: no cover - DB down must not break training
            logger.exception("could not persist model artifact to database")
    return target


# ---------------------------------------------------------------------------
# Durable artifact storage (database)
# ---------------------------------------------------------------------------

def save_artifact_to_db(artifact: Dict[str, Any], session: Any = None) -> Dict[str, Any]:
    """Upsert ``artifact`` as the single active row in ``model_artifacts``.

    Keyed on ``model_version``; re-saving the same version updates in place.
    Every other row is deactivated so exactly one model is active.
    """
    if session is not None:
        return _save_artifact_to_db(session, artifact)
    from app.database.session import session_scope

    with session_scope() as s:
        return _save_artifact_to_db(s, artifact)


def _save_artifact_to_db(session: Any, artifact: Dict[str, Any]) -> Dict[str, Any]:
    from sqlalchemy import select, update

    from app.database import models

    version = str(artifact.get("model_version") or MODEL_VERSION)
    A = models.ModelArtifact
    session.execute(update(A).values(is_active=False).where(A.is_active.is_(True)))
    row = session.execute(select(A).where(A.model_version == version)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = A(
            model_version=version,
            feature_set_version=str(artifact.get("feature_set_version") or FEATURE_SET_VERSION),
            trained_at=str(artifact.get("trained_at") or ""),
            payload=artifact,
            is_active=True,
            created_at=now,
        )
        session.add(row)
        action = "inserted"
    else:
        row.feature_set_version = str(
            artifact.get("feature_set_version") or FEATURE_SET_VERSION
        )
        row.trained_at = str(artifact.get("trained_at") or "")
        row.payload = artifact
        row.is_active = True
        action = "updated"
    session.flush()
    return {
        "action": action,
        "model_version": version,
        "feature_set_version": row.feature_set_version,
        "trained_at": row.trained_at,
        "is_active": True,
    }


def load_artifact_from_db(session: Any = None) -> Optional[Dict[str, Any]]:
    """Return the active stored artifact payload, or ``None``."""
    try:
        if session is not None:
            return _load_artifact_from_db(session)
        from app.database.session import session_scope

        with session_scope() as s:
            return _load_artifact_from_db(s)
    except Exception:  # pragma: no cover - DB unreachable => no model, not a crash
        logger.exception("could not read model artifact from database")
        return None


def _load_artifact_from_db(session: Any) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select

    from app.database import models

    A = models.ModelArtifact
    row = session.execute(
        select(A).where(A.is_active.is_(True)).order_by(A.id.desc())
    ).scalars().first()
    if row is None or not row.payload:
        return None
    payload = row.payload
    return dict(payload) if isinstance(payload, dict) else None


_CACHE: Dict[str, Any] = {"path": None, "artifact": None, "mtime": None}


def load_artifact(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return the registered artifact: local file first, database fallback.

    Falls through to the durable ``model_artifacts`` row when the local file is
    missing (fresh container) or unreadable. Returns ``None`` only when neither
    source holds a usable artifact — callers must then surface
    ``model_not_available`` rather than invent a probability.
    """
    target = Path(path) if path else artifact_path()
    try:
        mtime: Optional[float] = target.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is not None:
        if _CACHE["path"] == str(target) and _CACHE["mtime"] == mtime:
            return _CACHE["artifact"]
        try:
            artifact = json.loads(target.read_text(encoding="utf-8"))
            _CACHE.update({"path": str(target), "artifact": artifact, "mtime": mtime})
            return artifact
        except Exception:  # corrupt artifact must never crash scoring
            logger.exception("unreadable model artifact at %s — trying database", target)

    _CACHE.update({"path": None, "artifact": None, "mtime": None})
    return load_artifact_from_db()



def model_status(path: Optional[Path] = None) -> Dict[str, Any]:
    artifact = load_artifact(path)
    if artifact is None:
        return {
            "status": "unavailable",
            "reason": "model_not_available: no trained model artifact is registered for scoring.",
            "artifact_path": str(Path(path) if path else artifact_path()),
        }
    return {
        "status": "ok",
        "model_version": artifact.get("model_version"),
        "feature_set_version": artifact.get("feature_set_version"),
        "trained_at": artifact.get("trained_at"),
        "n_train": artifact.get("n_train"),
        "metrics": artifact.get("metrics"),
        "artifact_path": str(Path(path) if path else artifact_path()),
    }


def predict(
    artifact: Dict[str, Any], features: Dict[str, Optional[float]]
) -> Dict[str, Any]:
    """Score one feature dict with a loaded artifact.

    ``confidence`` is the share of model inputs that came from real data (i.e.
    were not median-imputed) — a measured completeness figure, not a guess.
    """
    names: List[str] = artifact["feature_names"]
    medians: Dict[str, float] = artifact["medians"]
    means: List[float] = artifact["means"]
    stds: List[float] = artifact["stds"]
    w: List[float] = artifact["coefficients"]
    b: float = artifact["intercept"]

    imputed: List[str] = []
    z = b
    for j, name in enumerate(names):
        v = features.get(name)
        if v is None:
            imputed.append(name)
            v = float(medians.get(name) or 0.0)
        scaled = (float(v) - means[j]) / (stds[j] or 1.0)
        z += w[j] * scaled

    cal = artifact.get("calibration") or {}
    a = float(cal.get("a", 1.0))
    c = float(cal.get("b", 0.0))
    probability = _sigmoid(a * z + c)
    completeness = 1.0 - (len(imputed) / len(names) if names else 0.0)
    return {
        "hr_probability": round(probability, 6),
        "confidence": round(completeness, 4),
        "raw_score": round(z, 6),
        "model_version": artifact.get("model_version"),
        "imputed_features": imputed,
        "features_used": len(names) - len(imputed),
        "features_total": len(names),
    }


__all__ = [
    "MODEL_VERSION",
    "artifact_path",
    "load_artifact_from_db",
    "save_artifact_to_db",
    "brier",
    "load_artifact",
    "log_loss",
    "model_status",
    "predict",
    "roc_auc",
    "save_artifact",
    "train_model",
]
