"""ML Price Predictor — LSTM direction classifier for NSE stocks.

Per-symbol models: each symbol gets its own LSTM (.h5) and scaler (.pkl).

Architecture
------------
LSTM(64, return_sequences=True) → Dropout(0.2) → BatchNormalization →
LSTM(32) → Dropout(0.2) → Dense(16, relu) → Dense(3, softmax)

3 output classes:  0 = DOWN, 1 = FLAT, 2 = UP
Label threshold:   ±0.5% next-day change

Public API
----------
prepare_features(df, indicators, symbol)  -> np.ndarray    (fits + saves scaler)
build_lstm_model(input_shape)             -> keras.Model
train_model(symbol, df)                   -> dict
predict_direction(symbol, df)             -> dict
get_ml_score(symbol, df)                  -> float
train_all_models(session)                 -> None  (async, for Celery)
"""

from __future__ import annotations

import asyncio
import datetime
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.logger import logger

# ── Optional ML imports ───────────────────────────────────────────────────────

_SKLEARN_AVAILABLE = False
_KERAS_AVAILABLE   = False
_JOBLIB_AVAILABLE  = False

try:
    from sklearn.preprocessing import MinMaxScaler
    _SKLEARN_AVAILABLE = True
except ImportError:
    pass

try:
    import joblib
    _JOBLIB_AVAILABLE = True
except ImportError:
    pass

try:
    from keras.models import Sequential, load_model
    from keras.layers import LSTM, Dense, Dropout, BatchNormalization
    from keras.callbacks import EarlyStopping
    _KERAS_AVAILABLE = True
except ImportError:
    try:
        from tensorflow.keras.models import Sequential, load_model  # type: ignore
        from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization  # type: ignore
        from tensorflow.keras.callbacks import EarlyStopping  # type: ignore
        _KERAS_AVAILABLE = True
    except ImportError:
        pass

# ── Paths ─────────────────────────────────────────────────────────────────────

_MODEL_DIR = Path(__file__).parent.parent / "models"
_MODEL_DIR.mkdir(exist_ok=True)

# ── Hyperparameters ───────────────────────────────────────────────────────────

_SEQUENCE_LENGTH    = 60     # daily bars per input sequence
_LABEL_THRESHOLD    = 0.005  # ±0.5% → FLAT; outside → UP / DOWN
_EPOCHS             = 50
_BATCH_SIZE         = 32
_PATIENCE           = 10     # EarlyStopping patience
_N_FEATURES         = 18
_MODEL_MAX_AGE_DAYS = 7      # skip re-training if model file is newer than this

_MIN_TRAIN_ROWS = _SEQUENCE_LENGTH + 60   # need at least this many bars

# ── In-memory caches (avoid repeated disk I/O) ────────────────────────────────

_model_cache:  dict[str, Any] = {}
_scaler_cache: dict[str, Any] = {}


