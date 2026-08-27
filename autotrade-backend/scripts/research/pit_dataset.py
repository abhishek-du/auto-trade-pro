"""Point-in-time T -> T+1 dataset builder. Look-ahead is refused, not avoided.

WHY THIS EXISTS
---------------
Before we can say "ORDER_WIN + breakout + volume = strong next-day candidate"
we need a dataset where every feature was GENUINELY KNOWABLE at T's close and
every label comes strictly from T+1. Without that discipline a backtest is
fake-good and the conclusion is worthless.

THE CONTRACT

    T          = a past trading date
    features   = information available at or before T's 15:30 IST close
    labels     = T+1 open / high / low / close / MFE / MAE, net of costs

THE THREE WAYS LOOK-AHEAD NORMALLY LEAKS, AND HOW EACH IS CLOSED

  1. A candle bar stamped after the close.
     Every candle query is bounded by `timestamp <= T_close_utc`. The bound is
     a parameter, never `now()`, so re-running the script tomorrow produces the
     identical row for the same T.

  2. News timestamped after the close, or news whose timestamp is wrong.
     Bounded by `published_at <= T_close_utc`. This codebase already had the
     exact bug that makes such a bound a lie: _parse_nse_announcement_dt once
     stored IST wall-clock in a UTC column, putting 4,159 rows (15.3% of all
     news_items with a published_at) 5h30m in the FUTURE relative to their own
     crawled_at. So the filter is `published_at <= T_close AND crawled_at <=
     T_close` -- an item is admissible only if we can prove we had already
     SEEN it, not merely that it claims an early publication time.

  3. Survivorship / identity leakage.
     Symbols are resolved with the deterministic resolver
     (utils/identity.py) using ONLY the instrument table, which is a
     point-in-time-stable reference. Unresolvable and ambiguous names are
     EXCLUDED and counted, never silently dropped or guessed into a ticker.

WHAT IT DOES NOT DO
-------------------
It does not select, rank or recommend anything. It emits rows. Any threshold
found by staring at these rows still has to survive a time-ordered holdout,
because choosing a rule after seeing the labels is look-ahead of a subtler kind
that no SQL bound can prevent.

RESEARCH ONLY. Reads production, writes a CSV. Imports no execution path.

USAGE
    cd autotrade-backend
    PYTHONPATH=$PWD .venv/bin/python scripts/research/pit_dataset.py 2026-08-01 2026-08-26
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass

from sqlalchemy import text

from db.database import AsyncSessionLocal

# NSE session, IST. Candles and news_items are UTC-naive.
_OPEN_IST = dt.time(9, 15)
_CLOSE_IST = dt.time(15, 30)
_IST_OFFSET = dt.timedelta(hours=5, minutes=30)

# Product-aware round-trip cost, matching paper_trading.trade_simulator.
COST_PCT = {"MIS": 0.0011, "CNC": 0.00294}


def ist_to_utc(d: dt.date, t: dt.time) -> dt.datetime:
    return dt.datetime.combine(d, t) - _IST_OFFSET


@dataclass
class Row:
    # ── identity ──
    t_date: str
    symbol: str
    source_symbol: str
    resolution: str
    # ── features, ALL knowable at T close ──
    event_type: str | None
    event_count_t: int
    nse_category: str | None
    close_t: float | None
    ret_1d_t: float | None
    ret_5d_t: float | None
    range_pct_t: float | None
    volume_t: float | None
    vol_ratio_20d_t: float | None
    above_ema20_t: int | None
    dist_20d_high_pct_t: float | None
    bars_available_t: int
    # ── labels, ALL from T+1 ──
    t1_date: str | None
    t1_open: float | None
    t1_high: float | None
    t1_low: float | None
    t1_close: float | None
    t1_gap_pct: float | None
    t1_ret_close_to_close_pct: float | None
    t1_ret_open_to_close_pct: float | None
    t1_mfe_pct: float | None
    t1_mae_pct: float | None
    t1_net_mis_pct: float | None
    # ── provenance ──
    label_bars: int
    excluded_reason: str | None


async def _daily_bars(s, symbol: str, upto_utc: dt.datetime, n: int = 30):
    """Last n daily bars at or before upto_utc. The bound is the whole point."""
    # The candles table carries TWO parallel 1d series, stamped 00:00 and 18:30
    # UTC. Picking by hour is a trap: the 00:00 series has 4.85M rows but only
    # FOUR symbols updated in the last ten days, and its stale bars are
    # PRE-corporate-action. Filtering to hour=0 gave JLHL.NS a close of 1348
    # (a June bar, pre-split) against a real 320, manufacturing a -76% "gap"
    # and a fake +1.9% median overnight edge across the whole dataset.
    #
    # DISTINCT ON takes the LATEST bar per calendar date whatever its hour, so
    # the live series wins wherever it exists and history still resolves.
    rows = (await s.execute(text("""
        SELECT DISTINCT ON (timestamp::date)
               timestamp, open, high, low, close, volume
        FROM candles
        WHERE symbol = :s AND timeframe = '1d' AND timestamp <= :b
        ORDER BY timestamp::date DESC, timestamp DESC
        LIMIT :n"""),
        {"s": symbol, "b": upto_utc, "n": n})).all()
    return list(reversed(rows))


async def _t1_bars(s, symbol: str, t1: dt.date):
    """T+1 intraday bars. Used ONLY for labels, never for features."""
    a = ist_to_utc(t1, _OPEN_IST)
    b = ist_to_utc(t1, _CLOSE_IST)
    return (await s.execute(text("""
        SELECT count(*) n, min(timestamp) first_ts,
               (array_agg(open  ORDER BY timestamp ASC ))[1] o,
               max(high) hi, min(low) lo,
               (array_agg(close ORDER BY timestamp DESC))[1] c
        FROM candles WHERE symbol = :s AND timeframe = '1m'
          AND timestamp >= :a AND timestamp <= :b"""),
        {"s": symbol, "a": a, "b": b})).first()


async def _events_at_t(s, t_close_utc: dt.datetime, t_open_utc: dt.datetime):
    """Events we had genuinely SEEN by T's close.

    Both published_at AND crawled_at must precede the close -- see the module
    docstring for the 4,159-row timezone bug that makes published_at alone
    untrustworthy.
    """
    return (await s.execute(text("""
        SELECT ce.id, ce.event_title, ce.bullish_stocks, ce.bearish_stocks,
               ce.confidence, ce.importance, ni.category
        FROM causal_events ce
        LEFT JOIN news_items ni ON ni.id = ce.news_id
        WHERE ce.created_at <= :close
          AND ce.created_at >= :open
          AND (ni.id IS NULL OR (ni.published_at <= :close AND ni.crawled_at <= :close))
        ORDER BY ce.created_at"""),
        {"close": t_close_utc, "open": t_open_utc})).all()


def _feat(bars) -> dict:
    """Features from daily bars only. Never touches T+1."""
    if not bars:
        return {}
    closes = [float(b.close) for b in bars]
    vols = [float(b.volume or 0) for b in bars]
    last = bars[-1]
    c = float(last.close)
    out = {
        "close_t": round(c, 4),
        "volume_t": round(float(last.volume or 0), 0),
        "bars_available_t": len(bars),
        "range_pct_t": round(100 * (float(last.high) - float(last.low)) / c, 4) if c else None,
    }
    if len(closes) >= 2 and closes[-2]:
        out["ret_1d_t"] = round(100 * (c / closes[-2] - 1), 4)
    if len(closes) >= 6 and closes[-6]:
        out["ret_5d_t"] = round(100 * (c / closes[-6] - 1), 4)
    if len(vols) >= 21:
        base = sum(vols[-21:-1]) / 20
        out["vol_ratio_20d_t"] = round(vols[-1] / base, 4) if base > 0 else None
    if len(closes) >= 20:
        k = 2 / 21
        ema = closes[-20]
        for x in closes[-19:]:
            ema = x * k + ema * (1 - k)
        out["above_ema20_t"] = 1 if c > ema else 0
        hi20 = max(float(b.high) for b in bars[-20:])
        out["dist_20d_high_pct_t"] = round(100 * (c / hi20 - 1), 4) if hi20 else None
    return out


async def build(start: dt.date, end: dt.date, out_path: str) -> None:
    from utils.identity import build_index, resolve_identity

    idx = await build_index()
    print(f"identity index: {idx.stats()}")

    rows: list[Row] = []
    skipped = {"unresolved": 0, "ambiguous": 0, "no_features": 0, "no_labels": 0}

    async with AsyncSessionLocal() as s:
        sessions = [r.d for r in (await s.execute(text("""
            SELECT DISTINCT timestamp::date d FROM candles
            WHERE timeframe = '1d' AND timestamp::date BETWEEN :a AND :b
            ORDER BY 1"""), {"a": start, "b": end + dt.timedelta(days=5)})).all()]

    print(f"trading sessions in range: {len(sessions)}")

    for i, T in enumerate(sessions):
        if T > end or i + 1 >= len(sessions):
            continue
        T1 = sessions[i + 1]
        t_close = ist_to_utc(T, _CLOSE_IST)
        t_open = ist_to_utc(T, _OPEN_IST)

        async with AsyncSessionLocal() as s:
            evs = await _events_at_t(s, t_close, t_open)
            per_symbol: dict[str, dict] = {}
            for e in evs:
                for field, side in (("bullish_stocks", "BUY"), ("bearish_stocks", "SELL")):
                    raw = getattr(e, field)
                    arr = raw if isinstance(raw, list) else json.loads(raw or "[]")
                    for nm in arr:
                        r = resolve_identity(str(nm), idx)
                        if not r.ok:
                            skipped["ambiguous" if r.needs_review else "unresolved"] += 1
                            continue
                        d = per_symbol.setdefault(r.symbol, {
                            "source_symbol": str(nm), "resolution": r.resolution.value,
                            "event_type": e.event_title, "nse_category": e.category,
                            "count": 0})
                        d["count"] += 1

            for sym, meta in per_symbol.items():
                bars = await _daily_bars(s, sym, t_close)
                f = _feat(bars)
                if not f or f.get("close_t") is None:
                    skipped["no_features"] += 1
                    continue
                lab = await _t1_bars(s, sym, T1)
                if not lab or not lab.n or lab.c is None:
                    skipped["no_labels"] += 1
                    continue

                entry = float(lab.o)
                hi, lo, cl = float(lab.hi), float(lab.lo), float(lab.c)
                ct = f["close_t"]

                # Stale-reference guard. A genuine overnight gap beyond +/-35%
                # is essentially always a corporate action or a stale bar, not
                # a tradeable move. Dropping these loudly beats letting one
                # pre-split close manufacture a -76% observation.
                if ct and entry and abs(entry / ct - 1) > 0.35:
                    skipped["stale_reference"] = skipped.get("stale_reference", 0) + 1
                    continue
                rows.append(Row(
                    t_date=str(T), symbol=sym,
                    source_symbol=meta["source_symbol"], resolution=meta["resolution"],
                    event_type=meta["event_type"], event_count_t=meta["count"],
                    nse_category=meta["nse_category"],
                    close_t=ct, ret_1d_t=f.get("ret_1d_t"), ret_5d_t=f.get("ret_5d_t"),
                    range_pct_t=f.get("range_pct_t"), volume_t=f.get("volume_t"),
                    vol_ratio_20d_t=f.get("vol_ratio_20d_t"),
                    above_ema20_t=f.get("above_ema20_t"),
                    dist_20d_high_pct_t=f.get("dist_20d_high_pct_t"),
                    bars_available_t=f["bars_available_t"],
                    t1_date=str(T1), t1_open=round(entry, 4), t1_high=round(hi, 4),
                    t1_low=round(lo, 4), t1_close=round(cl, 4),
                    t1_gap_pct=round(100 * (entry / ct - 1), 4) if ct else None,
                    t1_ret_close_to_close_pct=round(100 * (cl / ct - 1), 4) if ct else None,
                    t1_ret_open_to_close_pct=round(100 * (cl / entry - 1), 4) if entry else None,
                    t1_mfe_pct=round(100 * (hi / entry - 1), 4) if entry else None,
                    t1_mae_pct=round(100 * (lo / entry - 1), 4) if entry else None,
                    t1_net_mis_pct=round(100 * ((cl / entry - 1) - COST_PCT["MIS"]), 4) if entry else None,
                    label_bars=int(lab.n), excluded_reason=None))

    if rows:
        with open(out_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(asdict(rows[0]).keys()))
            w.writeheader()
            for r in rows:
                w.writerow(asdict(r))

    print(f"\nrows written : {len(rows)} -> {out_path}")
    print(f"excluded     : {skipped}")
    if rows:
        import statistics as st
        nets = [r.t1_net_mis_pct for r in rows if r.t1_net_mis_pct is not None]
        print(f"\nT+1 open->close, net of MIS cost, n={len(nets)}")
        print(f"  median {st.median(nets):+.3f}%   mean {st.mean(nets):+.3f}%")
        print(f"  P(>0)  {100*sum(1 for x in nets if x>0)/len(nets):.1f}%")
        print("\nNOTE: this is a DESCRIPTION of the sample, not an edge. Any rule")
        print("chosen after seeing these numbers must still survive a time-ordered")
        print("holdout — picking a threshold from labels is look-ahead too.")


if __name__ == "__main__":
    a = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today() - dt.timedelta(days=30)
    b = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date.today() - dt.timedelta(days=1)
    out = sys.argv[3] if len(sys.argv) > 3 else f"/tmp/pit_dataset_{a}_{b}.csv"
    asyncio.run(build(a, b, out))
