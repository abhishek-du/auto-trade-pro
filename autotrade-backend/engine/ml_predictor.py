"""ML Price Predictor — LSTM direction classifier + Random Forest signal classifier.

IN-12: LSTM predicts next-day price direction (UP / DOWN / FLAT).
IN-13: Random Forest classifies trading signal from indicator features.

Both models are lightweight (sklearn + keras/torch optional).  When the
heavy ML libraries are absent the functions return neutral predictions
rather than crashing — the rest of the pipeline continues unchanged.

Public API
----------
predict_lstm_direction(df)         -> LSTMPrediction          (sync, CPU-bound)
predict_rf_signal(indicator_dict)  -> RFPrediction            (sync, CPU-bound)
train_lstm(df)                     -> LSTMModel | None        (sync, CPU-bound)
train_rf(features, labels)         -> RFModel | None          (sync)
MLPredictor.predict(df, indicators) -> MLResult               (async dispatch)
"""

from __future__ import annotations

import asyncio
import math
import os
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from utils.logger import logger

# ── Optional heavy ML imports ─────────────────────────────────────────────────

_SKLEARN_AVAILABLE = False
_KERAS_AVAILABLE   = False

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    _SKLEARN_AVAILABLE = True
except ImportError:
    pass

try:
    # Prefer keras (TF 2.x) but accept standalone keras 3.x
    import keras
    from keras import layers, models
    _KERAS_AVAILABLE = True
except ImportError:
    try:
        from tensorflow import keras
        from tensorflow.keras import layers, models  # type: ignore[no-redef]
        _KERAS_AVAILABLE = True
    except ImportError:
        pass

# ── Model persistence paths ───────────────────────────────────────────────────

_MODEL_DIR = Path(__file__).parent.parent / "models"
_LSTM_PATH = _MODEL_DIR / "lstm_model.keras"
_RF_PATH   = _MODEL_DIR / "rf_model.pkl"
_SCALER_PATH = _MODEL_DIR / "rf_scaler.pkl"

_MODEL_DIR.mkdir(exist_ok=True)

# ── Hyperparameters ───────────────────────────────────────────────────────────

_LSTM_LOOKBACK    = 20    # days of history fed into each LSTM sample
_LSTM_EPOCHS      = 30
_LSTM_BATCH_SIZE  = 32
_RF_ESTIMATORS    = 200
_RF_MAX_DEPTH     = 8
_LABEL_THRESHOLD  = 0.005  # ±0.5% change → FLAT; outside → UP or DOWN


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class LSTMPrediction:
    direction: str          # 'UP' | 'DOWN' | 'FLAT'
    confidence: float       # 0–1
    score: float            # score contribution: UP=+15, DOWN=-15, FLAT=0
    available: bool = True


@dataclass
class RFPrediction:
    signal: str             # 'BUY' | 'SELL' | 'HOLD'
    confidence: float       # 0–1
    score: float            # BUY=+20, SELL=-20, HOLD=0
    feature_importances: dict[str, float] = field(default_factory=dict)
    available: bool = True


@dataclass
class MLResult:
    lstm: LSTMPrediction
    rf:   RFPrediction
    combined_score: float   # sum of both scores, clamped to [-35, +35]


# ── Feature engineering ───────────────────────────────────────────────────────

