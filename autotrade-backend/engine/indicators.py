"""Technical indicator calculations for AutoTrade Pro.

Uses TA-Lib when available; falls back to pandas/numpy.
Returns an IndicatorSignals dataclass with pre-classified signal strings
and a composite score in [-100, +100].
"""

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False

from utils.config import settings
from utils.logger import logger


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_last(arr) -> float:
    """Last non-NaN value in array, or math.nan."""
    if arr is None:
        return math.nan
    for v in reversed(arr):
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return float(v)
    return math.nan


def _last_two(arr) -> tuple[float, float]:
    """Return (second-to-last, last) non-NaN values.  math.nan when absent."""
    vals: list[float] = []
    for v in reversed(arr):
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            vals.append(float(v))
            if len(vals) == 2:
                break
    if len(vals) == 2:
        return vals[1], vals[0]   # chronological order: (prev, last)
    if len(vals) == 1:
        return math.nan, vals[0]
    return math.nan, math.nan


# ── Signal classifiers ────────────────────────────────────────────────────────

def _rsi_signal(rsi: float) -> str:
    if math.isnan(rsi):
        return "NEUTRAL"
    if rsi <= 30:
        return "OVERSOLD"
    if rsi >= 70:
        return "OVERBOUGHT"
    return "NEUTRAL"


def _macd_cross(hist_prev: float, hist_last: float) -> str:
    if math.isnan(hist_prev) or math.isnan(hist_last):
        return "NONE"
    if hist_prev < 0 and hist_last >= 0:
        return "BULLISH_CROSS"
    if hist_prev > 0 and hist_last <= 0:
        return "BEARISH_CROSS"
    return "NONE"


def _bb_position(close: float, upper: float, middle: float, lower: float) -> str:
    if any(math.isnan(x) for x in (close, upper, middle, lower)):
        return "MIDDLE"
    band = upper - lower
    if band == 0:
        return "MIDDLE"
    if close > upper:
        return "ABOVE_UPPER"
    if close >= upper - 0.1 * band:
        return "NEAR_UPPER"
    if close <= lower:
        return "BELOW_LOWER"
    if close <= lower + 0.1 * band:
        return "NEAR_LOWER"
    return "MIDDLE"


def _ema_trend(price: float, ema20: float, ema50: float, ema200: float) -> str:
    if any(math.isnan(x) for x in (price, ema20, ema50)):
        return "NEUTRAL"
    above_20 = price > ema20
    above_50 = price > ema50
    if math.isnan(ema200):
        if above_20 and above_50:
            return "BULL"
        if not above_20 and not above_50:
            return "BEAR"
        return "NEUTRAL"
    above_200 = price > ema200
    if above_20 and above_50 and above_200:
        return "STRONG_BULL"
    if above_20 and above_50:
        return "BULL"
    if not above_20 and not above_50 and not above_200:
        return "STRONG_BEAR"
    if not above_20 and not above_50:
        return "BEAR"
    return "NEUTRAL"


def _stoch_signal(k: float, d: float) -> str:
    if math.isnan(k) or math.isnan(d):
        return "NEUTRAL"
    if k < 20 and d < 20:
        return "OVERSOLD"
    if k > 80 and d > 80:
        return "OVERBOUGHT"
    return "NEUTRAL"


# ── Composite score ───────────────────────────────────────────────────────────

def _composite_score(
    rsi: float,
    macd_cross: str,
    bb_pos: str,
    ema_trend: str,
    stoch_k: float,
) -> float:
    """Weighted composite score in [-100, +100]."""
    # RSI component  ±20
    rsi_score = 0.0
    if not math.isnan(rsi):
        rsi_score = max(-20.0, min(20.0, 50.0 - rsi))

    # MACD cross component  ±25
    macd_score = {"BULLISH_CROSS": 25.0, "BEARISH_CROSS": -25.0}.get(macd_cross, 0.0)

    # Bollinger Band position  ±15
    bb_score = {
        "BELOW_LOWER": 15.0,
        "NEAR_LOWER":   8.0,
        "MIDDLE":       0.0,
        "NEAR_UPPER":  -8.0,
        "ABOVE_UPPER": -15.0,
    }.get(bb_pos, 0.0)

    # EMA trend  ±25
    ema_score = {
        "STRONG_BULL":  25.0,
        "BULL":         12.0,
        "NEUTRAL":       0.0,
        "BEAR":        -12.0,
        "STRONG_BEAR": -25.0,
    }.get(ema_trend, 0.0)

    # Stochastic component  ±15
    stoch_score = 0.0
    if not math.isnan(stoch_k):
        stoch_score = max(-15.0, min(15.0, (50.0 - stoch_k) * 15.0 / 30.0))

    return rsi_score + macd_score + bb_score + ema_score + stoch_score


