"""Self-contained vectorized indicators for the Agent.

Mirrors the reference trading_agent/indicators.py exactly but lives inside
the agent package so the agent cycle has zero runtime import issues.
These are NOT duplicates of engine/indicators.py — they are lightweight,
pure-numpy implementations optimised for hot-loop backtesting.

engine/indicators.py (TA-Lib based) is used for the API signal layer.
These functions are used by engine/agent/* for regime classification
and strategy evaluation, where TA-Lib is not always available.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    rs = (up.ewm(alpha=1 / n, adjust=False).mean() /
          down.ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan))
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    line = ema(close, fast) - ema(close, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [(h - l).abs(), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    m = sma(close, n)
    s = close.rolling(n).std()
    return m + k * s, m, m - k * s


def adx_indicator(df: pd.DataFrame, n: int = 14):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -l.diff()
    plus_dm = pd.Series(
        np.where((up > down) & (up > 0), up, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=df.index
    )
    tr = pd.concat(
        [(h - l).abs(), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), plus_di, minus_di


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0):
    a = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    st = pd.Series(index=df.index, dtype="float64")
    direction = pd.Series(index=df.index, dtype="int8")
    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = upper.iloc[i]
            direction.iloc[i] = -1
            continue
        prev = st.iloc[i - 1]
        if df["close"].iloc[i] > prev:
            st.iloc[i] = max(lower.iloc[i], prev)
            direction.iloc[i] = 1
        else:
            st.iloc[i] = min(upper.iloc[i], prev)
            direction.iloc[i] = -1
    return st, direction


def volume_spike(df: pd.DataFrame, n: int = 20, k: float = 1.5) -> pd.Series:
    avg = df["volume"].rolling(n).mean()
    return df["volume"] > k * avg
