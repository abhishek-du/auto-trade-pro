"""EXIT EXPERIMENT — shadow mode. Records what other exit horizons would have done.

WHAT THIS IS
------------
Phase 24 measured, on 5,488 t0 opportunities across five sessions, that the
signalled subset returns net -0.052% at 60 minutes and +0.342% held to the
close, and that the pattern replicated on a held-out session. That is evidence
that the exit horizon may be closing positions before the edge matures.

It is NOT yet evidence that "hold everything longer" is correct. This module
exists so tomorrow can compare rather than assume.

WHAT THIS IS NOT
----------------
This module CANNOT place, modify or close an order. It is not imported by any
trading path, it takes no position object, it calls nothing in
paper_trading.trade_simulator, engine.decision_router or
engine.zerodha_executor, and it runs after the session from the command line.
tests/test_exit_shadow_isolation.py enforces every one of those claims.

The real trade always follows CONTROL — the existing exit stack, unchanged.

WHY IT RUNS AFTER THE CLOSE
---------------------------
The hypothetical horizons need candles that do not exist yet at the moment a
position is closed. Computing them at close time is impossible, so this reads
already-closed trades and the candles that followed them.

USAGE
-----
    cd autotrade-backend
    PYTHONPATH=$PWD .venv/bin/python scripts/research/exit_horizon_shadow.py 2026-08-27

Writes one EXIT_HORIZON_SHADOW row per trade into simulation_logs and prints a
comparison table. Counts and prices only; no payloads, no credentials.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import statistics as st
import sys

from sqlalchemy import text

from db.database import AsyncSessionLocal

IST = "at time zone 'UTC' at time zone 'Asia/Kolkata'"

# Horizons measured from each position's ACTUAL exit, plus the session close.
HORIZONS_MIN = (30, 60, 90, 120)

# Corrected product-aware round-trip costs, as a fraction of notional.
# Both scenarios pay the same round trip, so this cancels when comparing
# actual against hypothetical — it is applied anyway so every figure printed is
# a net figure and cannot be mistaken for a gross one.
COST_PCT = {"MIS": 0.0011, "CNC": 0.00294}


def _net(gross: float, notional: float, product: str | None) -> float:
    return gross - notional * COST_PCT.get((product or "CNC").upper(), COST_PCT["CNC"])


async def _extremes(session, symbol: str, a: dt.datetime, b: dt.datetime):
    """(high, low, last close) over (a, b] in UTC-naive candle time."""
    r = (await session.execute(
        text(
            "SELECT MAX(high) hi, MIN(low) lo, "
            "(array_agg(close ORDER BY timestamp DESC))[1] lastc "
            "FROM candles WHERE symbol = :s AND timeframe = '1m' "
            "AND timestamp > :a AND timestamp <= :b"
        ),
        {"s": symbol, "a": a, "b": b},
    )).first()
    if r is None or r.lastc is None:
        return None
    return float(r.hi), float(r.lo), float(r.lastc)


async def run(day: dt.date) -> list[dict]:
    async with AsyncSessionLocal() as s:
        trades = (await s.execute(
            text(f"""
                SELECT id, symbol, direction::text AS direction, product,
                       entry_price, exit_price, size_units, pnl, exit_reason,
                       opened_at, closed_at,
                       (closed_at {IST}) AS closed_ist
                FROM paper_trades
                WHERE (closed_at {IST})::date = :d
                ORDER BY closed_at
            """),
            {"d": day},
        )).all()

    if not trades:
        print(f"no trades closed on {day}")
        return []

    # Session close in UTC-naive terms, matching candles.timestamp.
    close_utc = dt.datetime.combine(day, dt.time(9, 59))

    out: list[dict] = []
    for t in trades:
        units = float(t.size_units or 0)
        entry = float(t.entry_price or 0)
        if units <= 0 or entry <= 0:
            continue
        long = "SELL" not in (t.direction or "").upper()
        notional = units * entry
        actual_net = float(t.pnl or 0)          # already net in paper_trades

        rec = {
            "trade_id": t.id,
            "symbol": t.symbol,
            "product": t.product,
            "exit_reason": t.exit_reason,
            "actual_exit_ist": str(t.closed_ist),
            "actual_net": round(actual_net, 2),
        }

        async with AsyncSessionLocal() as s2:
            for m in HORIZONS_MIN:
                e = await _extremes(s2, t.symbol, t.closed_at,
                                    t.closed_at + dt.timedelta(minutes=m))
                if e is None:
                    rec[f"hold_{m}m_net"] = None
                    continue
                hi, lo, last = e
                gross = (last - entry) * units if long else (entry - last) * units
                rec[f"hold_{m}m_net"] = round(_net(gross, notional, t.product), 2)
                rec[f"hold_{m}m_mfe_pct"] = round(
                    100 * ((hi - entry) if long else (entry - lo)) / entry, 3)
                rec[f"hold_{m}m_mae_pct"] = round(
                    100 * ((lo - entry) if long else (entry - hi)) / entry, 3)

            e = await _extremes(s2, t.symbol, t.closed_at, close_utc)
            if e is None:
                rec["hold_close_net"] = None
            else:
                hi, lo, last = e
                gross = (last - entry) * units if long else (entry - last) * units
                rec["hold_close_net"] = round(_net(gross, notional, t.product), 2)
                rec["hold_close_mfe_pct"] = round(
                    100 * ((hi - entry) if long else (entry - lo)) / entry, 3)
                rec["hold_close_mae_pct"] = round(
                    100 * ((lo - entry) if long else (entry - hi)) / entry, 3)

        out.append(rec)

    # Persist for later analysis. Research only; failure here loses a
    # measurement and nothing else.
    try:
        from db.models import SimulationLog
        async with AsyncSessionLocal() as s3:
            for rec in out:
                s3.add(SimulationLog(
                    event_type="EXIT_HORIZON_SHADOW",
                    symbol=rec["symbol"],
                    message=f"shadow horizons for trade {rec['trade_id']}",
                    data=rec,
                ))
            await s3.commit()
    except Exception as exc:
        print(f"  (persist failed, results still printed: {type(exc).__name__})")

    _report(out, day)
    return out


def _report(rows: list[dict], day: dt.date) -> None:
    print(f"\n### EXIT HORIZON SHADOW — {day}, {len(rows)} closed trades")
    print("### CONTROL is what actually happened. The rest is hypothetical and was NOT traded.\n")

    def agg(key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return (len(v), sum(v), st.median(v)) if v else (0, 0.0, 0.0)

    print(f"  {'scenario':<26}{'n':>5}{'total net':>12}{'median':>10}{'vs CONTROL':>13}")
    n0, tot0, med0 = len(rows), sum(r["actual_net"] for r in rows), st.median(
        [r["actual_net"] for r in rows])
    print(f"  {'CONTROL (actual exit)':<26}{n0:>5}{tot0:>12,.0f}{med0:>10,.0f}{'—':>13}")
    for m in HORIZONS_MIN:
        n, tot, med = agg(f"hold_{m}m_net")
        print(f"  {'hold +' + str(m) + 'm':<26}{n:>5}{tot:>12,.0f}{med:>10,.0f}{tot - tot0:>13,.0f}")
    n, tot, med = agg("hold_close_net")
    print(f"  {'hold to session close':<26}{n:>5}{tot:>12,.0f}{med:>10,.0f}{tot - tot0:>13,.0f}")

    print(f"\n  by exit family (total net, CONTROL vs hold-to-close)")
    fams: dict[str, list[dict]] = {}
    for r in rows:
        fams.setdefault(r["exit_reason"] or "-", []).append(r)
    print(f"  {'exit reason':<24}{'n':>5}{'CONTROL':>11}{'to close':>11}{'delta':>10}")
    for f, rs in sorted(fams.items(), key=lambda kv: -len(kv[1])):
        a = sum(r["actual_net"] for r in rs)
        h = [r["hold_close_net"] for r in rs if r.get("hold_close_net") is not None]
        hv = sum(h) if h else 0.0
        print(f"  {f:<24}{len(rs):>5}{a:>11,.0f}{hv:>11,.0f}{hv - a:>10,.0f}")

    print("\n  NOTE: 'hold to close' is only available for positions closed before the")
    print("  session end. A position closed at squareoff has no forward window and")
    print("  is reported as n/a rather than as zero.")


if __name__ == "__main__":
    d = (dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
         else dt.date.today())
    asyncio.run(run(d))
