"""V2 live-session monitor. READ-ONLY. Answers one question per section.

The 2026-08-27 session is the first to run TRADING_STRATEGY_MODE=V2. The point
of watching it is NOT to see whether the day was profitable — one session
cannot answer that, and the Phase-25 replay found only three of thirty-four
trades separating the horizons at all.

The point is to catch the ways V2 could be silently WRONG:

  1. the gate never fires        -> configured but unreachable, day is wasted
  2. the gate fires too widely   -> a hard stop or invalidation got deferred,
                                    which is the dangerous failure
  3. the gate leaks              -> a PROFIT_MANAGEMENT exit closed before
                                    the minimum hold, so V2 is not in force
  4. the rank capture is silent  -> or records the wrong band again
  5. anything the capture logged actually traded

Sections 2, 3 and 5 are ALARMS. Everything else is observation.

READ-ONLY: SELECTs and log greps. It imports no execution path, constructs no
trade or position, and writes nothing at all -- not even a research row.
tests/test_research_scripts_cannot_trade.py enforces the first two.

USAGE
    cd autotrade-backend
    PYTHONPATH=$PWD .venv/bin/python scripts/research/v2_session_monitor.py [YYYY-MM-DD]
"""
from __future__ import annotations

import asyncio
import datetime as dt
import subprocess
import sys

from sqlalchemy import text

from db.database import AsyncSessionLocal
from engine.exit_policy import ExitFamily, classify, describe

IST = "at time zone 'UTC' at time zone 'Asia/Kolkata'"
WORKER_LOGS = ("/tmp/celery_worker.log", "/tmp/exit_worker.log",
               "/tmp/trade_worker.log", "/tmp/scan_worker.log")

ALARMS: list[str] = []


