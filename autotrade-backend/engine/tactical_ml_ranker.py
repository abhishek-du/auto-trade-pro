"""Path F — Layer 2: ML ranker. PLACEHOLDER, NOT A MODEL.

This ships the real interface with no model behind it. `predict_proba` returns a
neutral 0.5 for every signal, which makes the ranking step a documented
pass-through rather than a silent no-op.

Why there is no model yet
-------------------------
Training needs a labelled set: replay historical signals and label 1 when the
target was hit before the stop, inside the strategy's horizon. That labelling
job does not exist, and `xgboost` is not installed. Shipping a model trained on
fabricated labels would be worse than shipping none — the repo already has one
example of that (`tasks/ml_optimizer.py` optimises against a mock target while
its banner claims to be a self-learning optimizer, audit D11-m). Not repeating
it.

When a real model lands: drop `models/tactical_xgb.json` in place, add the
xgboost dependency, and `_load_model()` picks it up with no caller change.
The point-in-time hook for building the labelled set already exists —
`get_latest_candles(..., before=<decision_time>)`.
"""
from __future__ import annotations

import os

from engine.tactical_rules import Signal
from utils.logger import logger

MODEL_PATH = "models/tactical_xgb.json"
NEUTRAL_PROBABILITY = 0.5

_model = None
_load_attempted = False
_warned = False

# Feature order the real model will expect. Declared now so the labelling job
# and the serving path cannot drift apart later.
FEATURE_ORDER = (
    "rsi_14", "roc_10", "volume_ratio", "atr_14", "sector_perf_1m",
    "nifty_returns_5d", "vix_level", "fii_net_flow", "delivery_pct", "hour_of_day",
)


def _load_model():
    global _model, _load_attempted
    if _load_attempted:
        return _model
    _load_attempted = True
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        import xgboost as xgb  # noqa: F401  — optional, absent by design today

        booster = xgb.Booster()
        booster.load_model(MODEL_PATH)
        _model = booster
        logger.info(f"[tactical_ml] loaded model from {MODEL_PATH}")
    except ImportError:
        logger.warning(
            f"[tactical_ml] {MODEL_PATH} exists but xgboost is not installed — "
            "falling back to neutral probability"
        )
    except Exception as exc:
        logger.warning(f"[tactical_ml] model load failed ({exc}) — neutral probability")
    return _model


def model_available() -> bool:
    return _load_model() is not None


def predict_proba(signal: Signal, features: dict | None = None) -> float:
    """Probability the signal reaches target before stop. 0.5 when no model.

    Callers MUST treat 0.5 as "unranked", not as a real 50% estimate — see
    `rank_signals`, which degrades to Layer-1 order rather than pretending the
    ML filter ran.
    """
    global _warned
    model = _load_model()
    if model is None:
        if not _warned:
            _warned = True
            # loguru uses {}-style formatting, not %-style — the old form left
            # literal "%s" / "%.2f" in the log line.
            logger.info(
                f"[tactical_ml] no model at {MODEL_PATH} — Layer 2 is a pass-through "
                f"(neutral {NEUTRAL_PROBABILITY:.2f}); ranking falls back to the "
                f"Layer-1 composite score"
            )
        return NEUTRAL_PROBABILITY

    try:
        import numpy as np
        import xgboost as xgb

        feats = features or {}
        row = np.array([[float(feats.get(k, 0.0)) for k in FEATURE_ORDER]])
        return float(model.predict(xgb.DMatrix(row))[0])
    except Exception as exc:
        logger.warning(f"[tactical_ml] prediction failed ({exc}) — neutral")
        return NEUTRAL_PROBABILITY


def rank_signals(
    scored: list[tuple[Signal, float]], *, top_n: int = 5, min_prob: float = 0.55
) -> list[tuple[Signal, float, float]]:
    """Return (signal, composite_score, ml_prob) for the best `top_n`.

    With no model loaded the `min_prob` filter is deliberately NOT applied: a
    neutral 0.5 would fail a 0.55 threshold and silently drop every signal,
    which would look like "the pipeline found nothing" rather than "Layer 2 is
    not built yet". Instead we keep Layer-1's ordering and take the top N.
    """
    if not scored:
        return []

    if not model_available():
        return [(s, sc, NEUTRAL_PROBABILITY) for s, sc in scored[:top_n]]

    ranked = [(s, sc, predict_proba(s, s.meta.get("features"))) for s, sc in scored]
    ranked = [r for r in ranked if r[2] > min_prob]
    ranked.sort(key=lambda r: r[2], reverse=True)
    return ranked[:top_n]
