"""Event-driven backtest for DAY_MOMENTUM / DAY_WEAKNESS.

METHOD — the parts that decide whether the result means anything:

  * NO LOOKAHEAD. At each decision minute the rule sees only bars strictly
    BEFORE it, and `live_price` is that bar's close. The rule's own `closed()`
    then drops one more bar, exactly as in production.
  * The REAL rule functions are called. Not a reimplementation — a
    reimplementation is a test of the reimplementation.
  * Forward simulation walks the remaining 1m bars in order and exits on
    whichever of stop/target the bar's LOW/HIGH touches first. When a single
    bar spans both, the STOP is taken: intrabar order is unknowable and
    assuming the good fill is how backtests flatter themselves.
  * Anything still open at 15:10 IST (09:40 UTC) is squared off at that bar,
    matching the live MIS squareoff.
  * One signal per symbol per day (the first), so a name that keeps qualifying
    all afternoon cannot dominate the sample.
"""
import asyncio, sys, warnings, json, io
warnings.filterwarnings("ignore")
import pandas as pd
from sqlalchemy import text
from db.database import AsyncSessionLocal
from engine import tactical_rules as R

DECISION_MINUTES = [(4, 0), (4, 30), (5, 0), (5, 30), (6, 0), (6, 30),
                    (7, 0), (7, 30), (8, 0), (8, 30), (9, 0)]   # UTC
SQUAREOFF = (9, 40)          # 15:10 IST
LOOKBACK_BARS = 200          # same window the executor passes

async def load_symbols(limit):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT symbol FROM hub_universe WHERE turnover_cr >= 5 AND rank > 0
            ORDER BY turnover_cr DESC LIMIT :l"""), {"l": limit})).fetchall()
    return [r[0] for r in rows]

async def load_day(day, syms):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM candles WHERE timeframe='1m' AND timestamp::date = :d
              AND symbol = ANY(:sy) ORDER BY symbol, timestamp"""),
            {"d": day, "sy": syms})).fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["symbol","timestamp","open","high","low","close","volume"])
    for c in ("open","high","low","close","volume"):
        df[c] = df[c].astype(float)
    return {sym: g.reset_index(drop=True) for sym, g in df.groupby("symbol")}

async def load_daily(syms, before_day):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT symbol, timestamp, open, high, low, close, volume FROM candles
            WHERE timeframe='1d' AND symbol = ANY(:sy) AND timestamp::date < :d
            ORDER BY symbol, timestamp"""), {"sy": syms, "d": before_day})).fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["symbol","timestamp","open","high","low","close","volume"])
    for c in ("open","high","low","close","volume"):
        df[c] = df[c].astype(float)
    return {sym: g.tail(30).reset_index(drop=True) for sym, g in df.groupby("symbol")}

def simulate(bars, i, sig):
    """Walk forward from bar i+1. Returns (R_multiple, outcome)."""
    entry, stop, target = sig.entry_price, sig.stop_loss, sig.target
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    is_long = sig.side == "BUY"
    for j in range(i + 1, len(bars)):
        b = bars.iloc[j]
        ts = b["timestamp"]
        hi, lo = b["high"], b["low"]
        if is_long:
            hit_stop, hit_tgt = lo <= stop, hi >= target
        else:
            hit_stop, hit_tgt = hi >= stop, lo <= target
        if hit_stop:                      # stop wins a tie — see module docstring
            return (-1.0, "STOP")
        if hit_tgt:
            return (abs(target - entry) / risk, "TARGET")
        if (ts.hour, ts.minute) >= SQUAREOFF:
            px = b["close"]
            r = ((px - entry) if is_long else (entry - px)) / risk
            return (r, "SQUAREOFF")
    px = bars.iloc[-1]["close"]
    r = ((px - entry) if is_long else (entry - px)) / risk
    return (r, "EOD")

async def main(n_syms=250):
    syms = await load_symbols(n_syms)
    async with AsyncSessionLocal() as s:
        days = [r[0] for r in (await s.execute(text("""
            SELECT DISTINCT timestamp::date FROM candles WHERE timeframe='1m'
            ORDER BY 1"""))).fetchall()]
    print(f"  symbols={len(syms)}  days={len(days)}  ({days[0]} .. {days[-1]})", flush=True)

    results = []
    for day in days:
        intraday = await load_day(day, syms)
        if not intraday:
            continue
        daily = await load_daily(syms, day)
        for sym, bars in intraday.items():
            dd = daily.get(sym)
            if dd is None or len(dd) < 11 or len(bars) < 40:
                continue
            fired = False
            for (H, M) in DECISION_MINUTES:
                if fired:
                    break
                mask = (bars["timestamp"].dt.hour * 60 + bars["timestamp"].dt.minute) <= (H * 60 + M)
                idx = mask.sum() - 1
                if idx < 30:
                    continue
                hist = bars.iloc[max(0, idx - LOOKBACK_BARS + 1): idx + 1]
                px = float(bars.iloc[idx]["close"])
                for fn, tag in ((R.day_momentum, "DAY_MOMENTUM"), (R.day_weakness, "DAY_WEAKNESS")):
                    try:
                        out = fn(sym, hist, dd, px)
                    except Exception:
                        out = []
                    if out:
                        sim = simulate(bars, idx, out[0])
                        if sim:
                            results.append({"day": str(day), "symbol": sym, "rule": tag,
                                            "hhmm": f"{H:02d}:{M:02d}", "R": sim[0],
                                            "outcome": sim[1], "conf": out[0].confidence,
                                            **{k: v for k, v in out[0].meta.items()
                                               if k in ("rvol", "range_pos")}})
                            fired = True
                            break
        print(f"    {day}  cum_signals={len(results)}", flush=True)
    io.open("/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad/bt_results.json","w").write(json.dumps(results))
    print(f"  TOTAL SIGNALS: {len(results)}")

asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 250))
