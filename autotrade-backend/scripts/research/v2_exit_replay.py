"""Replay the SAME historical trades under CONTROL and every V2 horizon.

WHAT THIS ANSWERS
-----------------
Phase 24 measured forward returns on OPPORTUNITIES. This measures P&L on
TRADES — the same trades, the same entries, the same stops, differing in one
variable only: when profit management is allowed to start.

WHAT IT CANNOT DO — READ THIS BEFORE BELIEVING A NUMBER
-------------------------------------------------------
Two of the live profit-management exits are NOT replayable:

  * EXHAUSTION (17 of the 43 historical MIS exits) reads the live 5m frame.
    The stored 5m series is rebuilt from 1m by the resampler, so a replay
    cannot recover the bar the live check actually consumed. This is the same
    limitation already documented at tasks/india_tasks.py's EXHAUSTION_AUDIT.
  * T1_REVERSAL_EXIT needs an LLM call that cannot be reproduced.

So the simulator models the MECHANICAL profit-management layer only: breakeven
at +2% and a 2.5-ATR chandelier, which is what update_trailing_stop does. That
makes every SIM figure a MODEL, not a measurement.

The defence against that is SIM-CONTROL. It runs the identical model with
profit management active from minute 0 — i.e. today's behaviour. Comparing it
against ACTUAL says how much of reality the model captures. Comparing it
against SIM-V2-X is then apples-to-apples, because both sides carry the same
modelling error. ACTUAL is ground truth and is never simulated.

    ACTUAL             what really happened, from paper_trades
    SIM-CONTROL        model, profit management from minute 0
    SIM-V2-{60..180}   model, profit management from minute X
    SIM-HOLD_TO_CLOSE  model, no profit management at all

Hard stop and squareoff are active in EVERY variant including HOLD_TO_CLOSE.
"Hold longer" never means "hold through an unbounded loss".

USAGE
    cd autotrade-backend
    PYTHONPATH=$PWD .venv/bin/python scripts/research/v2_exit_replay.py

Research only. Imports no execution path; writes nothing but SimulationLog.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import statistics as st
import sys

from sqlalchemy import text

from db.database import AsyncSessionLocal

# Horizons the brief asks for, plus the two bounds.
HORIZONS = (60, 90, 120, 150, 180)

# Mechanical profit-management parameters, read from the same settings the live
# trailing stop uses so the model cannot silently drift from the code.
from utils.config import settings

BREAKEVEN_TRIGGER_PCT = float(getattr(settings, "TRAILING_BREAKEVEN_TRIGGER_PCT", 2.0))
ATR_MULT = float(getattr(settings, "TRAILING_STOP_ATR_MULT", 2.5))
SQUAREOFF_IST = (
    int(getattr(settings, "TIME_BASED_EXIT_HOUR", 15)),
    int(getattr(settings, "TIME_BASED_EXIT_MINUTE", 10)),
)

from paper_trading.trade_simulator import estimate_trade_cost


def _squareoff_utc(day: dt.date) -> dt.datetime:
    """IST squareoff expressed in the UTC-naive frame candles are stored in."""
    h, m = SQUAREOFF_IST
    return dt.datetime.combine(day, dt.time(h, m)) - dt.timedelta(hours=5, minutes=30)


def _net(gross: float, units: float, entry: float, exit_px: float,
         long: bool, product: str) -> float:
    a = "BUY" if long else "SELL"
    b = "SELL" if long else "BUY"
    return gross - (estimate_trade_cost(units, entry, a, product)
                    + estimate_trade_cost(units, exit_px, b, product))


def _simulate(bars, *, entry, hard_stop, units, long, product,
              pm_from_minute, opened_at, squareoff):
    """Walk 1m bars and return (net_pnl, exit_reason, exit_minute, mfe, mae).

    `pm_from_minute=0` is CONTROL. `None` disables profit management entirely.

    Intrabar ordering is UNKNOWABLE from OHLC, so when both the stop and a
    favourable extreme occur in one bar this assumes the ADVERSE one first.
    That biases every variant against holding, which is the conservative
    direction for a change whose whole thesis is "hold longer".
    """
    stop = hard_stop
    peak = entry
    mfe = mae = 0.0
    atr_window: list[float] = []

    for ts, o, h, l, c in bars:
        held = (ts - opened_at).total_seconds() / 60.0

        fav = (h - entry) if long else (entry - l)
        adv = (l - entry) if long else (entry - h)
        mfe = max(mfe, fav * units)
        mae = min(mae, adv * units)

        atr_window.append(h - l)
        if len(atr_window) > 14:
            atr_window.pop(0)
        atr = sum(atr_window) / len(atr_window) if atr_window else 0.0

        # 1. Hard stop — active in every variant, at every age.
        if (long and l <= stop) or ((not long) and h >= stop):
            gross = (stop - entry) * units if long else (entry - stop) * units
            return _net(gross, units, entry, stop, long, product), "HARD_STOP", held, mfe, mae

        # 2. Profit management, once the horizon has passed.
        if pm_from_minute is not None and held >= pm_from_minute:
            peak = max(peak, h) if long else min(peak, l)
            gain = ((peak / entry - 1) * 100) if long else ((entry / peak - 1) * 100)
            if gain >= BREAKEVEN_TRIGGER_PCT:
                cand = entry if long else entry
                stop = max(stop, cand) if long else min(stop, cand)
                if atr > 0:
                    ch = peak - ATR_MULT * atr if long else peak + ATR_MULT * atr
                    if long and ch < c:
                        stop = max(stop, ch)
                    elif (not long) and ch > c:
                        stop = min(stop, ch)

        # 3. Squareoff — the maximum hold, in every variant.
        if ts >= squareoff:
            gross = (c - entry) * units if long else (entry - c) * units
            return _net(gross, units, entry, c, long, product), "MARKET_SQUAREOFF", held, mfe, mae

    if not bars:
        return None
    ts, o, h, l, c = bars[-1]
    held = (ts - opened_at).total_seconds() / 60.0
    gross = (c - entry) * units if long else (entry - c) * units
    return _net(gross, units, entry, c, long, product), "SESSION_END", held, mfe, mae


async def run() -> dict:
    async with AsyncSessionLocal() as s:
        trades = (await s.execute(text("""
            SELECT id, symbol, direction::text AS direction, product,
                   entry_price, size_units, pnl, exit_reason,
                   initial_risk_inr, opened_at, closed_at
            FROM paper_trades
            WHERE status != 'OPEN'
              AND strategy_family = 'TACTICAL'
              AND product = 'MIS'
              AND initial_risk_inr > 0
              AND size_units > 0
            ORDER BY opened_at
        """))).all()

    print(f"\n### V2 EXIT REPLAY — {len(trades)} historical TACTICAL/MIS trades")
    print("### Same trades, same entries, same stops. One variable: when profit")
    print("### management may start.\n")

    variants = ["SIM-CONTROL"] + [f"SIM-V2-{m}" for m in HORIZONS] + ["SIM-HOLD_TO_CLOSE"]
    results: dict[str, list] = {v: [] for v in variants}
    actual: list[float] = []
    skipped = 0

    async with AsyncSessionLocal() as s:
        for t in trades:
            long = "SELL" not in (t.direction or "").upper()
            units = float(t.size_units)
            entry = float(t.entry_price)
            risk_per_unit = float(t.initial_risk_inr) / units
            # The ORIGINAL stop, not paper_trades.stop_loss — trailing mutates
            # that column in place, so the stored value on a trailed winner is
            # a profit stop and using it would smuggle CONTROL's behaviour into
            # every variant.
            hard_stop = entry - risk_per_unit if long else entry + risk_per_unit

            squareoff = _squareoff_utc(t.opened_at.date())
            rows = (await s.execute(text("""
                SELECT timestamp, open, high, low, close FROM candles
                WHERE symbol = :sym AND timeframe = '1m'
                  AND timestamp >= :a AND timestamp <= :b
                ORDER BY timestamp
            """), {"sym": t.symbol, "a": t.opened_at, "b": squareoff})).all()

            bars = [(r.timestamp, float(r.open), float(r.high), float(r.low), float(r.close))
                    for r in rows]
            if len(bars) < 5:
                skipped += 1
                continue

            actual.append(float(t.pnl or 0))
            for v in variants:
                pm = (0 if v == "SIM-CONTROL"
                      else None if v == "SIM-HOLD_TO_CLOSE"
                      else int(v.rsplit("-", 1)[1]))
                out = _simulate(
                    bars, entry=entry, hard_stop=hard_stop, units=units, long=long,
                    product=t.product or "MIS", pm_from_minute=pm,
                    opened_at=t.opened_at, squareoff=squareoff,
                )
                if out:
                    results[v].append(out)

    if not actual:
        print("no replayable trades (no 1m candles in the holding windows)")
        return {}

    _report(actual, results, skipped)
    return {"actual": actual, "results": results}


def _stats(pnls: list[float]) -> dict:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    eq, peak, dd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "n": len(pnls),
        "net": sum(pnls),
        "avg": st.mean(pnls) if pnls else 0.0,
        "median": st.median(pnls) if pnls else 0.0,
        "win_rate": 100 * len(wins) / len(pnls) if pnls else 0.0,
        "pf": (gross_w / gross_l) if gross_l > 0 else float("inf"),
        "max_dd": dd,
    }


def _report(actual, results, skipped) -> None:
    base = _stats(actual)
    print(f"  skipped for missing candles: {skipped}\n")
    hdr = (f"  {'variant':<20}{'n':>4}{'net':>10}{'avg':>9}{'median':>9}"
           f"{'win%':>7}{'PF':>7}{'maxDD':>9}{'avg hold':>10}{'MFE cap':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    print(f"  {'ACTUAL (ground truth)':<20}{base['n']:>4}{base['net']:>10,.0f}"
          f"{base['avg']:>9,.0f}{base['median']:>9,.0f}{base['win_rate']:>7.0f}"
          f"{base['pf']:>7.2f}{base['max_dd']:>9,.0f}{'—':>10}{'—':>9}")

    for v, rows in results.items():
        if not rows:
            continue
        pnls = [r[0] for r in rows]
        holds = [r[2] for r in rows]
        mfes = [r[3] for r in rows]
        s = _stats(pnls)
        cap = (100 * sum(pnls) / sum(mfes)) if sum(mfes) > 0 else 0.0
        print(f"  {v:<20}{s['n']:>4}{s['net']:>10,.0f}{s['avg']:>9,.0f}"
              f"{s['median']:>9,.0f}{s['win_rate']:>7.0f}{s['pf']:>7.2f}"
              f"{s['max_dd']:>9,.0f}{st.mean(holds):>9.0f}m{cap:>8.0f}%")

    print("\n  exit distribution")
    for v, rows in results.items():
        if not rows:
            continue
        dist: dict[str, int] = {}
        for _, reason, _, _, _ in rows:
            dist[reason] = dist.get(reason, 0) + 1
        print(f"  {v:<20}" + "  ".join(f"{k}={n}" for k, n in sorted(dist.items())))

    print("\n  average MAE (rupees, the worst the position got before its exit)")
    for v, rows in results.items():
        if rows:
            print(f"  {v:<20}{st.mean([r[4] for r in rows]):>10,.0f}")

    # How many INDIVIDUAL trades actually change between variants. If a whole
    # horizon's advantage rests on one or two trades, the ranking is noise and
    # must not be used to pick a horizon.
    ctrl = results.get("SIM-CONTROL") or []
    if ctrl:
        print("\n  trades whose outcome differs from SIM-CONTROL")
        print(f"  {'variant':<20}{'changed':>9}{'of n':>7}{'net delta':>12}{'from changed':>14}")
        for v, rows in results.items():
            if v == "SIM-CONTROL" or not rows or len(rows) != len(ctrl):
                continue
            diffs = [(i, r[0] - c[0]) for i, (r, c) in enumerate(zip(rows, ctrl))
                     if abs(r[0] - c[0]) > 0.01]
            delta = sum(d for _, d in diffs)
            print(f"  {v:<20}{len(diffs):>9}{len(rows):>7}{delta:>12,.0f}{delta:>13,.0f}")
        print("  A horizon that moves only a handful of trades has not been shown to")
        print("  be better than its neighbours; it has been shown to be indistinguishable.")

    sim_control = results.get("SIM-CONTROL") or []
    if sim_control:
        sim_net = sum(r[0] for r in sim_control)
        print(f"\n  MODEL VALIDITY: SIM-CONTROL net ₹{sim_net:,.0f} vs ACTUAL "
              f"₹{base['net']:,.0f} (gap ₹{sim_net - base['net']:,.0f}).")
        print("  The gap is EXHAUSTION and T1_REVERSAL_EXIT, which the model cannot")
        print("  replay. A large gap means SIM-CONTROL is a poor stand-in for today's")
        print("  behaviour and the V2 deltas below it should be read as indicative only.")


if __name__ == "__main__":
    asyncio.run(run())