def calculate_supertrend(df: pd.DataFrame, period: int = 7, multiplier: float = 3.0) -> dict:
    """Calculate Supertrend line, direction, and score contribution."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if len(df) < period + 1:
        return {"supertrend": math.nan, "direction": "BEARISH", "score": 0.0}

    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    close = df["close"].astype(float).values

    if TALIB_AVAILABLE:
        atr = talib.ATR(high, low, close, timeperiod=period)
    else:
        prev_close = pd.Series(close).shift(1)
        tr = pd.concat([
            pd.Series(high) - pd.Series(low),
            (pd.Series(high) - prev_close).abs(),
            (pd.Series(low) - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().values

    hl2 = (high + low) / 2
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)

    final_upper = np.full(len(df), np.nan)
    final_lower = np.full(len(df), np.nan)
    supertrend = np.full(len(df), np.nan)

    valid_indices = np.where(~np.isnan(atr))[0]
    if len(valid_indices) == 0:
        return {"supertrend": math.nan, "direction": "BEARISH", "score": 0.0}

    start = int(valid_indices[0])
    final_upper[start] = upper_band[start]
    final_lower[start] = lower_band[start]
    supertrend[start] = final_upper[start] if close[start] <= final_upper[start] else final_lower[start]

    for i in range(start + 1, len(df)):
        if upper_band[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]

        if lower_band[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]

        if supertrend[i - 1] == final_upper[i - 1]:
            if close[i] > final_upper[i]:
                supertrend[i] = final_lower[i]
            else:
                supertrend[i] = final_upper[i]
        elif supertrend[i - 1] == final_lower[i - 1]:
            if close[i] < final_lower[i]:
                supertrend[i] = final_upper[i]
            else:
                supertrend[i] = final_lower[i]
        else:
            supertrend[i] = final_upper[i] if close[i] <= final_upper[i] else final_lower[i]

    value = _safe_last(supertrend)
    if math.isnan(value):
        return {"supertrend": math.nan, "direction": "BEARISH", "score": 0.0}

    direction = "BULLISH" if close[-1] > value else "BEARISH"
    score = 20.0 if direction == "BULLISH" else -20.0

    directions = []
    for i in range(max(start, len(df) - 3), len(df)):
        if not math.isnan(supertrend[i]):
            directions.append("BULLISH" if close[i] > supertrend[i] else "BEARISH")
    if len(directions) >= 2 and directions[-1] != directions[-2]:
        score += 5.0 if direction == "BULLISH" else -5.0

    logger.info(f"Supertrend: {direction} | Line: {value:.2f} | Close: {close[-1]:.2f}")
    return {"supertrend": value, "direction": direction, "score": score}


def calculate_vwap(df: pd.DataFrame) -> dict:
    """VWAP with ±1σ/±2σ bands, reset per trading day (IST).

    Only meaningful on intraday bars (≤30 min interval).  Returns score=0
    with a warning for daily/weekly data or when no timestamp column exists.
    """
    nan = math.nan
    _empty = {
        "vwap": nan, "vwap_upper_1": nan, "vwap_upper_2": nan,
        "vwap_lower_1": nan, "vwap_lower_2": nan,
        "vwap_position": "NEAR_VWAP", "vwap_score": 0.0,
    }

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if "timestamp" not in df.columns:
        logger.debug("calculate_vwap: no timestamp column — VWAP skipped")
        return _empty

    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    ts_ist = ts.dt.tz_convert("Asia/Kolkata")

    # Guard: only meaningful on ≤30-min intraday bars
    if len(ts_ist) >= 2:
        median_min = ts_ist.diff().dropna().median().total_seconds() / 60
        if median_min > 30:
            logger.debug(
                f"calculate_vwap: bar interval ~{median_min:.0f} min — "
                "VWAP is not meaningful on daily/weekly data; score set to 0"
            )
            return _empty

    df["_date"]    = ts_ist.dt.date
    df["_typical"] = (
        df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)
    ) / 3

    # Cumulative VWAP per calendar day — resets at midnight IST (≈ 9:15 AM IST session open)
    vwap_pieces: list[pd.Series] = []
    for _, grp in df.groupby("_date", sort=False):
        tp_vol  = (grp["_typical"] * grp["volume"].astype(float)).cumsum()
        cum_vol = grp["volume"].astype(float).cumsum().replace(0, np.nan)
        vwap_pieces.append(tp_vol / cum_vol)
    df["_vwap"] = pd.concat(vwap_pieces)

    # ±1σ / ±2σ bands using rolling 20-bar deviation
    df["_dev"]     = df["_typical"] - df["_vwap"]
    rolling_std    = df["_dev"].rolling(window=20, min_periods=2).std()
    df["_upper_1"] = df["_vwap"] + rolling_std
    df["_upper_2"] = df["_vwap"] + 2 * rolling_std
    df["_lower_1"] = df["_vwap"] - rolling_std
    df["_lower_2"] = df["_vwap"] - 2 * rolling_std

    last_close   = float(df["close"].iloc[-1])
    last_vwap    = _safe_last(df["_vwap"].values)
    last_upper_1 = _safe_last(df["_upper_1"].values)
    last_upper_2 = _safe_last(df["_upper_2"].values)
    last_lower_1 = _safe_last(df["_lower_1"].values)
    last_lower_2 = _safe_last(df["_lower_2"].values)

    if any(math.isnan(v) for v in (last_vwap, last_upper_1, last_lower_1)):
        return _empty

    # Collapsed bands (zero std) — not enough price spread to classify position
    if last_upper_1 == last_lower_1:
        return {**_empty, "vwap": last_vwap, "vwap_upper_1": last_upper_1,
                "vwap_upper_2": last_upper_2, "vwap_lower_1": last_lower_1,
                "vwap_lower_2": last_lower_2}

    # Position and score
    if last_close >= last_upper_2:
        position, score = "ABOVE_VWAP", -25.0
    elif last_close > last_upper_1:
        position, score = "ABOVE_VWAP", -10.0
    elif last_close <= last_lower_2:
        position, score = "BELOW_VWAP",  25.0
    elif last_close < last_lower_1:
        position, score = "BELOW_VWAP",  15.0
    else:
        position, score = "NEAR_VWAP",    0.0

    logger.info(
        f"VWAP: {last_vwap:.2f}  │  Close: {last_close:.2f}  │  "
        f"±1σ [{last_lower_1:.2f}, {last_upper_1:.2f}]  "
        f"±2σ [{last_lower_2:.2f}, {last_upper_2:.2f}]  │  {position}"
    )
    return {
        "vwap": last_vwap,
        "vwap_upper_1": last_upper_1,
        "vwap_upper_2": last_upper_2,
        "vwap_lower_1": last_lower_1,
        "vwap_lower_2": last_lower_2,
        "vwap_position": position,
        "vwap_score": score,
    }


def calculate_ichimoku(df: pd.DataFrame) -> dict:
    """Ichimoku Cloud with 5-level signal (STRONG_BUY → STRONG_SELL).

    Requires ≥52 bars for Senkou Span B; ≥78 for the cloud at the current bar
    (Senkou B shifted 26 forward). The pd.notna guard catches insufficient data.
    """
    nan = math.nan
    _empty = {
        "ichimoku_tenkan": nan, "ichimoku_kijun": nan,
        "ichimoku_senkou_a": nan, "ichimoku_senkou_b": nan,
        "ichimoku_signal": "NEUTRAL", "ichimoku_score": 0.0,
    }

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if len(df) < 52:
        return _empty

    high_s  = df["high"].astype(float)
    low_s   = df["low"].astype(float)
    close_s = df["close"].astype(float)

    tenkan   = (high_s.rolling(9).max()  + low_s.rolling(9).min())  / 2
    kijun    = (high_s.rolling(26).max() + low_s.rolling(26).min()) / 2
    # .shift(26): senkou_a.iloc[-1] = (tenkan.iloc[-27] + kijun.iloc[-27]) / 2 — current cloud
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high_s.rolling(52).max() + low_s.rolling(52).min()) / 2).shift(26)

    def _iloc_float(s: pd.Series) -> float:
        v = s.iloc[-1]
        return float(v) if pd.notna(v) else nan

    tk_now = _iloc_float(tenkan)
    kj_now = _iloc_float(kijun)
    sa_now = _iloc_float(senkou_a)
    sb_now = _iloc_float(senkou_b)

    if any(math.isnan(v) for v in (tk_now, kj_now, sa_now, sb_now)):
        return _empty

    price_now    = float(close_s.iloc[-1])
    chikou_close = float(close_s.iloc[-27]) if len(close_s) > 26 else price_now
    cloud_top    = max(sa_now, sb_now)
    cloud_bot    = min(sa_now, sb_now)

    bull_conditions = [
        price_now > cloud_top,       # price above cloud
        tk_now > kj_now,             # tenkan above kijun (momentum)
        sa_now > sb_now,             # bullish (green) cloud
        price_now > chikou_close,    # chikou: current close above price 26 bars ago
    ]
    bear_conditions = [
        price_now < cloud_bot,
        tk_now < kj_now,
        sa_now < sb_now,
        price_now < chikou_close,
    ]

    bull_count = sum(bull_conditions)
    bear_count = sum(bear_conditions)

    if   bull_count == 4: signal, score = "STRONG_BUY",   35.0
    elif bull_count >= 3: signal, score = "BUY",           20.0
    elif bear_count == 4: signal, score = "STRONG_SELL",  -35.0
    elif bear_count >= 3: signal, score = "SELL",         -20.0
    else:                 signal, score = "NEUTRAL",        0.0

    logger.info(
        f"Ichimoku: {signal}  │  T: {tk_now:.2f}  K: {kj_now:.2f}  "
        f"Cloud [{cloud_bot:.2f}, {cloud_top:.2f}]  │  Price: {price_now:.2f}"
    )
    return {
        "ichimoku_tenkan": tk_now, "ichimoku_kijun": kj_now,
        "ichimoku_senkou_a": sa_now, "ichimoku_senkou_b": sb_now,
        "ichimoku_signal": signal, "ichimoku_score": score,
    }


def calculate_adx(df: pd.DataFrame) -> dict:
    """ADX trend strength with +DI/-DI directional indicators."""
    nan = math.nan
    _empty = {
        "adx": nan, "adx_plus_di": nan, "adx_minus_di": nan,
        "adx_trend_strength": "NONE", "adx_direction": "BEARISH", "adx_score": 0.0,
    }

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if len(df) < 28:
        return _empty

    high  = df["high"].astype(float).values
    low   = df["low"].astype(float).values
    close = df["close"].astype(float).values

    if TALIB_AVAILABLE:
        adx_arr      = talib.ADX(high, low, close, timeperiod=14)
        plus_di_arr  = talib.PLUS_DI(high, low, close, timeperiod=14)
        minus_di_arr = talib.MINUS_DI(high, low, close, timeperiod=14)
    else:
        s_h, s_l, s_c = pd.Series(high), pd.Series(low), pd.Series(close)
        prev_h = s_h.shift(1).bfill()
        prev_l = s_l.shift(1).bfill()
        prev_c = s_c.shift(1).bfill()

        tr = pd.concat([
            s_h - s_l,
            (s_h - prev_c).abs(),
            (s_l - prev_c).abs(),
        ], axis=1).max(axis=1)
        up, down = s_h - prev_h, prev_l - s_l
        plus_dm  = pd.Series(np.where((up > down) & (up > 0),   up.values,   0.0))
        minus_dm = pd.Series(np.where((down > up) & (down > 0), down.values, 0.0))

        alpha = 1.0 / 14
        smt_tr  = tr.ewm(alpha=alpha, adjust=False).mean()
        smt_pdm = plus_dm.ewm(alpha=alpha, adjust=False).mean()
        smt_mdm = minus_dm.ewm(alpha=alpha, adjust=False).mean()

        plus_di_s  = 100.0 * smt_pdm / smt_tr
        minus_di_s = 100.0 * smt_mdm / smt_tr
        dx_s = (100.0 * (plus_di_s - minus_di_s).abs()
                / (plus_di_s + minus_di_s + 1e-10))
        adx_s = dx_s.ewm(alpha=alpha, adjust=False).mean()

        adx_arr      = adx_s.values
        plus_di_arr  = plus_di_s.values
        minus_di_arr = minus_di_s.values

    adx_val      = _safe_last(adx_arr)
    plus_di_val  = _safe_last(plus_di_arr)
    minus_di_val = _safe_last(minus_di_arr)

    if math.isnan(adx_val):
        return _empty

    if adx_val > 25:
        strength = "STRONG"
    elif adx_val > 15:
        strength = "WEAK"
    else:
        strength = "NONE"

    direction = "BULLISH" if plus_di_val > minus_di_val else "BEARISH"

    if   strength == "STRONG": score = 25.0 if direction == "BULLISH" else -25.0
    elif strength == "WEAK":   score = 12.0 if direction == "BULLISH" else -12.0
    else:                      score = 0.0

    logger.info(
        f"ADX: {adx_val:.1f} ({strength})  │  "
        f"+DI: {plus_di_val:.1f}  -DI: {minus_di_val:.1f}  │  {direction}"
    )
    return {
        "adx": adx_val, "adx_plus_di": plus_di_val, "adx_minus_di": minus_di_val,
        "adx_trend_strength": strength, "adx_direction": direction, "adx_score": score,
    }


_RIBBON_PERIODS = (5, 8, 13, 21, 34, 55, 89, 144)


def calculate_ema_ribbon(df: pd.DataFrame) -> dict:
    """8-period Fibonacci EMA ribbon: spread/compressed state with score."""
    nan = math.nan
    _empty = {
        "ema_ribbon": [nan] * 8, "ema_ribbon_state": "COMPRESSED", "ribbon_score": 0.0,
    }

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if len(df) < 144:
        return _empty

    close = df["close"].astype(float).values
    price = float(close[-1])

    emas: list[float] = []
    for p in _RIBBON_PERIODS:
        if TALIB_AVAILABLE:
            val = _safe_last(talib.EMA(close, timeperiod=p))
        else:
            val = _safe_last(pd.Series(close).ewm(span=p, adjust=False).mean().values)
        emas.append(val)

    if any(math.isnan(e) for e in emas):
        return _empty

    # BULLISH_SPREAD: fastest → slowest strictly decreasing, price above fastest
    is_bullish    = all(emas[i] > emas[i + 1] for i in range(7)) and price > emas[0]
    # BEARISH_SPREAD: fastest → slowest strictly increasing, price below fastest
    is_bearish    = all(emas[i] < emas[i + 1] for i in range(7)) and price < emas[0]
    # COMPRESSED: all 8 EMAs span ≤2% of the median EMA
    ema_range     = max(emas) - min(emas)
    median_ema    = sorted(emas)[3]
    is_compressed = (median_ema != 0) and (ema_range / abs(median_ema) * 100 <= 2.0)

    if   is_bullish:    state, score = "BULLISH_SPREAD",  20.0
    elif is_bearish:    state, score = "BEARISH_SPREAD", -20.0
    elif is_compressed: state, score = "COMPRESSED",      0.0
    else:               state, score = "TRANSITIONAL",    0.0

    logger.info(
        f"EMA Ribbon: {state}  │  "
        f"EMA5={emas[0]:.2f}  EMA21={emas[3]:.2f}  EMA89={emas[6]:.2f}  EMA144={emas[7]:.2f}  │  "
        f"Price={price:.2f}"
    )
    return {"ema_ribbon": emas, "ema_ribbon_state": state, "ribbon_score": score}


# ── Main dataclass ────────────────────────────────────────────────────────────

@dataclass
class IndicatorSignals:
    rsi: float
    rsi_signal: str                   # 'OVERSOLD' | 'OVERBOUGHT' | 'NEUTRAL'

    macd: float
    macd_signal: float
    macd_histogram: float
    macd_cross: str                   # 'BULLISH_CROSS' | 'BEARISH_CROSS' | 'NONE'

    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_position: str                  # 'ABOVE_UPPER'|'NEAR_UPPER'|'MIDDLE'|'NEAR_LOWER'|'BELOW_LOWER'

    ema_20: float
    ema_50: float
    ema_200: float
    ema_trend: str                    # 'STRONG_BULL'|'BULL'|'NEUTRAL'|'BEAR'|'STRONG_BEAR'

    atr: float

    stoch_k: float
    stoch_d: float
    stoch_signal: str                 # 'OVERSOLD' | 'OVERBOUGHT' | 'NEUTRAL'

    supertrend: float
    supertrend_direction: str          # 'BULLISH' | 'BEARISH'
    supertrend_score: float

    vwap: float
    vwap_upper_1: float                # +1 standard deviation band
    vwap_upper_2: float                # +2 standard deviations band
    vwap_lower_1: float                # -1 standard deviation band
    vwap_lower_2: float                # -2 standard deviations band
    vwap_position: str                 # 'ABOVE_VWAP' | 'NEAR_VWAP' | 'BELOW_VWAP'
    vwap_score: float

    ichimoku_tenkan: float
    ichimoku_kijun: float
    ichimoku_senkou_a: float
    ichimoku_senkou_b: float
    ichimoku_signal: str               # 'STRONG_BUY'|'BUY'|'NEUTRAL'|'SELL'|'STRONG_SELL'
    ichimoku_score: float

    adx: float
    adx_plus_di: float
    adx_minus_di: float
    adx_trend_strength: str            # 'STRONG' | 'WEAK' | 'NONE'
    adx_direction: str                 # 'BULLISH' | 'BEARISH'
    adx_score: float

    ema_ribbon: list[float]            # EMA values for periods [5,8,13,21,34,55,89,144]
    ema_ribbon_state: str              # 'BULLISH_SPREAD'|'BEARISH_SPREAD'|'COMPRESSED'|'TRANSITIONAL'

    composite_score: float            # -100 … +100

    upper_circuit_days: int   = 0    # consecutive candles close ≈ day high
    volume_surge:       float = 1.0  # latest vol / 20-candle avg

    def to_dict(self) -> dict:
        """JSON-safe representation — replaces math.nan with None."""
        def _clean(v):
            if isinstance(v, float) and math.isnan(v):
                return None
            if isinstance(v, list):
                return [None if (isinstance(x, float) and math.isnan(x)) else x for x in v]
            return v

        return {k: _clean(v) for k, v in self.__dict__.items()}


# ── Composite-score → signal label (single source of truth) ────────────────────
# Used by the deep-analysis endpoint, the market scanner, and the trade loop so
# every surface (stock detail page, scanner UI, agent) derives the SAME signal
# from the SAME composite_score. Thresholds: ±25 actionable, ±60 strong.

def score_to_signal(score: float) -> str:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return "NEUTRAL"
    if score >= 60:  return "STRONG_BUY"
    if score >= 25:  return "BUY"
    if score >= -25: return "NEUTRAL"
    if score >= -60: return "SELL"
    return "STRONG_SELL"


# ── Momentum-breakout / upper-circuit detector ────────────────────────────────

def _momentum_breakout_score(df: pd.DataFrame) -> tuple[int, float, float]:
    """Detect upper-circuit / all-buy-pressure momentum streaks.

    Returns (uc_days, vol_surge, bonus_score).

    uc_days     — consecutive candles where close ≈ day high AND gain ≥ 0.5%
                  (proxy for NSE upper circuit or strong buy-locked trading)
    vol_surge   — latest volume / 20-candle rolling average
    bonus_score — added to composite score to neutralize the overbought
                  penalties that RSI/BB/Stoch apply to breakout stocks (+0…+75)

    Without this, RSI=90 (−20) + BB_ABOVE_UPPER (−15) + Stoch=95 (−15) = −50
    penalty wipes out EMA/MACD bullish signals on the strongest momentum stocks.
    """
    if len(df) < 3:
        return 0, 1.0, 0.0

    dfc = df.copy()
    dfc.columns = [c.lower() for c in dfc.columns]
    close  = dfc["close"].astype(float).values
    high   = dfc["high"].astype(float).values
    volume = dfc["volume"].astype(float).values

    uc_days = 0
    n = len(close)
    for i in range(n - 1, max(-1, n - 15), -1):
        prev = close[i - 1] if i > 0 else close[i] * 0.995
        if (
            high[i] > 0
            and close[i] >= high[i] * 0.995        # close pinned at top of candle
            and close[i] >= prev * 1.005            # at least 0.5% gain from prev
        ):
            uc_days += 1
        else:
            break

    avg_vol   = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))
    vol_surge = float(volume[-1]) / avg_vol if avg_vol > 0 else 1.0

    if uc_days == 0:
        return 0, round(vol_surge, 2), 0.0

    # Each UC day adds 15 pts (1→+15, 3→+45, 5→+60) to clear the −50 overbought
    # penalty and give a net-positive momentum signal.  Volume confirms conviction.
    base      = min(60.0, uc_days * 15.0)
    vol_bonus = min(15.0, (vol_surge - 1.0) * 6.0) if vol_surge >= 1.5 else 0.0
    return uc_days, round(vol_surge, 2), round(base + vol_bonus, 1)


# ── Edge-case bundle ──────────────────────────────────────────────────────────

def _nan_bundle() -> IndicatorSignals:
    nan = math.nan
    return IndicatorSignals(
        rsi=nan,           rsi_signal="NEUTRAL",
        macd=nan,          macd_signal=nan,    macd_histogram=nan, macd_cross="NONE",
        bb_upper=nan,      bb_middle=nan,      bb_lower=nan,       bb_position="MIDDLE",
        ema_20=nan,        ema_50=nan,         ema_200=nan,        ema_trend="NEUTRAL",
        atr=nan,
        stoch_k=nan,       stoch_d=nan,        stoch_signal="NEUTRAL",
        supertrend=nan,    supertrend_direction="BEARISH", supertrend_score=0.0,
        vwap=nan,          vwap_upper_1=nan,  vwap_upper_2=nan,
        vwap_lower_1=nan,  vwap_lower_2=nan,
        vwap_position="NEAR_VWAP",             vwap_score=0.0,
        ichimoku_tenkan=nan,   ichimoku_kijun=nan,
        ichimoku_senkou_a=nan, ichimoku_senkou_b=nan,
        ichimoku_signal="NEUTRAL",             ichimoku_score=0.0,
        adx=nan,           adx_plus_di=nan,   adx_minus_di=nan,
        adx_trend_strength="NONE", adx_direction="BEARISH", adx_score=0.0,
        ema_ribbon=[nan] * 8,  ema_ribbon_state="COMPRESSED",
        composite_score=0.0,
    )


# ── Core computation ──────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> IndicatorSignals:
    """Compute all indicators from an OHLCV DataFrame.

    Expects columns: open, high, low, close, volume (case-insensitive).
    Returns _nan_bundle() when fewer than 5 rows are supplied.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if len(df) < 5:
        return _nan_bundle()

    close = df["close"].values.astype(np.float64)
    high  = df["high"].values.astype(np.float64)
    low   = df["low"].values.astype(np.float64)

    # ── RSI ────────────────────────────────────────────────────────────────────
    if TALIB_AVAILABLE:
        rsi_arr = talib.RSI(close, timeperiod=14)
    else:
        delta = pd.Series(close).diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi_arr = (100 - 100 / (1 + rs)).values

    rsi = _safe_last(rsi_arr)

    # ── MACD ───────────────────────────────────────────────────────────────────
    if TALIB_AVAILABLE:
        macd_arr, sig_arr, hist_arr = talib.MACD(
            close, fastperiod=12, slowperiod=26, signalperiod=9
        )
    else:
        s = pd.Series(close)
        fast     = s.ewm(span=12, adjust=False).mean()
        slow     = s.ewm(span=26, adjust=False).mean()
        macd_s   = fast - slow
        sig_s    = macd_s.ewm(span=9, adjust=False).mean()
        hist_s   = macd_s - sig_s
        macd_arr, sig_arr, hist_arr = macd_s.values, sig_s.values, hist_s.values

    macd_val  = _safe_last(macd_arr)
    macd_sig  = _safe_last(sig_arr)
    hist_prev, hist_last = _last_two(hist_arr)
    cross = _macd_cross(hist_prev, hist_last)

    # ── Bollinger Bands ────────────────────────────────────────────────────────
    if TALIB_AVAILABLE:
        bb_up, bb_mid, bb_lo = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
    else:
        s      = pd.Series(close)
        bb_mid = s.rolling(20).mean()
        bb_std = s.rolling(20).std()
        bb_up  = (bb_mid + 2 * bb_std).values
        bb_lo  = (bb_mid - 2 * bb_std).values
        bb_mid = bb_mid.values

    bbu = _safe_last(bb_up)
    bbm = _safe_last(bb_mid)
    bbl = _safe_last(bb_lo)
    bb_pos = _bb_position(close[-1], bbu, bbm, bbl)

    # ── EMA ────────────────────────────────────────────────────────────────────
    def _ema(period: int) -> float:
        if len(close) < period:
            return math.nan
        if TALIB_AVAILABLE:
            return _safe_last(talib.EMA(close, timeperiod=period))
        return _safe_last(pd.Series(close).ewm(span=period, adjust=False).mean().values)

    e20  = _ema(20)
    e50  = _ema(50)
    e200 = _ema(200)
    trend = _ema_trend(close[-1], e20, e50, e200)

    # ── ATR ────────────────────────────────────────────────────────────────────
    if TALIB_AVAILABLE:
        atr_arr = talib.ATR(high, low, close, timeperiod=14)
    else:
        df_tr    = pd.DataFrame({"high": high, "low": low, "close": close})
        prev_c   = df_tr["close"].shift(1)
        tr       = pd.concat([
            df_tr["high"] - df_tr["low"],
            (df_tr["high"] - prev_c).abs(),
            (df_tr["low"]  - prev_c).abs(),
        ], axis=1).max(axis=1)
        atr_arr  = tr.rolling(14).mean().values

    atr = _safe_last(atr_arr)

    # ── Stochastic ─────────────────────────────────────────────────────────────
    if TALIB_AVAILABLE:
        sk_arr, sd_arr = talib.STOCH(
            high, low, close,
            fastk_period=14, slowk_period=3, slowd_period=3,
        )
    else:
        df_st   = pd.DataFrame({"high": high, "low": low, "close": close})
        ll      = df_st["low"].rolling(14).min()
        hh      = df_st["high"].rolling(14).max()
        fk      = 100 * (df_st["close"] - ll) / (hh - ll + 1e-10)
        sk_s    = fk.rolling(3).mean()
        sd_s    = sk_s.rolling(3).mean()
        sk_arr, sd_arr = sk_s.values, sd_s.values

    sk = _safe_last(sk_arr)
    sd = _safe_last(sd_arr)

    # ── Supertrend ────────────────────────────────────────────────────────────
    supertrend = calculate_supertrend(df)

    # ── VWAP ──────────────────────────────────────────────────────────────────
    vwap = calculate_vwap(df)

    # ── Ichimoku Cloud ────────────────────────────────────────────────────────
    ichimoku = calculate_ichimoku(df)

    # ── ADX ───────────────────────────────────────────────────────────────────
    adx = calculate_adx(df)

    # ── EMA Ribbon ────────────────────────────────────────────────────────────
    ribbon = calculate_ema_ribbon(df)

    # ── Composite score ────────────────────────────────────────────────────────
    uc_days, vol_surge, momentum_bonus = _momentum_breakout_score(df)
    score = max(
        -100.0,
        min(100.0,
            _composite_score(rsi, cross, bb_pos, trend, sk)
            + supertrend["score"]
            + vwap["vwap_score"]
            + ichimoku["ichimoku_score"]
            + adx["adx_score"]
            + ribbon["ribbon_score"]
            + momentum_bonus),
    )

    return IndicatorSignals(
        rsi=rsi,               rsi_signal=_rsi_signal(rsi),
        macd=macd_val,         macd_signal=macd_sig,  macd_histogram=hist_last,
        macd_cross=cross,
        bb_upper=bbu,          bb_middle=bbm,         bb_lower=bbl,
        bb_position=bb_pos,
        ema_20=e20,            ema_50=e50,            ema_200=e200,
        ema_trend=trend,
        atr=atr,
        stoch_k=sk,            stoch_d=sd,            stoch_signal=_stoch_signal(sk, sd),
        supertrend=supertrend["supertrend"],
        supertrend_direction=supertrend["direction"],
        supertrend_score=supertrend["score"],
        vwap=vwap["vwap"],
        vwap_upper_1=vwap["vwap_upper_1"],
        vwap_upper_2=vwap["vwap_upper_2"],
        vwap_lower_1=vwap["vwap_lower_1"],
        vwap_lower_2=vwap["vwap_lower_2"],
        vwap_position=vwap["vwap_position"],
        vwap_score=vwap["vwap_score"],
        ichimoku_tenkan=ichimoku["ichimoku_tenkan"],
        ichimoku_kijun=ichimoku["ichimoku_kijun"],
        ichimoku_senkou_a=ichimoku["ichimoku_senkou_a"],
        ichimoku_senkou_b=ichimoku["ichimoku_senkou_b"],
        ichimoku_signal=ichimoku["ichimoku_signal"],
        ichimoku_score=ichimoku["ichimoku_score"],
        adx=adx["adx"],
        adx_plus_di=adx["adx_plus_di"],
        adx_minus_di=adx["adx_minus_di"],
        adx_trend_strength=adx["adx_trend_strength"],
        adx_direction=adx["adx_direction"],
        adx_score=adx["adx_score"],
        ema_ribbon=ribbon["ema_ribbon"],
        ema_ribbon_state=ribbon["ema_ribbon_state"],
        composite_score=score,
        upper_circuit_days=uc_days,
        volume_surge=vol_surge,
    )


# ── Stop-loss / take-profit helpers ──────────────────────────────────────────

def suggest_stop_loss(entry_price: float, direction: str, atr: float) -> float:
    """ATR-based stop: entry ± ATR × ATR_MULTIPLIER."""
    offset = atr * settings.ATR_MULTIPLIER
    return entry_price - offset if direction.upper() == "BUY" else entry_price + offset


def suggest_take_profit(entry_price: float, stop_loss: float, direction: str) -> float:
    """Risk-multiple take-profit: entry ± risk × MIN_RISK_REWARD."""
    risk = abs(entry_price - stop_loss)
    offset = risk * settings.MIN_RISK_REWARD
    return entry_price + offset if direction.upper() == "BUY" else entry_price - offset