# ── Low-level indicator helpers ───────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / (loss + 1e-10))


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    line   = ema12 - ema26
    signal = line.ewm(span=9, adjust=False).mean()
    return line, signal


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([hi - lo, (hi - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _week_of_month(dt) -> int:
    return (dt.day - 1) // 7 + 1


# ── Core feature builder (shared between training and prediction) ─────────────

def _compute_raw_features(df: pd.DataFrame, indicators=None) -> np.ndarray:
    """Compute the raw (unscaled) 18-column feature matrix.

    Features
    --------
     1  close_pct_change
     2  open_pct_change
     3  high_pct_change
     4  low_pct_change
     5  log_volume
     6  volume_ratio            (volume / volume_sma_20)
     7  rsi                     (RSI-14, divided by 100)
     8  macd_normalized         (macd_line / close)
     9  bb_position             (%B: 0=lower band, 1=upper band)
    10  atr_normalized          (ATR-14 / close)
    11  ema20_ratio             (close / ema_20)
    12  ema50_ratio             (close / ema_50)
    13  ema200_ratio            (close / ema_200)
    14  day_of_week             (0=Mon … 4=Fri, normalized ÷ 4)
    15  week_of_month           (1–4, normalized ÷ 4)
    16  supertrend_direction    (1=BULLISH, -1=BEARISH → mapped to 0/0.5/1)
    17  macd_signal_normalized  (macd_signal / close)
    18  volume_ratio_change     (pct_change of volume_ratio, clipped ±5)
    """
    close  = df["close"]
    volume = df["volume"]
    n      = len(df)

    # OHLCV pct changes
    close_pct = close.pct_change().fillna(0)
    open_pct  = df["open"].pct_change().fillna(0)
    high_pct  = df["high"].pct_change().fillna(0)
    low_pct   = df["low"].pct_change().fillna(0)

    # Volume
    log_vol      = np.log1p(volume.clip(lower=0))
    vol_sma20    = volume.rolling(20, min_periods=1).mean()
    volume_ratio = (volume / (vol_sma20 + 1e-10)).clip(0, 10)
    vol_ratio_ch = volume_ratio.pct_change().fillna(0).clip(-5, 5)

    # Momentum
    rsi_norm        = _rsi(close) / 100.0
    macd_line, macd_sig = _macd(close)
    macd_norm       = (macd_line    / (close + 1e-10)).fillna(0)
    macd_sig_norm   = (macd_sig     / (close + 1e-10)).fillna(0)

    # Bollinger %B
    bb_sma   = close.rolling(20, min_periods=2).mean()
    bb_std   = close.rolling(20, min_periods=2).std().fillna(0)
    bb_upper = bb_sma + 2 * bb_std
    bb_lower = bb_sma - 2 * bb_std
    bb_width = (bb_upper - bb_lower).clip(lower=1e-10)
    bb_pos   = ((close - bb_lower) / bb_width).clip(0, 1)

    # ATR
    atr_norm = (_atr(df) / (close + 1e-10)).fillna(0).clip(0, 0.2)

    # EMA ratios
    ema20    = close.ewm(span=20,  adjust=False).mean()
    ema50    = close.ewm(span=50,  adjust=False).mean()
    ema200   = close.ewm(span=200, adjust=False).mean()
    ema20_r  = (close / (ema20  + 1e-10)).fillna(1)
    ema50_r  = (close / (ema50  + 1e-10)).fillna(1)
    ema200_r = (close / (ema200 + 1e-10)).fillna(1)

    # Temporal
    if hasattr(df.index, "dayofweek"):
        dow = pd.Series(df.index.dayofweek, index=df.index).clip(0, 4) / 4.0
        wom = pd.Series([_week_of_month(d) for d in df.index], index=df.index).clip(1, 4) / 4.0
    else:
        dow = pd.Series(np.zeros(n), index=df.index)
        wom = pd.Series(np.zeros(n), index=df.index)

    # Supertrend direction: {-1, 0, 1} → {0, 0.5, 1}
    if indicators and "supertrend_direction" in indicators:
        st_raw = indicators["supertrend_direction"]
        if isinstance(st_raw, (int, float)):
            st = pd.Series(float(st_raw), index=df.index)
        else:
            st = pd.Series(st_raw, index=df.index).reindex(df.index).fillna(0)
    else:
        st = pd.Series(0.0, index=df.index)
    supertrend = (st + 1) / 2.0

    return np.column_stack([
        close_pct.values,       # 1
        open_pct.values,        # 2
        high_pct.values,        # 3
        low_pct.values,         # 4
        log_vol.values,         # 5
        volume_ratio.values,    # 6
        rsi_norm.values,        # 7
        macd_norm.values,       # 8
        bb_pos.values,          # 9
        atr_norm.values,        # 10
        ema20_r.values,         # 11
        ema50_r.values,         # 12
        ema200_r.values,        # 13
        dow.values,             # 14
        wom.values,             # 15
        supertrend.values,      # 16
        macd_sig_norm.values,   # 17
        vol_ratio_ch.values,    # 18
    ]).astype(np.float32)


# ── 1. prepare_features ───────────────────────────────────────────────────────

def prepare_features(
    df: pd.DataFrame,
    indicators=None,
    symbol: str | None = None,
) -> np.ndarray:
    """Build and normalize the 18-feature matrix from OHLCV data.

    Fits a MinMaxScaler on the data and — when *symbol* is provided —
    saves it to ``models/{symbol}_scaler.pkl`` for use during inference.

    Parameters
    ----------
    df         : OHLCV DataFrame (columns: open, high, low, close, volume).
    indicators : optional dict; recognized key: ``supertrend_direction``.
    symbol     : when provided, the fitted scaler is persisted to disk.

    Returns
    -------
    np.ndarray of shape (len(df), 18) with all values in [0, 1].
    """
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("scikit-learn is required for prepare_features")
    if not _JOBLIB_AVAILABLE:
        raise RuntimeError("joblib is required for prepare_features")

    raw       = _compute_raw_features(df, indicators)
    scaler    = MinMaxScaler(feature_range=(0, 1))
    normalized = scaler.fit_transform(raw).astype(np.float32)

    if symbol is not None:
        scaler_path = _MODEL_DIR / f"{symbol}_scaler.pkl"
        joblib.dump(scaler, scaler_path)
        logger.info(f"[ml_predictor] scaler saved → {scaler_path}")

    return normalized


# ── 2. build_lstm_model ───────────────────────────────────────────────────────

def build_lstm_model(input_shape: tuple):
    """Construct and compile the LSTM price-direction classifier.

    Parameters
    ----------
    input_shape : (sequence_length, n_features), e.g. (60, 18).

    Returns
    -------
    Compiled Keras Sequential model (3-class softmax output).
    """
    if not _KERAS_AVAILABLE:
        raise RuntimeError("keras/tensorflow is required for build_lstm_model")

    model = Sequential([
        LSTM(64, return_sequences=True, input_shape=input_shape),
        Dropout(0.2),
        BatchNormalization(),
        LSTM(32, return_sequences=False),
        Dropout(0.2),
        Dense(16, activation="relu"),
        Dense(3, activation="softmax"),     # DOWN, FLAT, UP
    ])
    model.compile(
        optimizer="adam",
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


# ── Label construction ────────────────────────────────────────────────────────

def _onehot_labels(df: pd.DataFrame) -> np.ndarray:
    """Return one-hot encoded next-bar direction labels, shape (n, 3)."""
    fut = df["close"].pct_change(1).shift(-1).fillna(0).values
    cls = np.where(fut >  _LABEL_THRESHOLD, 2,
          np.where(fut < -_LABEL_THRESHOLD, 0, 1)).astype(np.int32)
    oh  = np.zeros((len(cls), 3), dtype=np.float32)
    oh[np.arange(len(cls)), cls] = 1.0
    return oh


def _make_sequences(
    features: np.ndarray, labels: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for i in range(_SEQUENCE_LENGTH, len(features)):
        X.append(features[i - _SEQUENCE_LENGTH : i])
        y.append(labels[i])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


# ── 3. train_model ────────────────────────────────────────────────────────────

def train_model(symbol: str, df: pd.DataFrame) -> dict:
    """Train and persist an LSTM model for *symbol*.

    Parameters
    ----------
    symbol : NSE ticker, e.g. ``RELIANCE.NS``.  Used as the file stem.
    df     : OHLCV DataFrame; needs at least ``_MIN_TRAIN_ROWS`` rows.

    Returns
    -------
    dict: {symbol, accuracy, val_accuracy, rows_used, trained_at}
    On failure: {symbol, error}
    """
    if not _KERAS_AVAILABLE:
        return {"symbol": symbol, "error": "keras/tensorflow not installed"}
    if not _SKLEARN_AVAILABLE or not _JOBLIB_AVAILABLE:
        return {"symbol": symbol, "error": "scikit-learn / joblib not installed"}

    if len(df) < _MIN_TRAIN_ROWS:
        msg = f"need ≥ {_MIN_TRAIN_ROWS} rows, got {len(df)}"
        logger.warning(f"[train_model] {symbol}: {msg}")
        return {"symbol": symbol, "error": msg}

    try:
        features = prepare_features(df, indicators=None, symbol=symbol)
        labels   = _onehot_labels(df)
        X, y     = _make_sequences(features, labels)

        split = int(len(X) * 0.8)
        X_tr, X_val = X[:split], X[split:]
        y_tr, y_val = y[:split], y[split:]

        model = build_lstm_model(input_shape=(X_tr.shape[1], X_tr.shape[2]))
        es    = EarlyStopping(
            monitor="val_loss", patience=_PATIENCE,
            restore_best_weights=True, verbose=0,
        )
        hist = model.fit(
            X_tr, y_tr,
            epochs=_EPOCHS,
            batch_size=_BATCH_SIZE,
            validation_data=(X_val, y_val),
            callbacks=[es],
            verbose=0,
        )

        model_path = _MODEL_DIR / f"{symbol}_lstm.h5"
        model.save(str(model_path))

        accuracy     = float(hist.history["accuracy"][-1])
        val_accuracy = float(hist.history["val_accuracy"][-1])

        # Bust cache so next prediction loads the fresh model
        _model_cache.pop(symbol, None)
        _scaler_cache.pop(symbol, None)

        logger.info(
            f"[train_model] {symbol}: saved to {model_path}  "
            f"acc={accuracy:.2%}  val_acc={val_accuracy:.2%}"
        )
        return {
            "symbol":       symbol,
            "accuracy":     round(accuracy, 4),
            "val_accuracy": round(val_accuracy, 4),
            "rows_used":    len(df),
            "trained_at":   datetime.datetime.utcnow().isoformat(),
        }

    except Exception as exc:
        logger.warning(f"[train_model] {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc)}


# ── 4. predict_direction ──────────────────────────────────────────────────────

def predict_direction(symbol: str, df: pd.DataFrame) -> dict:
    """Predict next-day price direction using the per-symbol LSTM.

    Loads model and scaler from disk on first call; results are cached.

    Parameters
    ----------
    symbol : NSE ticker matching saved model/scaler file stems.
    df     : recent OHLCV history; at least ``_SEQUENCE_LENGTH`` rows required.

    Returns
    -------
    dict: {predicted_direction, up_prob, down_prob, flat_prob, confidence, ml_score}

    ml_score
    --------
    +15  if UP probability > 0.60
    -15  if DOWN probability > 0.60
      0  otherwise (FLAT or low-confidence directional call)
    """
    neutral = {
        "predicted_direction": "FLAT",
        "up_prob":    0.0,
        "down_prob":  0.0,
        "flat_prob":  1.0,
        "confidence": 0.0,
        "ml_score":   0.0,
    }

    if not (_KERAS_AVAILABLE and _SKLEARN_AVAILABLE and _JOBLIB_AVAILABLE):
        return {**neutral, "error": "ML libraries not installed"}

    model_path  = _MODEL_DIR / f"{symbol}_lstm.h5"
    scaler_path = _MODEL_DIR / f"{symbol}_scaler.pkl"

    if not model_path.exists() or not scaler_path.exists():
        return {**neutral, "error": "model not trained — call train_model() first"}

    if len(df) < _SEQUENCE_LENGTH:
        return {**neutral, "error": f"need ≥ {_SEQUENCE_LENGTH} rows, got {len(df)}"}

    try:
        if symbol not in _model_cache:
            _model_cache[symbol]  = load_model(str(model_path))
        if symbol not in _scaler_cache:
            _scaler_cache[symbol] = joblib.load(scaler_path)

        model  = _model_cache[symbol]
        scaler = _scaler_cache[symbol]

        # Use extra leading rows for indicator warm-up, then take last 60
        warmup_df = df.tail(_SEQUENCE_LENGTH + 250).copy()
        raw    = _compute_raw_features(warmup_df, indicators=None)
        scaled = scaler.transform(raw)
        seq    = scaled[-_SEQUENCE_LENGTH:][np.newaxis, ...]   # (1, 60, 18)

        probs     = model.predict(seq, verbose=0)[0]           # [DOWN, FLAT, UP]
        down_prob = float(probs[0])
        flat_prob = float(probs[1])
        up_prob   = float(probs[2])

        if up_prob >= down_prob and up_prob >= flat_prob:
            direction  = "UP"
            confidence = up_prob
        elif down_prob >= flat_prob:
            direction  = "DOWN"
            confidence = down_prob
        else:
            direction  = "FLAT"
            confidence = flat_prob

        ml_score = 15.0 if up_prob > 0.60 else (-15.0 if down_prob > 0.60 else 0.0)

        logger.info(
            f"[predict_direction] {symbol}: {direction} "
            f"up={up_prob:.2%}  down={down_prob:.2%}  flat={flat_prob:.2%}  "
            f"ml_score={ml_score:+.0f}"
        )
        return {
            "predicted_direction": direction,
            "up_prob":    round(up_prob,    4),
            "down_prob":  round(down_prob,  4),
            "flat_prob":  round(flat_prob,  4),
            "confidence": round(confidence, 4),
            "ml_score":   ml_score,
        }

    except Exception as exc:
        logger.warning(f"[predict_direction] {symbol}: {exc}")
        _model_cache.pop(symbol, None)
        _scaler_cache.pop(symbol, None)
        return {**neutral, "error": str(exc)}


# ── 5. get_ml_score ───────────────────────────────────────────────────────────

def get_ml_score(symbol: str, df: pd.DataFrame) -> float:
    """Return the ML contribution for the confluence signal engine.

    Returns 0.0 when:
    - ``settings.ENABLE_ML_PREDICTIONS`` is False
    - No trained model file exists for *symbol*
    - Any internal prediction error occurs

    Otherwise delegates to predict_direction() and returns ml_score
    (+15, -15, or 0).
    """
    from utils.config import settings

    if not settings.ENABLE_ML_PREDICTIONS:
        return 0.0

    if not (_MODEL_DIR / f"{symbol}_lstm.h5").exists():
        return 0.0

    result = predict_direction(symbol, df)
    return float(result.get("ml_score", 0.0))


# ── 6. train_all_models (async — for Celery beat) ─────────────────────────────

async def train_all_models(session) -> None:
    """Train LSTM models for all NSE large + mid cap symbols.

    Strategy
    --------
    - Skip a symbol when its model file is less than ``_MODEL_MAX_AGE_DAYS`` days old.
    - Fetch the last 2 years of 1-day candles from the ``ohlcv_candles`` DB table.
    - Run train_model() in the thread-pool executor (CPU-bound, non-blocking).

    Designed to run weekly via Celery beat: Sunday 02:00 IST (Saturday 20:30 UTC).
    """
    from sqlalchemy import select
    from db.models import OHLCVCandle
    from utils.config import settings

    symbols = settings.nse_symbols + settings.nse_mid_symbols
    loop    = asyncio.get_event_loop()
    cutoff  = datetime.datetime.utcnow() - datetime.timedelta(days=730)
    now     = datetime.datetime.utcnow()

    logger.info(f"[train_all_models] Starting for {len(symbols)} symbols")

    for symbol in symbols:
        model_path = _MODEL_DIR / f"{symbol}_lstm.h5"

        if model_path.exists():
            age_days = (now - datetime.datetime.utcfromtimestamp(
                model_path.stat().st_mtime
            )).days
            if age_days < _MODEL_MAX_AGE_DAYS:
                logger.debug(
                    f"[train_all_models] {symbol}: model is {age_days}d old, skipping"
                )
                continue

        rows = (await session.execute(
            select(OHLCVCandle)
            .where(
                OHLCVCandle.symbol    == symbol,
                OHLCVCandle.timeframe == "1d",
                OHLCVCandle.timestamp >= cutoff,
            )
            .order_by(OHLCVCandle.timestamp)
        )).scalars().all()

        if not rows:
            logger.warning(f"[train_all_models] {symbol}: no 1d candles in DB — skipping")
            continue

        df = pd.DataFrame([{
            "open":   r.open,
            "high":   r.high,
            "low":    r.low,
            "close":  r.close,
            "volume": r.volume,
        } for r in rows])

        if len(df) < _MIN_TRAIN_ROWS:
            logger.warning(
                f"[train_all_models] {symbol}: only {len(df)} bars < {_MIN_TRAIN_ROWS} — skipping"
            )
            continue

        result = await loop.run_in_executor(None, train_model, symbol, df)
        if "error" in result:
            logger.warning(f"[train_all_models] {symbol}: {result['error']}")
        else:
            logger.info(
                f"[train_all_models] {symbol}  "
                f"acc={result['accuracy']:.2%}  val_acc={result['val_accuracy']:.2%}  "
                f"rows={result['rows_used']}"
            )

    logger.info("[train_all_models] Complete")