def _ohlcv_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical features to OHLCV DataFrame for ML input.

    Expects columns: open, high, low, close, volume.
    Returns DataFrame with additional feature columns; original rows intact.
    """
    d = df.copy()
    c = d["close"]

    d["ret_1d"]  = c.pct_change(1)
    d["ret_3d"]  = c.pct_change(3)
    d["ret_5d"]  = c.pct_change(5)
    d["hl_range"] = (d["high"] - d["low"]) / c
    d["gap"]     = (d["open"] - d["close"].shift(1)) / d["close"].shift(1)

    # Moving averages
    d["sma_5"]   = c.rolling(5,  min_periods=1).mean()
    d["sma_20"]  = c.rolling(20, min_periods=1).mean()
    d["price_vs_sma5"]  = c / d["sma_5"]  - 1
    d["price_vs_sma20"] = c / d["sma_20"] - 1

    # RSI (14)
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + gain / (loss + 1e-10))

    # Bollinger %B
    sma20 = c.rolling(20, min_periods=2).mean()
    std20 = c.rolling(20, min_periods=2).std()
    d["bb_pct_b"] = (c - (sma20 - 2 * std20)) / (4 * std20 + 1e-10)

    # Volume change
    d["vol_change"] = d["volume"].pct_change(1).fillna(0)

    d = d.fillna(method="bfill").fillna(0)
    return d


_FEATURE_COLS = [
    "ret_1d", "ret_3d", "ret_5d", "hl_range", "gap",
    "price_vs_sma5", "price_vs_sma20",
    "rsi", "bb_pct_b", "vol_change",
]


def _make_labels(df: pd.DataFrame) -> np.ndarray:
    """Create next-day direction labels: 0=DOWN, 1=FLAT, 2=UP."""
    future_ret = df["close"].pct_change(1).shift(-1).fillna(0).values
    labels = np.where(
        future_ret >  _LABEL_THRESHOLD, 2,
        np.where(future_ret < -_LABEL_THRESHOLD, 0, 1)
    )
    return labels.astype(np.int32)


def _direction_from_label(label: int) -> str:
    return {0: "DOWN", 1: "FLAT", 2: "UP"}.get(label, "FLAT")


# ── LSTM model ────────────────────────────────────────────────────────────────

def _build_lstm(n_features: int) -> "keras.Model":
    inp  = layers.Input(shape=(_LSTM_LOOKBACK, n_features))
    x    = layers.LSTM(64, return_sequences=True)(inp)
    x    = layers.Dropout(0.2)(x)
    x    = layers.LSTM(32)(x)
    x    = layers.Dropout(0.2)(x)
    out  = layers.Dense(3, activation="softmax")(x)
    model = models.Model(inp, out)
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model


def _make_lstm_sequences(
    features: np.ndarray, labels: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for i in range(_LSTM_LOOKBACK, len(features)):
        X.append(features[i - _LSTM_LOOKBACK : i])
        y.append(labels[i])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


def train_lstm(df: pd.DataFrame) -> object | None:
    """Train and persist an LSTM model from *df* (OHLCV DataFrame).

    Returns the trained Keras model, or None when Keras is unavailable
    or the DataFrame is too short (< 200 rows).
    """
    if not _KERAS_AVAILABLE:
        logger.warning("train_lstm: keras/tensorflow not installed — skipping")
        return None

    if len(df) < _LSTM_LOOKBACK + 50:
        logger.warning(f"train_lstm: need ≥ {_LSTM_LOOKBACK + 50} rows, got {len(df)}")
        return None

    feat_df  = _ohlcv_features(df)
    features = feat_df[_FEATURE_COLS].values.astype(np.float32)
    labels   = _make_labels(feat_df)

    X, y = _make_lstm_sequences(features, labels)
    split = int(len(X) * 0.8)
    X_tr, X_val, y_tr, y_val = X[:split], X[split:], y[:split], y[split:]

    model = _build_lstm(len(_FEATURE_COLS))
    model.fit(
        X_tr, y_tr,
        epochs=_LSTM_EPOCHS,
        batch_size=_LSTM_BATCH_SIZE,
        validation_data=(X_val, y_val),
        verbose=0,
    )
    model.save(str(_LSTM_PATH))
    logger.info(f"train_lstm: saved to {_LSTM_PATH}")
    return model


def predict_lstm_direction(df: pd.DataFrame) -> LSTMPrediction:
    """Predict next-day price direction using a trained LSTM.

    Falls back to FLAT/unavailable when the model file is absent.
    """
    if not _KERAS_AVAILABLE:
        return LSTMPrediction(direction="FLAT", confidence=0.0, score=0.0, available=False)

    if not _LSTM_PATH.exists():
        logger.debug("predict_lstm_direction: no saved model — train first")
        return LSTMPrediction(direction="FLAT", confidence=0.0, score=0.0, available=False)

    if len(df) < _LSTM_LOOKBACK:
        return LSTMPrediction(direction="FLAT", confidence=0.0, score=0.0, available=False)

    try:
        model    = models.load_model(str(_LSTM_PATH))
        feat_df  = _ohlcv_features(df)
        features = feat_df[_FEATURE_COLS].values.astype(np.float32)
        seq      = features[-_LSTM_LOOKBACK:][np.newaxis, ...]   # (1, lookback, features)
        probs    = model.predict(seq, verbose=0)[0]               # shape (3,)
        label    = int(np.argmax(probs))
        conf     = float(probs[label])
        direction = _direction_from_label(label)
        score     = 15.0 if direction == "UP" else (-15.0 if direction == "DOWN" else 0.0)
        logger.info(f"LSTM prediction: {direction}  confidence={conf:.2%}  score={score:+.0f}")
        return LSTMPrediction(direction=direction, confidence=conf, score=score)
    except Exception as exc:
        logger.warning(f"predict_lstm_direction: {exc}")
        return LSTMPrediction(direction="FLAT", confidence=0.0, score=0.0, available=False)


# ── Random Forest model ───────────────────────────────────────────────────────

_RF_INDICATOR_FEATURES = [
    "rsi",
    "macd_signal",          # positive = bullish
    "bb_position",          # >1 = above upper, <0 = below lower
    "supertrend_direction", # +1 / -1
    "vwap_score",
    "ichimoku_score",
    "adx",
    "adx_direction",        # +1 bullish / -1 bearish
    "ema_ribbon_state",     # +1 bullish, -1 bearish, 0 neutral
    "volume_ratio",         # volume / 20d avg volume
]


def _encode_rf_features(indicator_dict: dict) -> np.ndarray | None:
    """Convert indicator snapshot dict to RF feature vector.

    Missing keys become 0; string labels are encoded as ±1/0.
    Returns None when fewer than 3 features are non-zero.
    """
    def _get(key: str, default: float = 0.0) -> float:
        v = indicator_dict.get(key, default)
        if isinstance(v, str):
            v = v.upper()
            # Bullish/UP signals → +1; Bearish/DOWN → -1
            if v in ("BUY", "STRONG_BUY", "BULLISH", "UP", "BULLISH_SPREAD"):
                return 1.0
            if v in ("SELL", "STRONG_SELL", "BEARISH", "DOWN", "BEARISH_SPREAD"):
                return -1.0
            return 0.0
        try:
            f = float(v)
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return 0.0

    vec = np.array(
        [_get(k) for k in _RF_INDICATOR_FEATURES],
        dtype=np.float32,
    )
    if np.count_nonzero(vec) < 3:
        return None
    return vec


def train_rf(
    features: np.ndarray,
    labels: np.ndarray,
) -> object | None:
    """Train and persist a Random Forest signal classifier.

    *labels*: array of ints where 0=SELL, 1=HOLD, 2=BUY.
    Returns the fitted classifier, or None when sklearn is unavailable.
    """
    if not _SKLEARN_AVAILABLE:
        logger.warning("train_rf: scikit-learn not installed — skipping")
        return None

    if len(features) < 50:
        logger.warning(f"train_rf: need ≥ 50 samples, got {len(features)}")
        return None

    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(features)

    X_tr, X_val, y_tr, y_val = train_test_split(
        X_scaled, labels, test_size=0.2, random_state=42, stratify=labels
    )
    clf = RandomForestClassifier(
        n_estimators=_RF_ESTIMATORS,
        max_depth=_RF_MAX_DEPTH,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_tr, y_tr)
    acc = accuracy_score(y_val, clf.predict(X_val))
    logger.info(f"train_rf: validation accuracy = {acc:.2%}")

    with open(_RF_PATH, "wb") as f:
        pickle.dump(clf, f)
    with open(_SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)

    logger.info(f"train_rf: saved to {_RF_PATH}")
    return clf


def predict_rf_signal(indicator_dict: dict) -> RFPrediction:
    """Predict BUY/SELL/HOLD from an indicator snapshot dict.

    Returns HOLD/unavailable when the model file is absent.
    """
    if not _SKLEARN_AVAILABLE:
        return RFPrediction(signal="HOLD", confidence=0.0, score=0.0, available=False)

    if not _RF_PATH.exists() or not _SCALER_PATH.exists():
        logger.debug("predict_rf_signal: no saved model — train first")
        return RFPrediction(signal="HOLD", confidence=0.0, score=0.0, available=False)

    vec = _encode_rf_features(indicator_dict)
    if vec is None:
        return RFPrediction(signal="HOLD", confidence=0.0, score=0.0, available=False)

    try:
        with open(_RF_PATH, "rb") as f:
            clf: RandomForestClassifier = pickle.load(f)
        with open(_SCALER_PATH, "rb") as f:
            scaler: StandardScaler = pickle.load(f)

        X       = scaler.transform(vec.reshape(1, -1))
        probs   = clf.predict_proba(X)[0]        # shape (n_classes,)
        label   = int(np.argmax(probs))
        conf    = float(probs[label])
        signal  = {0: "SELL", 1: "HOLD", 2: "BUY"}.get(label, "HOLD")
        score   = 20.0 if signal == "BUY" else (-20.0 if signal == "SELL" else 0.0)

        importances = dict(zip(
            _RF_INDICATOR_FEATURES,
            [round(float(v), 4) for v in clf.feature_importances_],
        ))
        logger.info(f"RF prediction: {signal}  confidence={conf:.2%}  score={score:+.0f}")
        return RFPrediction(
            signal=signal,
            confidence=conf,
            score=score,
            feature_importances=importances,
        )
    except Exception as exc:
        logger.warning(f"predict_rf_signal: {exc}")
        return RFPrediction(signal="HOLD", confidence=0.0, score=0.0, available=False)


# ── Unified predictor ─────────────────────────────────────────────────────────

class MLPredictor:
    """Combines LSTM and Random Forest predictions into a single result.

    Both predictions run synchronously in the default executor so the
    async event loop is never blocked.
    """

    async def predict(
        self,
        df: pd.DataFrame,
        indicator_dict: dict,
    ) -> MLResult:
        loop = asyncio.get_event_loop()

        lstm_pred, rf_pred = await asyncio.gather(
            loop.run_in_executor(None, predict_lstm_direction, df),
            loop.run_in_executor(None, predict_rf_signal, indicator_dict),
        )

        combined = max(-35.0, min(35.0, lstm_pred.score + rf_pred.score))
        return MLResult(lstm=lstm_pred, rf=rf_pred, combined_score=combined)


# Module-level singleton
ml_predictor = MLPredictor()
