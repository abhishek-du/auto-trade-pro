"""Path F — strategy rules. Pure functions, no I/O.

Each rule takes an oldest-first OHLCV DataFrame plus context and returns a list
of `Signal` (usually 0 or 1). Keeping these pure is what makes them testable
against hand-built frames without a database.

Forming-bar discipline (audit D5)
---------------------------------
Every rule computes indicators via `compute_indicators(df, exclude_forming_bar=True)`
and, where it needs raw price/volume, reads from `closed(df)` — never from
`df.iloc[-1]` directly. The last row of the passed frame may be a bar that has
not printed yet; trading on it means the number you decided on is not the number
that bar finally prints, which is exactly the backtest-vs-live divergence D5
identified.

The one deliberate exception is `entry_price`, which is the *live* price — that
is a real, executable number, not a forming aggregate.

Indicator reuse
---------------
`IndicatorSignals` already provides RSI, MACD, Bollinger bands, ATR, VWAP,
pivot, EMA ribbon and a `patterns` list containing 'Bullish Engulfing'. These
rules consume those rather than recomputing, so there is exactly one RSI
implementation in the trading path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.indicators import compute_indicators
from utils.logger import logger


@dataclass(frozen=True)
class Signal:
    """One tactical trade idea. Immutable — scoring attaches results separately."""

    symbol: str
    side: str  # "BUY" | "SELL"
    entry_price: float
    stop_loss: float
    target: float
    confidence: float  # 0-100, genuinely computed by the rule
    strategy_name: str
    timestamp: datetime
    sub_pipeline: str = "F1"  # F1 | F2 | F3 | F4
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    def is_sane(self) -> bool:
        """Reject arithmetically impossible signals before they go anywhere.

        A rule that emits stop==entry produces a divide-by-zero in sizing and an
        infinite R:R; a stop on the wrong side of entry is a sign-error that
        would read as a huge winner. Catch both here rather than downstream.
        """
        if min(self.entry_price, self.stop_loss, self.target) <= 0:
            return False
        if self.risk_per_unit <= 0:
            return False
        if self.side == "BUY":
            return self.stop_loss < self.entry_price < self.target
        if self.side == "SELL":
            return self.target < self.entry_price < self.stop_loss
        return False


def closed(df: pd.DataFrame) -> pd.DataFrame:
    """The frame minus its still-forming last bar."""
    return df.iloc[:-1] if len(df) > 1 else df


def _vol_surge(df: pd.DataFrame, lookback: int = 20, window: int = 5) -> float:
    """Recent volume vs its trailing average, on CLOSED bars only.

    The trailing mean deliberately excludes the window being measured — using an
    average that contains its own numerator damps the very surge it is meant to
    detect (the flaw audit D5 flagged in `_momentum_breakout_score`).
    """
    d = closed(df)
    if len(d) < lookback + window:
        return 1.0
    recent = float(d["volume"].iloc[-window:].mean())
    base = float(d["volume"].iloc[-(lookback + window) : -window].mean())
    return recent / base if base > 0 else 1.0


def _safe_indicators(df: pd.DataFrame):
    try:
        return compute_indicators(df, exclude_forming_bar=True)
    except Exception as exc:
        logger.debug(f"[tactical_rules] indicator computation failed: {exc}")
        return None


def _conf(base: float, *bonuses: float) -> float:
    """Clamp a computed confidence into 0-100.

    Confidence must be genuinely derived from the setup — the execution gate
    rejects anything not marked CALCULATED, and a hardcoded number would be a
    lie in the audit trail even in shadow mode.
    """
    return float(max(0.0, min(100.0, base + sum(bonuses))))


# ── F1 · Intraday momentum ────────────────────────────────────────────────────

def orb(
    symbol: str,
    df_1m: pd.DataFrame,
    live_price: float,
    orb_start: datetime,
    orb_end: datetime,
    *,
    now: datetime | None = None,
) -> list[Signal]:
    """Opening Range Breakout — break of the 09:15-09:30 range on volume."""
    d = closed(df_1m)
    if len(d) < 25 or live_price <= 0:
        return []

    ts = pd.to_datetime(d["timestamp"])
    window = d[(ts >= pd.Timestamp(orb_start).tz_localize(None)) & (ts < pd.Timestamp(orb_end).tz_localize(None))]
    if len(window) < 5:
        return []

    orb_high = float(window["high"].max())
    orb_low = float(window["low"].min())
    if orb_high <= orb_low <= 0:
        return []

    surge = _vol_surge(df_1m)
    if surge < 1.5:
        return []

    now = now or datetime.now()
    # Confidence scales with how decisively volume confirms the break.
    conf = _conf(55.0, min(25.0, (surge - 1.5) * 20.0))

    if live_price > orb_high * 1.002:
        sig = Signal(symbol, "BUY", live_price, orb_low, live_price * 1.02, conf,
                     "ORB", now, "F1",
                     {"orb_high": orb_high, "orb_low": orb_low, "vol_surge": surge})
        return [sig] if sig.is_sane() else []

    if live_price < orb_low * 0.998:
        sig = Signal(symbol, "SELL", live_price, orb_high, live_price * 0.98, conf,
                     "ORB", now, "F1",
                     {"orb_high": orb_high, "orb_low": orb_low, "vol_surge": surge})
        return [sig] if sig.is_sane() else []

    return []


def vwap_trend(
    symbol: str, df_1m: pd.DataFrame, live_price: float, *, now: datetime | None = None
) -> list[Signal]:
    """Price holding one side of VWAP for 2 consecutive closed minutes."""
    d = closed(df_1m)
    if len(d) < 25 or live_price <= 0:
        return []

    ind = _safe_indicators(df_1m)
    if ind is None or not ind.vwap or np.isnan(ind.vwap) or ind.vwap <= 0:
        return []

    if _vol_surge(df_1m, window=1) < 1.0:
        return []

    last2 = d["close"].iloc[-2:]
    now = now or datetime.now()
    vwap = float(ind.vwap)
    dist = abs(live_price - vwap) / vwap
    conf = _conf(52.0, min(20.0, dist * 400.0))

    if (last2 > vwap).all() and live_price > vwap:
        sig = Signal(symbol, "BUY", live_price, vwap * 0.995, live_price * 1.015,
                     conf, "VWAP", now, "F1", {"vwap": vwap})
        return [sig] if sig.is_sane() else []

    if (last2 < vwap).all() and live_price < vwap:
        sig = Signal(symbol, "SELL", live_price, vwap * 1.005, live_price * 0.985,
                     conf, "VWAP", now, "F1", {"vwap": vwap})
        return [sig] if sig.is_sane() else []

    return []


def gap_and_go(
    symbol: str,
    df_1m: pd.DataFrame,
    df_daily: pd.DataFrame,
    live_price: float,
    *,
    now: datetime | None = None,
) -> list[Signal]:
    """Gap up >1% that keeps going, with short-term RSI confirming."""
    dd = closed(df_daily)
    d1 = closed(df_1m)
    if len(dd) < 2 or len(d1) < 15 or live_price <= 0:
        return []

    prev_close = float(dd["close"].iloc[-1])
    prev_high = float(dd["high"].iloc[-1])
    if prev_close <= 0:
        return []

    session_open = float(d1["open"].iloc[0])
    gap_pct = (session_open - prev_close) / prev_close
    if gap_pct <= 0.01:
        return []

    first15 = d1.iloc[:15]
    if live_price <= float(first15["close"].iloc[-1]):
        return []

    ind = _safe_indicators(df_1m)
    if ind is None or np.isnan(ind.rsi) or ind.rsi <= 60:
        return []

    now = now or datetime.now()
    conf = _conf(58.0, min(20.0, (gap_pct - 0.01) * 400.0), min(12.0, (ind.rsi - 60) * 0.6))
    sig = Signal(symbol, "BUY", live_price, prev_high, live_price * 1.02, conf,
                 "GAP_AND_GO", now, "F1",
                 {"gap_pct": round(gap_pct * 100, 2), "rsi": round(float(ind.rsi), 1)})
    return [sig] if sig.is_sane() else []


def pivot_levels(df_daily: pd.DataFrame) -> dict[str, float] | None:
    """Classic floor-trader pivots from the previous CLOSED daily bar."""
    d = closed(df_daily)
    if len(d) < 1:
        return None
    h, l, c = float(d["high"].iloc[-1]), float(d["low"].iloc[-1]), float(d["close"].iloc[-1])
    if h <= 0 or l <= 0:
        return None
    p = (h + l + c) / 3.0
    return {"P": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "range": h - l}


def pivot_bounce_breakout(
    symbol: str,
    df_1m: pd.DataFrame,
    df_daily: pd.DataFrame,
    live_price: float,
    *,
    now: datetime | None = None,
) -> list[Signal]:
    """Bounce off S1, or a volume-confirmed break of R1."""
    lv = pivot_levels(df_daily)
    d = closed(df_1m)
    if lv is None or len(d) < 25 or live_price <= 0:
        return []

    ind = _safe_indicators(df_1m)
    atr = float(ind.atr) if ind and ind.atr and not np.isnan(ind.atr) else lv["range"] * 0.1
    now = now or datetime.now()
    last = d.iloc[-1]
    bullish_candle = float(last["close"]) > float(last["open"])

    # Bounce: trading near S1 with a bullish closed candle, target the pivot.
    if bullish_candle and lv["S1"] < live_price <= lv["S1"] * 1.005 and lv["P"] > live_price:
        sig = Signal(symbol, "BUY", live_price, lv["S1"] - 0.5 * atr, lv["P"],
                     _conf(54.0), "PIVOT_BOUNCE", now, "F1", {**lv, "atr": atr})
        return [sig] if sig.is_sane() else []

    # Breakout: above R1 on volume, target R2, stop back at the pivot.
    surge = _vol_surge(df_1m)
    if live_price > lv["R1"] and surge >= 1.5 and lv["R2"] > live_price:
        sig = Signal(symbol, "BUY", live_price, lv["P"], lv["R2"],
                     _conf(56.0, min(20.0, (surge - 1.5) * 20.0)),
                     "PIVOT_BREAKOUT", now, "F1", {**lv, "vol_surge": surge})
        return [sig] if sig.is_sane() else []

    return []


def scalp_engulfing(
    symbol: str, df_1m: pd.DataFrame, live_price: float, *, now: datetime | None = None
) -> list[Signal]:
    """Bullish engulfing / piercing on 1-min, tight fixed R:R.

    Reuses the pattern detection already in `IndicatorSignals.patterns` rather
    than adding a second candlestick implementation.
    """
    if len(closed(df_1m)) < 25 or live_price <= 0:
        return []

    ind = _safe_indicators(df_1m)
    if ind is None:
        return []

    pats = {p.lower() for p in (ind.patterns or [])}
    if not any("engulfing" in p or "piercing" in p for p in pats):
        return []

    now = now or datetime.now()
    # Fixed 0.15% stop / 0.3% target, per the brief's scalping spec.
    sig = Signal(symbol, "BUY", live_price, live_price * 0.9985, live_price * 1.003,
                 _conf(51.0), "SCALP", now, "F1", {"patterns": sorted(pats)})
    return [sig] if sig.is_sane() else []


# ── F4 · Mean reversion ───────────────────────────────────────────────────────

def overbought_fade(
    symbol: str, df_5m: pd.DataFrame, live_price: float, *, now: datetime | None = None
) -> list[Signal]:
    """RSI > 70 AND price above the upper Bollinger band → fade to the mean."""
    if len(closed(df_5m)) < 25 or live_price <= 0:
        return []

    ind = _safe_indicators(df_5m)
    if ind is None or np.isnan(ind.rsi) or not ind.bb_upper or not ind.bb_middle:
        return []
    if ind.rsi <= 70 or live_price <= ind.bb_upper:
        return []
    if ind.bb_middle >= live_price:  # mean already above price — no room to fade
        return []

    now = now or datetime.now()
    conf = _conf(53.0, min(22.0, (float(ind.rsi) - 70) * 1.1))
    # The stop must sit ABOVE entry for a short. The brief specifies
    # "upper band + 0.5%", which assumes entry is at the band -- but by the time
    # we fade, price has usually run past it, and band*1.005 can land BELOW
    # entry, i.e. a stop already breached at entry. Take whichever is higher.
    stop = max(float(ind.bb_upper) * 1.005, live_price * 1.005)
    sig = Signal(symbol, "SELL", live_price, stop,
                 float(ind.bb_middle), conf, "OVERBOUGHT_FADE", now, "F4",
                 {"rsi": round(float(ind.rsi), 1), "bb_upper": float(ind.bb_upper)})
    return [sig] if sig.is_sane() else []


def oversold_rebound(
    symbol: str, df_5m: pd.DataFrame, live_price: float, *, now: datetime | None = None
) -> list[Signal]:
    """RSI < 30 AND price below the lower Bollinger band → buy the rebound."""
    if len(closed(df_5m)) < 25 or live_price <= 0:
        return []

    ind = _safe_indicators(df_5m)
    if ind is None or np.isnan(ind.rsi) or not ind.bb_lower or not ind.bb_middle:
        return []
    if ind.rsi >= 30 or live_price >= ind.bb_lower:
        return []
    if ind.bb_middle <= live_price:
        return []

    now = now or datetime.now()
    conf = _conf(53.0, min(22.0, (30 - float(ind.rsi)) * 1.1))
    # Mirror of the overbought case: the stop must sit BELOW entry for a long.
    stop = min(float(ind.bb_lower) * 0.995, live_price * 0.995)
    sig = Signal(symbol, "BUY", live_price, stop,
                 float(ind.bb_middle), conf, "OVERSOLD_REBOUND", now, "F4",
                 {"rsi": round(float(ind.rsi), 1), "bb_lower": float(ind.bb_lower)})
    return [sig] if sig.is_sane() else []


def volume_breakout_5m(
    symbol: str, df_5m: pd.DataFrame, live_price: float, *, now: datetime | None = None
) -> list[Signal]:
    """5m breakout: price clears the 20-bar high on >=1.5x average volume.

    Added 2026-08-20. F4 was mean-reversion only, so a sector trending hard all
    session (the sugar complex on 19-20 Aug) produced no F4 signal at all: fades
    require an overbought RSI *against* the move, which never triggered.

    The 20-bar low is the stop rather than a fixed percentage because it is the
    level that actually invalidates a breakout. That makes risk-per-share
    variable, so `TacticalRiskManager` decides size -- do not add a percentage
    stop here to make the R:R look tidier.
    """
    d = closed(df_5m)
    if len(d) < 25 or live_price <= 0:
        return []

    prior = d.iloc[:-1]                      # exclude the bar that just closed
    hi20 = float(prior["high"].tail(20).max())
    lo20 = float(prior["low"].tail(20).min())
    if not (hi20 > 0 and lo20 > 0) or live_price <= hi20:
        return []

    vol = prior["volume"].tail(20)
    avg_vol = float(vol.mean())
    last_vol = float(d["volume"].iloc[-1])
    if avg_vol <= 0 or last_vol < 1.5 * avg_vol:
        return []

    if lo20 >= live_price:                   # stop must sit below entry
        return []

    now = now or datetime.now()
    rvol = last_vol / avg_vol
    conf = _conf(55.0, min(15.0, (rvol - 1.5) * 6.0),
                 min(10.0, (live_price / hi20 - 1.0) * 500.0))
    # Target is 2% per the brief, but never inside the stop distance -- a 2%
    # target on a 6% stop is a losing proposition by construction.
    target = max(live_price * 1.02, live_price + (live_price - lo20) * 1.2)
    sig = Signal(symbol, "BUY", live_price, lo20, target, conf,
                 "VOLUME_BREAKOUT", now, "F4",
                 {"hi20": round(hi20, 2), "rvol": round(rvol, 2)})
    return [sig] if sig.is_sane() else []


def vwap_crossover_5m(
    symbol: str, df_5m: pd.DataFrame, live_price: float, *, now: datetime | None = None
) -> list[Signal]:
    """5m VWAP reclaim: two consecutive closes above VWAP with RVOL > 1.5.

    Two bars, not one, because a single close above VWAP is noise at the 5m
    scale -- price crosses and re-crosses repeatedly in a chop. Requiring the
    prior bar to have closed above too is what makes this a trend read rather
    than a tick.

    VWAP is computed here from the session's own bars rather than taken from
    `IndicatorSignals.vwap`, so the anchor is unambiguous: the rule needs VWAP
    as of the PREVIOUS bar to judge that bar's close, which the aggregate
    indicator does not expose.
    """
    d = closed(df_5m)
    if len(d) < 6 or live_price <= 0:
        return []

    tp = (d["high"] + d["low"] + d["close"]) / 3.0
    cum_v = d["volume"].cumsum()
    cum_pv = (tp * d["volume"]).cumsum()
    if float(cum_v.iloc[-1]) <= 0:
        return []
    vwap_series = cum_pv / cum_v.replace(0, np.nan)

    vwap_now = float(vwap_series.iloc[-1])
    vwap_prev = float(vwap_series.iloc[-2])
    if not (vwap_now > 0 and vwap_prev > 0):
        return []

    # Two consecutive closes above VWAP, and price still above it now.
    if float(d["close"].iloc[-1]) <= vwap_now:
        return []
    if float(d["close"].iloc[-2]) <= vwap_prev:
        return []
    if live_price <= vwap_now:
        return []

    vol = d["volume"].tail(20)
    avg_vol = float(vol.mean())
    if avg_vol <= 0 or float(d["volume"].iloc[-1]) < 1.5 * avg_vol:
        return []

    now = now or datetime.now()
    rvol = float(d["volume"].iloc[-1]) / avg_vol
    prem = live_price / vwap_now - 1.0
    conf = _conf(52.0, min(14.0, (rvol - 1.5) * 6.0), min(12.0, prem * 400.0))
    # Stop below VWAP (the level being defended), target above -- but both
    # anchored so they stay on the correct side of a price that has already run.
    stop = min(vwap_now * 0.995, live_price * 0.995)
    target = max(vwap_now * 1.015, live_price * 1.005)
    sig = Signal(symbol, "BUY", live_price, stop, target, conf,
                 "VWAP_CROSSOVER", now, "F4",
                 {"vwap": round(vwap_now, 2), "rvol": round(rvol, 2),
                  "premium_pct": round(prem * 100, 2)})
    return [sig] if sig.is_sane() else []


def _cfg(name, default):
    """Settings lookup local to this module (rules are otherwise pure)."""
    from utils.config import settings
    return getattr(settings, name, default)


def _prev_close(df_daily, fallback: float) -> float:
    """Yesterday's close from the daily frame, or `fallback` if unavailable.

    Uses the last daily bar whose close differs from today's forming bar. The
    daily frame may or may not already carry a bar for today depending on when
    the backfill ran, so the second-to-last bar is taken when the frame is long
    enough and the last one otherwise.
    """
    try:
        if df_daily is None or len(df_daily) == 0:
            return fallback
        closes = df_daily["close"].astype(float)

        # Whether the frame's last bar is TODAY decides which row is "previous
        # close", and it is NOT safe to assume. Measured 2026-08-21: the daily
        # backfill was two sessions behind (newest bar 19 Aug), so a blind
        # iloc[-2] reached back to 18 Aug and reported HSCL down 12.78% when its
        # real day move was -7.07%. Read the date instead of guessing.
        ref = float(closes.iloc[-1])
        if "timestamp" in df_daily.columns and len(closes) >= 2:
            import pandas as _pd

            last_ts = _pd.to_datetime(df_daily["timestamp"].iloc[-1])
            if last_ts.date() >= _pd.Timestamp.utcnow().date():
                ref = float(closes.iloc[-2])       # last bar IS today
        return ref if ref > 0 else fallback
    except Exception:
        return fallback


def day_momentum(
    symbol: str, df_1m: pd.DataFrame, df_daily: pd.DataFrame, live_price: float,
    *, now: datetime | None = None,
) -> list[Signal]:
    """Pure trend capture — no chart pattern required.

    WHY THIS EXISTS (2026-08-21)
    ----------------------------
    Every other F1 rule needs a SHAPE: an opening-range break, a gap, a pivot
    touch, an engulfing candle. A stock that simply grinds up all session on
    heavy volume matches none of them. Measured on the 21-Aug session: of 29
    stocks that cleared volume + intraday-momentum + VWAP screens, F1's existing
    rules fired on exactly ONE (NETWEB, and only SCALP). NCC +6.9% on 13.2x
    volume, JINDALSAW +7.8% on 17.5x, THOMASCOOK +12.1%, MANINDS +6.1% -- all
    invisible, with good data and fresh bars.

    This rule measures the move itself rather than its shape:
      1. RVOL >= TACTICAL_DAY_MOM_MIN_RVOL      (default 2.0)
      2. price in the top 30% of the day's range (holding its highs)
      3. price >= 0.5% above session VWAP        (above the average buyer)
      4. day gain >= TACTICAL_DAY_MOM_MIN_GAIN_PCT (default 2.0)

    All four must hold. Individually each is common; together they describe a
    stock being accumulated, which is the thing the pattern rules keep missing.

    RVOL uses the 20 CLOSED daily bars before today, so today's own volume is
    never part of its own baseline -- the flaw audit D5 flagged elsewhere.
    """
    d = closed(df_1m)
    if len(d) < 20 or live_price <= 0 or df_daily is None or len(df_daily) < 11:
        return []

    day_high = float(d["high"].max())
    day_low = float(d["low"].min())
    day_open = float(d["open"].iloc[0])
    rng = day_high - day_low
    if rng <= 0 or day_open <= 0:
        return []

    # 1. Relative volume against the trailing 20 CLOSED sessions.
    prior = df_daily.iloc[:-1] if len(df_daily) > 1 else df_daily
    avg_vol = float(prior["volume"].tail(20).mean())
    vol_today = float(d["volume"].sum())
    if avg_vol <= 0:
        return []
    rvol = vol_today / avg_vol
    if rvol < float(_cfg("TACTICAL_DAY_MOM_MIN_RVOL", 2.0)):
        return []

    # 2. Holding the top of the day's range.
    range_pos = (live_price - day_low) / rng
    if range_pos < float(_cfg("TACTICAL_DAY_MOM_MIN_RANGE_POS", 0.70)):
        return []

    # 3. Above session VWAP. Computed from today's bars rather than taken from
    # IndicatorSignals: this needs the SESSION anchor, and the indicator's VWAP
    # is over whatever window the frame happens to hold.
    tp = (d["high"] + d["low"] + d["close"]) / 3.0
    vol = d["volume"]
    if float(vol.sum()) <= 0:
        return []
    vwap = float((tp * vol).sum() / vol.sum())
    if vwap <= 0 or live_price < vwap * 1.005:
        return []

    # 4. A real move, not drift.
    # Measured against PREVIOUS CLOSE, not the frame's first bar.
    #
    # The executor fetches only the last 200 one-minute bars while an NSE
    # session is 375 minutes, so `df_1m.open.iloc[0]` is the open of a bar
    # around midday — not the day's open. Measured 2026-08-21: that made
    # BALRAMCHIN read -0.32% when its real intraday move was -2.41%, and Kite's
    # own day change was -4.92%. Every gain/loss gate here was scoring a partial
    # window. Previous close also folds in the opening gap, which is the number
    # a trader actually means by "up 3% today"; the range-position gate above
    # still rejects a gap that has since faded.
    ref = _prev_close(df_daily, day_open)
    gain_pct = (live_price / ref - 1.0) * 100.0
    if gain_pct < float(_cfg("TACTICAL_DAY_MOM_MIN_GAIN_PCT", 2.0)):
        return []

    now = now or datetime.now()
    ind = _safe_indicators(df_1m)
    atr = float(ind.atr) if ind is not None and ind.atr and not np.isnan(ind.atr) else 0.0

    # Stop: the wider of 1.5 ATR and 1% -- a 1% stop on a name that has already
    # run 12% is inside the noise and would be taken out on any pullback.
    stop = live_price - max(atr * 1.5, live_price * 0.01)
    if stop <= 0 or stop >= live_price:
        return []
    # Target floored at 2R so the trade cannot be structurally negative-expectancy,
    # and separately at 1.5% so a tight-ATR name still has room worth trading.
    risk = live_price - stop
    target = live_price + max(risk * 2.0, live_price * 0.015)

    conf = _conf(70.0, min(10.0, (rvol - 2.0) * 2.0), min(8.0, (range_pos - 0.7) * 26.0))
    sig = Signal(symbol, "BUY", live_price, stop, target, conf,
                 "DAY_MOMENTUM", now, "F1",
                 {"rvol": round(rvol, 2), "range_pos": round(range_pos, 2),
                  "vwap_premium_pct": round((live_price / vwap - 1) * 100, 2),
                  "day_gain_pct": round(gain_pct, 2), "atr": round(atr, 2)})
    return [sig] if sig.is_sane() else []


def day_weakness(
    symbol: str, df_1m: pd.DataFrame, df_daily: pd.DataFrame, live_price: float,
    *, now: datetime | None = None,
) -> list[Signal]:
    """Mirror of `day_momentum` for the short side — pure breakdown, no pattern.

    WHY (2026-08-21)
    ----------------
    Of that session's 15 biggest losers, F1 produced signals on ONE. All 15 were
    in the universe with good data. The cause is structural: of 13
    signal-generating branches in this module only 3 can emit a SELL (ORB short,
    VWAP short, OVERBOUGHT_FADE) and all three need a SHAPE — a range breakdown,
    a VWAP cross, an overbought fade. A stock that simply bleeds all session on
    news matches none of them. Measured that day: 251 BUY signals against 17
    SELL. BALRAMCHIN -4.9%, RENUKA -3.8%, ANDHRSUGAR -3.8% produced nothing.

    GATES ARE DELIBERATELY TIGHTER THAN THE LONG SIDE. A short's loss is
    unbounded, it is MIS-only on NSE (forced same-day cover), and it must clear
    the VIX panic guard. So this needs a bigger move (2.5% vs 2.0%) and a
    firmer position in the day's range (bottom 25% vs the long side's top 30%).

    RVOL uses the 20 CLOSED daily bars, so today is never part of its own
    baseline.
    """
    d = closed(df_1m)
    if len(d) < 20 or live_price <= 0 or df_daily is None or len(df_daily) < 11:
        return []

    day_high = float(d["high"].max())
    day_low = float(d["low"].min())
    day_open = float(d["open"].iloc[0])
    rng = day_high - day_low
    if rng <= 0 or day_open <= 0:
        return []

    prior = df_daily.iloc[:-1] if len(df_daily) > 1 else df_daily
    avg_vol = float(prior["volume"].tail(20).mean())
    vol_today = float(d["volume"].sum())
    if avg_vol <= 0:
        return []
    rvol = vol_today / avg_vol
    if rvol < float(_cfg("TACTICAL_DAY_WEAK_MIN_RVOL", 2.0)):
        return []

    # Sitting near the LOW of the day — sellers still in control.
    range_pos = (live_price - day_low) / rng
    if range_pos > float(_cfg("TACTICAL_DAY_WEAK_MAX_RANGE_POS", 0.25)):
        return []

    tp = (d["high"] + d["low"] + d["close"]) / 3.0
    vol = d["volume"]
    if float(vol.sum()) <= 0:
        return []
    vwap = float((tp * vol).sum() / vol.sum())
    if vwap <= 0 or live_price > vwap * 0.995:      # must be BELOW vwap
        return []

    # Previous close, not the frame's first bar — see day_momentum for the
    # 200-bar-window defect this fixes.
    ref = _prev_close(df_daily, day_open)
    loss_pct = (1.0 - live_price / ref) * 100.0
    if loss_pct < float(_cfg("TACTICAL_DAY_WEAK_MIN_LOSS_PCT", 2.5)):
        return []

    now = now or datetime.now()
    ind = _safe_indicators(df_1m)
    atr = float(ind.atr) if ind is not None and ind.atr and not np.isnan(ind.atr) else 0.0

    # Stop ABOVE entry for a short, target below. Same reasoning as the long
    # side: a 1% stop on a name that has already fallen 7% is inside the noise.
    stop = live_price + max(atr * 1.5, live_price * 0.01)
    if stop <= live_price:
        return []
    risk = stop - live_price
    target = live_price - max(risk * 2.0, live_price * 0.015)
    if target <= 0:
        return []

    conf = _conf(70.0, min(10.0, (rvol - 2.0) * 2.0),
                 min(8.0, (0.25 - range_pos) * 32.0))
    sig = Signal(symbol, "SELL", live_price, stop, target, conf,
                 "DAY_WEAKNESS", now, "F1",
                 {"rvol": round(rvol, 2), "range_pos": round(range_pos, 2),
                  "vwap_discount_pct": round((1 - live_price / vwap) * 100, 2),
                  "day_loss_pct": round(loss_pct, 2), "atr": round(atr, 2)})
    return [sig] if sig.is_sane() else []


F1_RULES = ("ORB", "VWAP", "GAP_AND_GO", "PIVOT_BOUNCE", "PIVOT_BREAKOUT", "SCALP", "DAY_MOMENTUM", "DAY_WEAKNESS")
F4_RULES = ("OVERBOUGHT_FADE", "OVERSOLD_REBOUND", "VOLUME_BREAKOUT", "VWAP_CROSSOVER")
