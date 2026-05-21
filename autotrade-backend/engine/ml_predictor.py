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
    from sklearn.preprocessing import MinMaxScaler, StandardScaler
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import accuracy_score
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

_model_cache:    dict[str, Any] = {}   # LSTM models
_scaler_cache:   dict[str, Any] = {}   # LSTM scalers
_rf_model_cache: dict[str, Any] = {}   # RF models
_rf_scaler_cache: dict[str, Any] = {}  # RF StandardScalers


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
    from db.models import Candle
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
            select(Candle)
            .where(
                Candle.symbol    == symbol,
                Candle.timeframe == "1d",
                Candle.timestamp >= cutoff,
            )
            .order_by(Candle.timestamp)
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


# ═════════════════════════════════════════════════════════════════════════════
# Random Forest Signal Classifier (IN-13)
# 50-feature set  |  5-day forward return labels  |  TimeSeriesSplit(5)
# ═════════════════════════════════════════════════════════════════════════════

# ── RF constants ──────────────────────────────────────────────────────────────

_RF_LABEL_THRESHOLD = 0.02   # ±2% 5-day return → UP/DOWN; inside = FLAT
_RF_LABEL_WINDOW    = 5      # forward bars for label
_RF_MIN_TRAIN_ROWS  = 300

_RF_FEATURE_NAMES: list[str] = [
    # Technical (14)
    "rsi", "macd", "macd_histogram", "bb_position", "atr_normalized",
    "stoch_k", "stoch_d", "adx", "plus_di", "minus_di",
    "ema5_ratio", "ema20_ratio", "ema50_ratio", "ema200_ratio",
    # Pattern flags (5)
    "bullish_engulfing", "hammer", "doji", "morning_star", "shooting_star",
    # Volume (3)
    "obv_trend", "volume_ratio", "vwap_position",
    # India-specific (3)
    "fii_net_flow_normalized", "india_vix_normalized", "sector_rs",
    # Calendar effects (8)
    "is_monday", "is_friday", "week_of_month", "days_to_weekly_expiry",
    "is_budget_month", "is_rbi_week", "month_normalized", "quarter",
    # Price action (8)
    "gap_up", "gap_down", "inside_bar", "outside_bar",
    "consecutive_up_days", "consecutive_down_days", "body_size_ratio", "shadow_ratio",
    # Extra momentum (9 → total 50)
    "lag_return_1d", "lag_return_2d", "lag_return_3d",
    "williams_r", "cci", "roc_5", "donchian_position",
    "close_to_high_52w", "close_to_low_52w",
]

assert len(_RF_FEATURE_NAMES) == 50, f"Expected 50 features, got {len(_RF_FEATURE_NAMES)}"


# ── RF indicator helpers ──────────────────────────────────────────────────────

