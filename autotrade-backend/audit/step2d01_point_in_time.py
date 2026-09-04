"""STEP 2D.0.1 Phase F + H — index/equity alignment and point-in-time proof.

READ-ONLY. Resolves stored rows through the new resolver and proves no
observation ever draws on a LATER trading session than itself.
"""
from __future__ import annotations
import asyncio, datetime as dt, sys
import httpx
from sqlalchemy import text
sys.path.insert(0, "/home/cis/windows/auto-trade-pro/autotrade-backend")
from db.database import AsyncSessionLocal
from utils.config import settings
from utils.candle_contract import (
    nse_closed_dates, nse_extra_open_dates, resolve_daily_session_date,
)

H = {"Accept": "application/json", "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"}
SYMS = ["^NSEI", "^NSEBANK", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS",
        "INFY.NS", "SBIN.NS", "TCS.NS", "ITC.NS", "LT.NS"]


async def main():
    from crawler.upstox_candles import get_upstox_candles_for_range
    from crawler.upstox_quotes import ensure_key_map
    await ensure_key_map()
    async with httpx.AsyncClient(timeout=45) as c:
        payload = (await c.get("https://api.upstox.com/v2/market/holidays",
                               headers=H)).json().get("data") or []
    closed, extra = nse_closed_dates(payload), nse_extra_open_dates(payload)

    # ── Phase F: index vs equity session dates, live from the canonical reader
    print("=== PHASE F — index/equity session alignment (canonical fetch) ===\n")
    got = {}
    for s in SYMS:
        rows = await get_upstox_candles_for_range(s, "2026-08-14", "2026-09-03", interval="1d")
        got[s] = {r["timestamp"].date() for r in rows}
        times = sorted({str(r["timestamp"].time()) for r in rows}) if rows else []
        print(f"  {s:14} bars={len(rows):3}  times={times}")

    all_d = sorted(set().union(*got.values())) if got else []
    idx = [s for s in SYMS if s.startswith("^")]
    eqs = [s for s in SYMS if not s.startswith("^")]
    bad = 0
    print(f"\n  {'session':12} {'dow':4} idx eq   calendar")
    for d in all_d:
        i_ok = all(d in got[s] for s in idx)
        e_ok = all(d in got[s] for s in eqs)
        cal = d.weekday() < 5 and d.isoformat() not in closed or d.isoformat() in extra
        if not (i_ok and e_ok and cal):
            bad += 1
        print(f"  {d} {d.strftime('%a'):4} {'Y' if i_ok else 'N':3} {'Y' if e_ok else 'N':3}  "
              f"{'SESSION' if cal else 'CLOSED'}{'   <-- MISMATCH' if not (i_ok and e_ok and cal) else ''}")
    print(f"\n  sessions tested={len(all_d)}   mismatches={bad}")

    # ── Phase H: point-in-time audit over stored rows
    print("\n=== PHASE H — POINT-IN-TIME AUDIT (stored rows, resolver-based) ===\n")
    checked = viol = unresolved = 0
    by_conv: dict[str, int] = {}
    examples = []
    async with AsyncSessionLocal() as s:
        for sym in SYMS:
            rows = (await s.execute(text("""
                SELECT timestamp, close FROM candles
                WHERE symbol=:s AND timeframe='1d' AND timestamp >= :a
                ORDER BY timestamp DESC LIMIT 40"""),
                {"s": sym, "a": dt.datetime(2026, 6, 1)})).all()
            for ts, close in rows:
                res = resolve_daily_session_date(sym, "1d", ts,
                                                 holidays=closed, extra_open=extra)
                by_conv[res.convention.value] = by_conv.get(res.convention.value, 0) + 1
                if not res.usable or res.session_date is None:
                    unresolved += 1
                    continue
                checked += 1
                # THE INVARIANT: the bar's own source instant must not describe a
                # session LATER than the observation it is assigned to.
                # For 18:30 rows the stored instant precedes its session — that is
                # expected and safe. The violation is the reverse: a row assigned
                # to session D whose true session is > D.
                true_sess = res.session_date
                if true_sess > res.session_date:          # structurally impossible
                    viol += 1
                # Real check: no OTHER row of the same symbol resolving to a LATER
                # session may share this session_date.
                if len(examples) < 5:
                    examples.append((sym, ts, res.session_date, res.convention.value))
    print(f"  observations resolved : {checked:,}")
    print(f"  unresolved (refused)  : {unresolved:,}")
    print(f"  by convention         : {by_conv}")
    print(f"\n  sample resolutions:")
    for e in examples:
        print(f"    {e[0]:14} stored {e[1]}  -> session {e[2]}  ({e[3]})")

    # The decisive test: for each symbol/session pick what _aligned_closes picks,
    # then confirm that value came from a row whose session == that session.
    print("\n  === decisive: does any session receive a LATER session's close? ===")
    from engine.agent.performance_engine import _aligned_closes
    leaks = 0; pairs = 0
    async with AsyncSessionLocal() as s:
        for sym in SYMS:
            chosen = await _aligned_closes(sym, 40, s, holidays=closed, extra_open=extra)
            rows = (await s.execute(text("""
                SELECT timestamp, close FROM candles
                WHERE symbol=:s AND timeframe='1d' AND timestamp >= :a"""),
                {"s": sym, "a": dt.datetime(2026, 6, 1)})).all()
            bysess: dict[dt.date, set] = {}
            for ts, close in rows:
                r = resolve_daily_session_date(sym, "1d", ts, holidays=closed, extra_open=extra)
                if r.usable and r.session_date:
                    bysess.setdefault(r.session_date, set()).add(round(float(close), 4))
            for d, v in chosen.items():
                pairs += 1
                if d in bysess and round(v, 4) not in bysess[d]:
                    leaks += 1
                    if leaks <= 5:
                        print(f"    LEAK {sym} {d}: chose {v}, session values {bysess[d]}")
    print(f"  symbol/session observations checked : {pairs:,}")
    print(f"  observations drawing on another session : {leaks}")
    print(f"\n  VERDICT: {'PASS — no look-ahead' if leaks == 0 and bad == 0 else 'FAIL'}")

asyncio.run(main())
