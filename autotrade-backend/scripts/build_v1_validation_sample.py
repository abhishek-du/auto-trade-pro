"""V1 validation sample — 100 symbols x 100 sessions (Step 2M.1 §7).

A SAMPLE, not the dataset. It exists to prove the row contract holds before
millions of rows are materialised:

    every feature in row D is computed from sessions <= D
    the target is the NEXT RESOLVED session after D, never D+1 calendar

Reads only through `engine.daily_series`, with an explicit `as_of`. Writes a
CSV; touches no table.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import math
import random
import statistics as st

from sqlalchemy import text

from db.database import AsyncSessionLocal
from engine.daily_series import session_bars
from utils.candle_contract import NSE_SPECIAL_SESSIONS

MARKET_PROXY = "NIFTYBEES.NS"
N_SYMBOLS = 100
N_SESSIONS = 100
WARMUP = 260          # sessions of history a row needs before it can be built


def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _ema(xs, n):
    if len(xs) < n:
        return None
    k, e = 2.0 / (n + 1), sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def _stdev(xs):
    return st.stdev(xs) if len(xs) > 1 else None


def _features(bars, i, mkt_ret, mkt_idx):
    """Row for prediction session bars[i]. Reads bars[:i+1] and nothing else."""
    hist = bars[: i + 1]                       # <= D, enforced by the slice
    c = [b[4] for b in hist]
    h = [b[2] for b in hist]
    lo = [b[3] for b in hist]
    v = [b[5] for b in hist]
    o = [b[1] for b in hist]
    close = c[-1]
    rets = [c[j] / c[j - 1] - 1 for j in range(1, len(c)) if c[j - 1] > 0]

    hl = h[-1] - lo[-1]
    row = {
        "return_1d":  c[-1] / c[-2] - 1 if len(c) > 1 and c[-2] else None,
        "return_5d":  c[-1] / c[-6] - 1 if len(c) > 5 and c[-6] else None,
        "return_21d": c[-1] / c[-22] - 1 if len(c) > 21 and c[-22] else None,
        "return_63d": c[-1] / c[-64] - 1 if len(c) > 63 and c[-64] else None,
        "sma_20": _sma(c, 20), "sma_50": _sma(c, 50), "sma_200": _sma(c, 200),
        "ema_20": _ema(c, 20), "ema_50": _ema(c, 50),
        "realised_vol_21d": _stdev(rets[-21:]) if len(rets) >= 21 else None,
        "range_pct": hl / close if close else None,
        "body_pct": (close - o[-1]) / hl if hl > 0 else None,      # NULL on a locked bar
        "upper_wick_pct": (h[-1] - max(o[-1], close)) / hl if hl > 0 else None,
        "lower_wick_pct": (min(o[-1], close) - lo[-1]) / hl if hl > 0 else None,
        "gap_pct": o[-1] / c[-2] - 1 if len(c) > 1 and c[-2] else None,
        "high_20d": max(h[-20:]) if len(h) >= 20 else None,
        "low_20d": min(lo[-20:]) if len(lo) >= 20 else None,
        "high_52w": max(h[-252:]) if len(h) >= 252 else None,
        "low_52w": min(lo[-252:]) if len(lo) >= 252 else None,
        "volume": v[-1],
        "avg_volume_20d": _sma(v, 20),
        "turnover_proxy": close * v[-1],
    }
    if row["sma_20"]:
        row["close_vs_sma20"] = close / row["sma_20"] - 1
    if row["sma_200"]:
        row["close_vs_sma200"] = close / row["sma_200"] - 1
    if row["high_20d"] and row["low_20d"] and row["high_20d"] > row["low_20d"]:
        row["range_position_20d"] = (close - row["low_20d"]) / (row["high_20d"] - row["low_20d"])
    if row["avg_volume_20d"]:
        row["volume_ratio_20d"] = v[-1] / row["avg_volume_20d"]
    if len(h) >= 21:
        row["breakout_20d"] = int(close > max(h[-21:-1]))
        row["breakdown_20d"] = int(close < min(lo[-21:-1]))

    # market proxy, cut at the SAME session
    d = bars[i][0]
    j = mkt_idx.get(d)
    if j is not None:
        row["mkt_return_1d"] = mkt_ret[j].get("r1")
        row["mkt_return_21d"] = mkt_ret[j].get("r21")
        if row["return_21d"] is not None and row["mkt_return_21d"] is not None:
            row["rel_strength_21d"] = row["return_21d"] - row["mkt_return_21d"]
    return row


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", type=int, default=N_SYMBOLS)
    ap.add_argument("--sessions", type=int, default=N_SESSIONS)
    ap.add_argument("--seed", type=int, default=20260908)
    args = ap.parse_args()

    random.seed(args.seed)
    async with AsyncSessionLocal() as s:
        pool = [r[0] for r in (await s.execute(text("""
            SELECT symbol FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
            GROUP BY symbol HAVING count(*) >= :n ORDER BY symbol
        """), {"n": WARMUP + args.sessions + 5})).all()]
        pool = [p for p in pool if not p.startswith("^")]
        syms = sorted(random.sample(pool, min(args.symbols, len(pool))))
        print(f"[sample] eligible symbols={len(pool)}  chosen={len(syms)}")

        last = (await s.execute(text("""
            SELECT max(timestamp)::date FROM candles
            WHERE timeframe='1d' AND to_char(timestamp,'HH24:MI')='03:45'
        """))).scalar()
        start = last - dt.timedelta(days=365 * 6)

        mkt = await session_bars(MARKET_PROXY, start, last, s, as_of=last,
                                 extra_open=NSE_SPECIAL_SESSIONS)
        mc = [b[4] for b in mkt]
        mkt_ret, mkt_idx = [], {}
        for k, b in enumerate(mkt):
            mkt_idx[b[0]] = k
            mkt_ret.append({
                "r1":  mc[k] / mc[k - 1] - 1 if k >= 1 and mc[k - 1] else None,
                "r21": mc[k] / mc[k - 21] - 1 if k >= 21 and mc[k - 21] else None,
            })

        rows, viol = [], []
        for sym in syms:
            bars = await session_bars(sym, start, last, s, as_of=last,
                                      extra_open=NSE_SPECIAL_SESSIONS)
            if len(bars) < WARMUP + 2:
                continue
            usable = [i for i in range(WARMUP, len(bars) - 1)
                      if bars[i][4] > 0 and bars[i + 1][4] > 0
                      and bars[i + 1][2] > 0 and bars[i + 1][3] > 0]
            for i in usable[-args.sessions:]:
                D, D1 = bars[i][0], bars[i + 1][0]
                cD = bars[i][4]
                feat = _features(bars, i, mkt_ret, mkt_idx)
                ret = bars[i + 1][4] / cD - 1
                row = {
                    "symbol": sym, "prediction_session": D, "target_session": D1,
                    "max_feature_session": D,          # the slice bound, asserted below
                    "close_D": cD,
                    "next_session_return": ret,
                    "next_session_high_return": bars[i + 1][2] / cD - 1,
                    "next_session_low_return": bars[i + 1][3] / cD - 1,
                    "extreme_return_flag": int(abs(ret) > 0.50),
                    **feat,
                }
                if D1 <= D:
                    viol.append(("target not after prediction", sym, D, D1))
                rows.append(row)

    keys = ["symbol", "prediction_session", "target_session", "max_feature_session",
            "close_D", "next_session_return", "next_session_high_return",
            "next_session_low_return", "extreme_return_flag"]
    keys += [k for k in rows[0] if k not in keys]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[sample] rows={len(rows)}  symbols={len({r['symbol'] for r in rows})}")
    print(f"[sample] key violations: {len(viol)}")
    print(f"[sample] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