def _stochastic(df: pd.DataFrame, period: int = 14, smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    lo = df["low"].rolling(period,  min_periods=1).min()
    hi = df["high"].rolling(period, min_periods=1).max()
    k  = (100 * (df["close"] - lo) / (hi - lo + 1e-10)).fillna(50)
    d  = k.rolling(smooth, min_periods=1).mean()
    return k, d


def _adx_indicators(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    hi, lo, cl = df["high"], df["low"], df["close"]
    prev_hi = hi.shift(1)
    prev_lo = lo.shift(1)
    prev_cl = cl.shift(1)

    tr = pd.concat([hi - lo, (hi - prev_cl).abs(), (lo - prev_cl).abs()], axis=1).max(axis=1)
    up   = hi - prev_hi
    down = prev_lo - lo
    pos_dm = np.where((up > down) & (up > 0), up.values, 0.0)
    neg_dm = np.where((down > up) & (down > 0), down.values, 0.0)

    sm_tr   = pd.Series(pos_dm, index=df.index).ewm(span=period, adjust=False).mean()
    sm_pos  = pd.Series(pos_dm, index=df.index).ewm(span=period, adjust=False).mean()
    sm_neg  = pd.Series(neg_dm, index=df.index).ewm(span=period, adjust=False).mean()
    sm_tr2  = tr.ewm(span=period, adjust=False).mean()

    plus_di  = (100 * sm_pos / (sm_tr2 + 1e-10)).fillna(0)
    minus_di = (100 * sm_neg / (sm_tr2 + 1e-10)).fillna(0)
    dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
    adx_s    = dx.ewm(span=period, adjust=False).mean().fillna(0)
    return adx_s, plus_di, minus_di


def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def _cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period, min_periods=1).mean()
    mad = tp.rolling(period, min_periods=1).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    return ((tp - sma) / (0.015 * mad + 1e-10)).fillna(0)


def _williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi = df["high"].rolling(period, min_periods=1).max()
    lo = df["low"].rolling(period,  min_periods=1).min()
    return -100 * (hi - df["close"]) / (hi - lo + 1e-10)


def _rolling_vwap(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].clip(lower=0)
    return (
        (tp * vol).rolling(period, min_periods=1).sum()
        / vol.rolling(period, min_periods=1).sum()
    )


def _consecutive_moves(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    ret  = close.pct_change().fillna(0).values
    up   = np.zeros(len(ret), dtype=np.float32)
    down = np.zeros(len(ret), dtype=np.float32)
    for i in range(1, len(ret)):
        if ret[i] > 0:
            up[i]   = up[i - 1] + 1
        elif ret[i] < 0:
            down[i] = down[i - 1] + 1
    return pd.Series(up, index=close.index), pd.Series(down, index=close.index)


# ── Candlestick pattern detector ──────────────────────────────────────────────

def _detect_patterns(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Return five binary (0/1 float32) pattern arrays aligned with df."""
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    n       = len(df)
    total   = h - l + 1e-10
    body    = np.abs(c - o)
    low_sh  = np.where(c >= o, o - l, c - l).clip(0)
    up_sh   = np.where(c >= o, h - c, h - o).clip(0)

    # Bullish engulfing
    be = np.zeros(n, dtype=np.float32)
    for i in range(1, n):
        if c[i-1] < o[i-1] and c[i] > o[i] and o[i] < c[i-1] and c[i] > o[i-1]:
            be[i] = 1.0

    # Hammer: small body at top, long lower shadow
    hammer = np.where(
        (body / total < 0.35) & (low_sh / total > 0.55) & (up_sh / total < 0.15),
        1.0, 0.0,
    ).astype(np.float32)

    # Doji: body < 10% of range
    doji = np.where(body / total < 0.10, 1.0, 0.0).astype(np.float32)

    # Morning star: bearish → small-body → bullish, closes above midpoint of bar 1
    ms = np.zeros(n, dtype=np.float32)
    for i in range(2, n):
        if (c[i-2] < o[i-2] and body[i-2] / total[i-2] > 0.40
                and body[i-1] / total[i-1] < 0.30
                and c[i] > o[i]
                and c[i] > (o[i-2] + c[i-2]) / 2):
            ms[i] = 1.0

    # Shooting star: small body at bottom, long upper shadow
    shooting = np.where(
        (body / total < 0.35) & (up_sh / total > 0.55) & (low_sh / total < 0.15),
        1.0, 0.0,
    ).astype(np.float32)

    return {
        "bullish_engulfing": be,
        "hammer":       hammer,
        "doji":         doji,
        "morning_star": ms,
        "shooting_star": shooting,
    }


# ── RF 50-feature matrix builder ──────────────────────────────────────────────

def _build_rf_features(
    df: pd.DataFrame,
    indicators_list: list[dict] | None = None,
) -> np.ndarray:
    """Compute the raw (unscaled) 50-column RF feature matrix.

    Parameters
    ----------
    df              : OHLCV DataFrame.
    indicators_list : optional list of per-bar dicts (same length as df).
                      Recognised keys: fii_net_flow_normalized,
                      india_vix_normalized, sector_rs.

    Returns
    -------
    np.ndarray of shape (len(df), 50), dtype float32.
    """
    n     = len(df)
    close = df["close"]
    vol   = df["volume"]

    # ── Technical (14) ───────────────────────────────────────────────────────
    rsi_s    = _rsi(close) / 100.0
    ml, ms_  = _macd(close)
    mh       = ml - ms_
    macd_n   = (ml / (close + 1e-10)).fillna(0)
    mh_n     = (mh / (close + 1e-10)).fillna(0)
    bb_sma   = close.rolling(20, min_periods=2).mean()
    bb_std   = close.rolling(20, min_periods=2).std().fillna(0)
    bb_pos   = ((close - (bb_sma - 2*bb_std)) / (4*bb_std + 1e-10)).clip(0, 1)
    atr_n    = (_atr(df) / (close + 1e-10)).fillna(0).clip(0, 0.2)
    sk, sd   = _stochastic(df)
    adx_v, pdi, ndi = _adx_indicators(df)
    ema5     = close.ewm(span=5,   adjust=False).mean()
    ema20    = close.ewm(span=20,  adjust=False).mean()
    ema50    = close.ewm(span=50,  adjust=False).mean()
    ema200   = close.ewm(span=200, adjust=False).mean()

    # ── Patterns (5) ─────────────────────────────────────────────────────────
    pats = _detect_patterns(df)

    # ── Volume (3) ───────────────────────────────────────────────────────────
    obv_raw  = _obv(df)
    obv_t    = np.sign(obv_raw.diff(5).fillna(0)).values.astype(np.float32)
    vol_sma  = vol.rolling(20, min_periods=1).mean()
    vol_r    = (vol / (vol_sma + 1e-10)).clip(0, 10)
    vwap_    = _rolling_vwap(df)
    vwap_pos = np.sign(close - vwap_).fillna(0).values.astype(np.float32)

    # ── India-specific (3) — from indicators_list or zero ────────────────────
    fii_n = np.zeros(n, dtype=np.float32)
    vix_n = np.zeros(n, dtype=np.float32)
    sec_r = np.zeros(n, dtype=np.float32)
    if indicators_list:
        for i, d in enumerate(indicators_list[:n]):
            fii_n[i] = float(d.get("fii_net_flow_normalized", 0))
            vix_n[i] = float(d.get("india_vix_normalized",    0))
            sec_r[i] = float(d.get("sector_rs",               0))

    # ── Calendar (8) ─────────────────────────────────────────────────────────
    if hasattr(df.index, "dayofweek"):
        dow_a   = np.array(df.index.dayofweek, dtype=np.int32)
        mon_a   = np.array(df.index.month,     dtype=np.int32)
        day_a   = np.array(df.index.day,       dtype=np.int32)
        is_mon  = (dow_a == 0).astype(np.float32)
        is_fri  = (dow_a == 4).astype(np.float32)
        wom     = np.clip((day_a - 1) // 7 + 1, 1, 4).astype(np.float32)
        # Days to next Thursday (NSE weekly expiry)
        d2exp   = ((3 - dow_a + 7) % 7).astype(np.float32)
        is_bud  = (mon_a == 2).astype(np.float32)
        # RBI MPC: typically 1st week of even months
        is_rbi  = ((mon_a % 2 == 0) & (day_a <= 7)).astype(np.float32)
        mon_n   = (mon_a / 12.0).astype(np.float32)
        qtr     = (np.ceil(mon_a / 3.0) / 4.0).astype(np.float32)
    else:
        is_mon = is_fri = wom = d2exp = is_bud = is_rbi = mon_n = qtr = (
            np.zeros(n, dtype=np.float32)
        )

    # ── Price action (8) ─────────────────────────────────────────────────────
    o_a, h_a, l_a, c_a = (
        df["open"].values, df["high"].values, df["low"].values, df["close"].values
    )
    prev_h = df["high"].shift(1).bfill().values
    prev_l = df["low"].shift(1).bfill().values
    total_ = h_a - l_a + 1e-10
    body_  = np.abs(c_a - o_a)
    lo_sh  = np.where(c_a >= o_a, o_a - l_a, c_a - l_a).clip(0)
    up_sh  = np.where(c_a >= o_a, h_a - c_a, h_a - o_a).clip(0)

    gap_up   = (df["open"] > df["high"].shift(1)).astype(np.float32).values
    gap_dn   = (df["open"] < df["low"].shift(1)).astype(np.float32).values
    inside   = ((h_a < prev_h) & (l_a > prev_l)).astype(np.float32)
    outside  = ((h_a > prev_h) & (l_a < prev_l)).astype(np.float32)
    body_r   = (body_ / total_).astype(np.float32)
    shadow_r = ((lo_sh + up_sh) / total_).astype(np.float32)
    up_d, dn_d = _consecutive_moves(close)
    up_d_n   = (up_d / 10.0).clip(0, 1).values
    dn_d_n   = (dn_d / 10.0).clip(0, 1).values

    # ── Extra momentum (9) ───────────────────────────────────────────────────
    lag1    = close.pct_change(1).fillna(0)
    lag2    = close.pct_change(2).fillna(0)
    lag3    = close.pct_change(3).fillna(0)
    wr_n    = ((_williams_r(df) + 100) / 100.0).clip(0, 1)
    cci_n   = ((_cci(df).clip(-200, 200) + 200) / 400.0)
    roc5    = close.pct_change(5).fillna(0)
    d_hi    = df["high"].rolling(20, min_periods=1).max()
    d_lo    = df["low"].rolling(20,  min_periods=1).min()
    don_pos = ((close - d_lo) / (d_hi - d_lo + 1e-10)).clip(0, 1)
    hi52    = df["high"].rolling(252, min_periods=1).max()
    lo52    = df["low"].rolling(252,  min_periods=1).min()
    to_hi   = ((close / (hi52 + 1e-10)) - 1).clip(-0.5, 0.5)
    to_lo   = ((close / (lo52 + 1e-10)) - 1).clip(-0.5, 0.5)

    # ── Assemble 50-column matrix ─────────────────────────────────────────────
    mat = np.column_stack([
        # Technical (14)
        rsi_s.values, macd_n.values, mh_n.values, bb_pos.values, atr_n.values,
        (sk / 100.0).values, (sd / 100.0).values,
        (adx_v / 100.0).values, (pdi / 100.0).values, (ndi / 100.0).values,
        (close / (ema5  + 1e-10)).fillna(1).values,
        (close / (ema20 + 1e-10)).fillna(1).values,
        (close / (ema50 + 1e-10)).fillna(1).values,
        (close / (ema200+ 1e-10)).fillna(1).values,
        # Patterns (5)
        pats["bullish_engulfing"], pats["hammer"], pats["doji"],
        pats["morning_star"], pats["shooting_star"],
        # Volume (3)
        obv_t, vol_r.values, vwap_pos,
        # India-specific (3)
        fii_n, vix_n, sec_r,
        # Calendar (8)
        is_mon, is_fri, wom / 4.0, d2exp / 7.0,
        is_bud, is_rbi, mon_n, qtr,
        # Price action (8)
        gap_up, gap_dn, inside, outside,
        up_d_n, dn_d_n, body_r, shadow_r,
        # Extra (9)
        lag1.values, lag2.values, lag3.values,
        wr_n.values, cci_n.values, roc5.values,
        don_pos.values, to_hi.values, to_lo.values,
    ]).astype(np.float32)

    return np.nan_to_num(mat, nan=0.0, posinf=0.0, neginf=0.0)


# ── 7. train_random_forest ────────────────────────────────────────────────────

def train_random_forest(
    symbol: str,
    df: pd.DataFrame,
    indicators_list: list | None = None,
) -> dict:
    """Train and persist a Random Forest signal classifier for *symbol*.

    Uses TimeSeriesSplit(5) for cross-validation (preserves time order).
    Labels: UP/DOWN/FLAT based on 5-day forward return (±2% threshold).

    Parameters
    ----------
    symbol          : NSE ticker; used as model file stem.
    df              : OHLCV DataFrame; needs ≥ ``_RF_MIN_TRAIN_ROWS`` rows.
    indicators_list : optional list of per-bar dicts for India-specific features.

    Returns
    -------
    dict: {symbol, accuracy, top_features, trained_at}
    On failure: {symbol, error}
    """
    if not (_SKLEARN_AVAILABLE and _JOBLIB_AVAILABLE):
        return {"symbol": symbol, "error": "scikit-learn / joblib not installed"}

    if len(df) < _RF_MIN_TRAIN_ROWS:
        msg = f"need ≥ {_RF_MIN_TRAIN_ROWS} rows, got {len(df)}"
        logger.warning(f"[train_rf] {symbol}: {msg}")
        return {"symbol": symbol, "error": msg}

    try:
        # ── Features ─────────────────────────────────────────────────────────
        X_raw = _build_rf_features(df, indicators_list)

        # ── 5-day forward return labels ──────────────────────────────────────
        fut = df["close"].pct_change(_RF_LABEL_WINDOW).shift(-_RF_LABEL_WINDOW).fillna(0).values
        y   = np.where(
            fut >  _RF_LABEL_THRESHOLD, 2,       # UP
            np.where(fut < -_RF_LABEL_THRESHOLD, 0, 1)   # DOWN / FLAT
        ).astype(np.int32)

        # Drop tail rows where label is undefined
        X_raw = X_raw[:-_RF_LABEL_WINDOW]
        y     = y[:-_RF_LABEL_WINDOW]

        scaler   = StandardScaler()
        X_scaled = scaler.fit_transform(X_raw)

        # ── TimeSeriesSplit cross-validation (5 folds, no shuffling) ─────────
        tscv      = TimeSeriesSplit(n_splits=5)
        fold_accs: list[float] = []

        for fold, (tr_idx, val_idx) in enumerate(tscv.split(X_scaled)):
            rf_fold = RandomForestClassifier(
                n_estimators=200, max_depth=10, min_samples_split=10,
                class_weight="balanced", random_state=42, n_jobs=-1,
            )
            rf_fold.fit(X_scaled[tr_idx], y[tr_idx])
            acc = accuracy_score(y[val_idx], rf_fold.predict(X_scaled[val_idx]))
            fold_accs.append(acc)
            logger.debug(f"[train_rf] {symbol} fold {fold+1}/5: val_acc={acc:.2%}")

        avg_acc = float(np.mean(fold_accs))
        logger.info(f"[train_rf] {symbol}: avg_cv_acc={avg_acc:.2%}")

        # ── Final model on full dataset ───────────────────────────────────────
        rf_final = RandomForestClassifier(
            n_estimators=200, max_depth=10, min_samples_split=10,
            class_weight="balanced", random_state=42, n_jobs=-1,
        )
        rf_final.fit(X_scaled, y)

        # ── Top 10 features by importance ─────────────────────────────────────
        imp       = rf_final.feature_importances_
        top_idx   = np.argsort(imp)[::-1][:10]
        top_feats = {_RF_FEATURE_NAMES[i]: round(float(imp[i]), 4) for i in top_idx}
        logger.info(f"[train_rf] {symbol} top features: {top_feats}")

        # ── Persist ───────────────────────────────────────────────────────────
        model_path  = _MODEL_DIR / f"{symbol}_rf.pkl"
        scaler_path = _MODEL_DIR / f"{symbol}_rf_scaler.pkl"
        joblib.dump(rf_final, model_path)
        joblib.dump(scaler,   scaler_path)

        _rf_model_cache.pop(symbol, None)
        _rf_scaler_cache.pop(symbol, None)

        logger.info(f"[train_rf] {symbol}: saved → {model_path}")
        return {
            "symbol":       symbol,
            "accuracy":     round(avg_acc, 4),
            "top_features": top_feats,
            "trained_at":   datetime.datetime.utcnow().isoformat(),
        }

    except Exception as exc:
        logger.warning(f"[train_rf] {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc)}


# ── 8. get_rf_score ───────────────────────────────────────────────────────────

def get_rf_score(symbol: str, feature_vector: np.ndarray) -> float:
    """Return the RF score contribution (+15 / -15 / 0) for one bar.

    Parameters
    ----------
    symbol         : NSE ticker; determines which RF model file to load.
    feature_vector : 1-D array of shape (50,).  Raw values — the saved
                     StandardScaler is applied internally before inference.

    Returns
    -------
    +15  if P(UP)   > 0.65
    -15  if P(DOWN) > 0.65
      0  otherwise (FLAT or low-confidence)
    """
    if not (_SKLEARN_AVAILABLE and _JOBLIB_AVAILABLE):
        return 0.0

    model_path  = _MODEL_DIR / f"{symbol}_rf.pkl"
    scaler_path = _MODEL_DIR / f"{symbol}_rf_scaler.pkl"

    if not model_path.exists() or not scaler_path.exists():
        return 0.0

    try:
        if symbol not in _rf_model_cache:
            _rf_model_cache[symbol]  = joblib.load(model_path)
        if symbol not in _rf_scaler_cache:
            _rf_scaler_cache[symbol] = joblib.load(scaler_path)

        rf     = _rf_model_cache[symbol]
        scaler = _rf_scaler_cache[symbol]

        vec   = np.nan_to_num(
            np.asarray(feature_vector, dtype=np.float32).reshape(1, -1),
            nan=0.0, posinf=0.0, neginf=0.0,
        )
        X     = scaler.transform(vec)
        proba = rf.predict_proba(X)[0]

        # Classes are sorted; map label integers to proba slots
        cls_list  = list(rf.classes_)
        up_prob   = float(proba[cls_list.index(2)]) if 2 in cls_list else 0.0
        down_prob = float(proba[cls_list.index(0)]) if 0 in cls_list else 0.0

        if up_prob > 0.65:
            score = 15.0
        elif down_prob > 0.65:
            score = -15.0
        else:
            score = 0.0

        logger.debug(
            f"[get_rf_score] {symbol}: up={up_prob:.2%} down={down_prob:.2%} score={score:+.0f}"
        )
        return score

    except Exception as exc:
        logger.warning(f"[get_rf_score] {symbol}: {exc}")
        _rf_model_cache.pop(symbol, None)
        _rf_scaler_cache.pop(symbol, None)
        return 0.0


# ── 9. get_combined_ml_score ──────────────────────────────────────────────────

def get_combined_ml_score(symbol: str, df: pd.DataFrame) -> float:
    """Consensus score from LSTM + Random Forest.

    Consensus logic
    ---------------
    Both positive  (LSTM +, RF +)  → +15   (strong bullish agreement)
    Both negative  (LSTM −, RF −)  → -15   (strong bearish agreement)
    One is zero                     → other × 0.5  (single model confirmation)
    Conflicting    (one +, one −)   →   0   (no consensus — abstain)

    Returns a float in {−15, −7.5, 0, +7.5, +15}.
    Returns 0 immediately when ENABLE_ML_PREDICTIONS is False.
    """
    from utils.config import settings

    if not settings.ENABLE_ML_PREDICTIONS:
        return 0.0

    lstm_score = get_ml_score(symbol, df)

    rf_score = 0.0
    if (_MODEL_DIR / f"{symbol}_rf.pkl").exists() and len(df) >= 260:
        warmup = df.tail(520).copy()       # extra rows for EMA/ATR warm-up
        fv     = _build_rf_features(warmup, indicators_list=None)
        if fv.shape[0] > 0:
            rf_score = get_rf_score(symbol, fv[-1])

    logger.debug(
        f"[get_combined_ml_score] {symbol}: lstm={lstm_score:+.0f} rf={rf_score:+.0f}"
    )

    # Consensus
    if lstm_score > 0 and rf_score > 0:
        return 15.0
    if lstm_score < 0 and rf_score < 0:
        return -15.0
    if lstm_score == 0 and rf_score == 0:
        return 0.0
    if lstm_score == 0:
        return rf_score * 0.5
    if rf_score == 0:
        return lstm_score * 0.5
    return 0.0      # conflicting signals → no contribution