def _h(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _grep_count(pattern: str) -> int:
    n = 0
    for f in WORKER_LOGS:
        try:
            out = subprocess.run(["grep", "-c", "-E", pattern, f],
                                 capture_output=True, text=True, timeout=30)
            n += int(out.stdout.strip() or 0)
        except Exception:
            continue
    return n


async def section_1_config() -> None:
    _h("1. IS V2 ACTUALLY IN FORCE?")
    d = describe()
    print(f"  mode             = {d['mode']}")
    print(f"  min_hold_minutes = {d['min_hold_minutes']:.0f}")
    print(f"  gated family     = {d['gated_family']}")
    if d["mode"] != "V2":
        ALARMS.append(f"mode is {d['mode']}, not V2 — the experiment is NOT running")
    if d["min_hold_minutes"] != 120:
        ALARMS.append(f"min_hold is {d['min_hold_minutes']}, expected 120")

    deferred = _grep_count(r"\[exit_policy\].*deferred")
    print(f"\n  '[exit_policy] ... deferred' lines in worker logs: {deferred}")
    print("  (0 before any position reaches a profit target is normal;")
    print("   0 after several profitable positions means the gate is unreachable)")


async def section_2_leaks(session, day) -> None:
    _h("2. ALARM — did any PROFIT_MANAGEMENT exit close before 120 minutes?")
    rows = (await session.execute(text(f"""
        SELECT symbol, exit_reason, pnl,
               round((EXTRACT(epoch FROM (closed_at - opened_at)) / 60)::numeric, 1) held_min,
               (closed_at {IST}) closed_ist,
               indicator_snapshot -> 'exit_meta' ->> 'exit_family'   fam,
               indicator_snapshot -> 'exit_meta' ->> 'strategy_mode' mode
        FROM paper_trades
        WHERE status != 'OPEN' AND (closed_at {IST})::date = :d
        ORDER BY closed_at
    """), {"d": day})).all()

    if not rows:
        print("  no trades closed yet today")
        return

    leaks = []
    for r in rows:
        fam = r.fam or classify(r.exit_reason)
        if fam == ExitFamily.PROFIT_MANAGEMENT and (r.held_min or 0) < 120 \
                and (r.mode or "") == "V2":
            leaks.append(r)

    print(f"  {'symbol':<16}{'reason':<22}{'family':<20}{'held':>8}{'pnl':>10}")
    for r in rows:
        fam = r.fam or classify(r.exit_reason) + " (derived)"
        mark = "  <== LEAK" if r in leaks else ""
        print(f"  {r.symbol:<16}{(r.exit_reason or '-'):<22}{fam:<20}"
              f"{(r.held_min or 0):>7.0f}m{float(r.pnl or 0):>10,.0f}{mark}")

    if leaks:
        ALARMS.append(
            f"{len(leaks)} PROFIT_MANAGEMENT exit(s) closed before 120m under V2 — "
            f"the gate is being bypassed: "
            + ", ".join(f"{r.symbol}/{r.exit_reason}@{r.held_min:.0f}m" for r in leaks)
        )
    else:
        print("\n  OK — no profit-management exit closed inside the hold window")


async def section_3_over_suppression(session, day) -> None:
    _h("3. ALARM — was anything suppressed that must NEVER be?")
    print("  V2 may defer ONLY: TAKE_PROFIT TRAIL_STOP EXHAUSTION")
    print("                     T1_REVERSAL_EXIT T1_HIT T2_HIT")

    open_rows = (await session.execute(text(f"""
        SELECT p.symbol, p.unrealised_pct, p.stop_loss, p.current_price,
               p.direction::text dir,
               round((EXTRACT(epoch FROM (now() - p.opened_at)) / 60)::numeric, 1) age_min
        FROM open_positions p ORDER BY p.opened_at
    """))).all()

    print(f"\n  open positions: {len(open_rows)}")
    breached = []
    for r in open_rows:
        long = "SELL" not in (r.dir or "").upper()
        px, sl = float(r.current_price or 0), float(r.stop_loss or 0)
        past_stop = (long and px < sl) or ((not long) and px > sl) if sl > 0 else False
        if past_stop:
            breached.append(r)
        print(f"  {r.symbol:<16}{(r.age_min or 0):>7.0f}m  "
              f"upnl={float(r.unrealised_pct or 0):+6.2f}%  px={px:.2f}  sl={sl:.2f}"
              f"{'  <== PAST STOP' if past_stop else ''}")

    if breached:
        ALARMS.append(
            "position(s) trading beyond the hard stop and still open — V2 must "
            "NEVER defer a HARD_STOP: "
            + ", ".join(r.symbol for r in breached)
        )
    elif open_rows:
        print("\n  OK — every open position is on the correct side of its stop")


async def section_4_families(session, day) -> None:
    _h("4. EXIT FAMILY MIX — the actual experimental readout")
    rows = (await session.execute(text(f"""
        SELECT COALESCE(indicator_snapshot -> 'exit_meta' ->> 'exit_family', '(untagged)') fam,
               count(*) n, round(sum(pnl)::numeric, 0) pnl,
               round(avg(EXTRACT(epoch FROM (closed_at - opened_at)) / 60)::numeric, 0) avg_min
        FROM paper_trades
        WHERE status != 'OPEN' AND (closed_at {IST})::date = :d
        GROUP BY 1 ORDER BY n DESC
    """), {"d": day})).all()
    if not rows:
        print("  nothing closed yet")
        return
    print(f"  {'family':<24}{'n':>5}{'net pnl':>12}{'avg hold':>11}")
    for r in rows:
        print(f"  {r.fam:<24}{r.n:>5}{float(r.pnl or 0):>12,.0f}{(r.avg_min or 0):>10.0f}m")
    print("\n  Compare against the CONTROL baseline: EXHAUSTION was 17 of 43 exits")
    print("  historically. If PROFIT_MANAGEMENT's share has dropped and TIME/")
    print("  SQUAREOFF has risen, the gate is doing what it was built to do.")


async def section_5_rank_overflow(session, day) -> None:
    _h("5. RANK 16-40 CAPTURE — recorded, and provably not traded")
    rows = (await session.execute(text(f"""
        SELECT data FROM simulation_logs
        WHERE event_type = 'TACTICAL_RANK_OVERFLOW'
          AND (timestamp {IST})::date = :d
        ORDER BY timestamp DESC LIMIT 200
    """), {"d": day})).all()

    if not rows:
        print("  no overflow rows yet (none expected before the first scan with")
        print("  more than TACTICAL_TOP_N qualifying signals)")
        return

    ranks, syms = [], set()
    for (data,) in rows:
        for s in (data or {}).get("signals", []):
            ranks.append(s.get("rank"))
            syms.add(s.get("symbol"))
    print(f"  rows={len(rows)}  signals={len(ranks)}  distinct symbols={len(syms)}")
    if ranks:
        print(f"  rank range = {min(ranks)} .. {max(ranks)}   (expected to start at 16)")
        if min(ranks) != 16:
            ALARMS.append(f"overflow ranks start at {min(ranks)}, expected 16 — "
                          f"the capture is aimed at the wrong band again")

    if syms:
        traded = (await session.execute(text(f"""
            SELECT DISTINCT symbol FROM paper_trades
            WHERE (opened_at {IST})::date = :d AND symbol = ANY(:syms)
        """), {"d": day, "syms": list(syms)})).all()
        if traded:
            ALARMS.append(
                "symbols recorded ONLY for research were traded: "
                + ", ".join(t.symbol for t in traded)
            )
        else:
            print("  OK — none of the captured symbols opened a position")


async def section_6_plumbing(session, day) -> None:
    _h("6. PLUMBING — funnel, MFE source, costs")
    f = (await session.execute(text(f"""
        SELECT count(*) n,
               max((data ->> 'universe')::int)  uni,
               max((data ->> 'scanned')::int)   scanned,
               max((data ->> 'persisted')::int) persisted
        FROM simulation_logs
        WHERE event_type = 'TACTICAL_SCAN_FUNNEL' AND (timestamp {IST})::date = :d
    """), {"d": day})).first()
    print(f"  scan funnel rows = {f.n}  (universe={f.uni} scanned={f.scanned} "
          f"persisted={f.persisted})")
    if f.n == 0:
        print("  NOTE: zero funnel rows during market hours means scans are not running")

    m = (await session.execute(text(f"""
        SELECT COALESCE(indicator_snapshot -> 'exit_meta' ->> 'mfe_src', '(none)') src,
               count(*) n
        FROM paper_trades
        WHERE status != 'OPEN' AND (closed_at {IST})::date = :d
        GROUP BY 1 ORDER BY n DESC
    """), {"d": day})).all()
    print("  MFE source: " + (", ".join(f"{r.src}={r.n}" for r in m) or "no closes yet"))

    c = (await session.execute(text(f"""
        SELECT product, count(*) n,
               round(avg(ABS(pnl) / NULLIF(entry_price * size_units, 0) * 100)::numeric, 3) pct
        FROM paper_trades
        WHERE status != 'OPEN' AND (closed_at {IST})::date = :d
        GROUP BY 1
    """), {"d": day})).all()
    for r in c:
        print(f"  {r.product}: {r.n} closes, mean |pnl| = {r.pct}% of notional")


async def run(day: dt.date) -> None:
    print(f"\nV2 SESSION MONITOR — {day}   "
          f"(generated {dt.datetime.now().strftime('%H:%M:%S')} IST)")
    await section_1_config()
    async with AsyncSessionLocal() as s:
        await section_2_leaks(s, day)
        await section_3_over_suppression(s, day)
        await section_4_families(s, day)
        await section_5_rank_overflow(s, day)
        await section_6_plumbing(s, day)

    _h("VERDICT")
    if ALARMS:
        print("  *** ALARMS ***")
        for a in ALARMS:
            print(f"   - {a}")
    else:
        print("  No alarms. V2 is behaving as designed so far.")
    print("\n  Reminder: a profitable day is NOT evidence for V2, and a losing day")
    print("  is NOT evidence against it. One session cannot separate the horizons.")


if __name__ == "__main__":
    d = (dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today())
    asyncio.run(run(d))
