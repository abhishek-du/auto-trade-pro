"""STEP 2C §7 — DRY RUN. Reads only; writes nothing.

Proposed mapping, live daily series only:
    1d @ 18:30 UTC  ->  (that date + 1 day) at 03:45 UTC
i.e. re-anchor the date label to the session's OPEN instant.

Scope note: the 00:00 UTC daily series is NOT touched. It is a different
pipeline with a different (and separately broken — Step 2B blocker B1,
pre-split prices) convention. Mixing the two fixes would confuse two distinct
defects.
"""
from __future__ import annotations
import asyncio, datetime as dt, sys
import httpx
from sqlalchemy import text
sys.path.insert(0, "/home/cis/windows/auto-trade-pro/autotrade-backend")
from db.database import AsyncSessionLocal
from utils.config import settings

MAP = ("date_trunc('day', timestamp + interval '1 day') + interval '3 hours 45 minutes'")
# SCOPE: NSE EQUITIES ONLY.
#
# The first dry run blocked on 4,104 rows that would have landed on a weekend.
# Investigating rather than overriding showed they are all GOVERNMENT SECURITIES
# (719GS2060-GS, 71GS2034-GS, ...) written by a one-off backfill in June/July
# 2026 under a different timestamp convention. Genuine equity rows occupy only
# Sun-Thu, exactly as a consistent offset pipeline must.
#
# This is an instrument-CLASS scope, not a symbol-specific exception: it is the
# same series-suffix filter the universe sync already uses, and non-equity debt
# is out of scope for this system by contract.
_EQUITY = (r"c.symbol ~ '^[A-Z][A-Z0-9&*.]*\.NS$' "          # plain NSE equity ticker
           r"AND symbol !~ '-[A-Z0-9]{2}\.NS$' "            # no -GS/-SG/-BE/-SM/-N0 series
           r"AND c.symbol !~ '^[0-9]'")                        # no numeric-coded debt
# Unaliased form for the simple aggregate queries (they use no alias).
LIVE = ("timeframe='1d' AND to_char(timestamp,'HH24:MI')='18:30' AND "
        + _EQUITY.replace("c.", ""))


async def main():
    # Independent NSE calendar — not from our DB.
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://api.upstox.com/v2/market/holidays",
                        headers={"Accept": "application/json",
                                 "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"})
    holidays = {h["date"] for h in (r.json().get("data") or [])}

    async with AsyncSessionLocal() as s:
        q = lambda sql, **kw: s.execute(text(sql), kw)

        rows = (await q(f"SELECT count(*), count(DISTINCT symbol) FROM candles WHERE {LIVE}")).first()
        print(f"§7.1  SCOPE\n    rows affected    = {rows[0]:,}\n    symbols affected = {rows[1]:,}")
        r2 = (await q(f"SELECT min(timestamp), max(timestamp) FROM candles WHERE {LIVE}")).first()
        print(f"    date range       = {r2[0]}  ..  {r2[1]}")

        print("\n§7.2  SAMPLE old -> new")
        for r3 in (await q(f"""SELECT symbol, timestamp old, {MAP} new, close
                               FROM candles WHERE {LIVE} AND symbol='RELIANCE.NS'
                               ORDER BY timestamp DESC LIMIT 6""")).all():
            print(f"    {r3[0]:14} {r3[1]}  ->  {r3[2]}   ({r3[2].strftime('%a')})  close={r3[3]}")

        print("\n§7.3  SAFETY CHECKS")
        # Explicit aliases. An earlier version built this by string-replacing
        # column names into the predicate, which silently mis-qualified them
        # once the predicate grew a `symbol` regex — it reported 0 collisions
        # when there were 2,762.
        dup = (await q(f"""SELECT count(*) FROM candles c
                 WHERE c.timeframe='1d' AND to_char(c.timestamp,'HH24:MI')='18:30'
                   AND {_EQUITY}
                   AND EXISTS (SELECT 1 FROM candles b
                     WHERE b.symbol=c.symbol AND b.timeframe='1d'
                       AND b.timestamp = date_trunc('day', c.timestamp + interval '1 day')
                                         + interval '3 hours 45 minutes')""")).scalar()
        print(f"    collisions with existing rows          = {dup:,}   {'OK' if dup==0 else 'BLOCKER'}")

        internal = (await q(f"""SELECT count(*) FROM (
                    SELECT symbol, {MAP} nt FROM candles WHERE {LIVE}
                    GROUP BY 1,2 HAVING count(*)>1) t""")).scalar()
        print(f"    duplicate (symbol,new_ts) within batch = {internal:,}   {'OK' if internal==0 else 'BLOCKER'}")

        fut = (await q(f"SELECT count(*) FROM candles WHERE {LIVE} AND {MAP} > (now() at time zone 'utc')")).scalar()
        print(f"    new timestamps in the future           = {fut:,}   {'OK' if fut==0 else 'REVIEW'}")

        wknd = (await q(f"""SELECT count(*) FROM candles WHERE {LIVE}
                            AND extract(dow from {MAP}) IN (0,6)""")).scalar()
        print(f"    new timestamps landing on a weekend    = {wknd:,}   {'OK' if wknd==0 else 'BLOCKER'}")

        # Holiday validation against the independent calendar.
        dates = [str(r4[0]) for r4 in (await q(f"""
            SELECT DISTINCT ({MAP})::date d FROM candles WHERE {LIVE}
              AND timestamp >= '2026-01-01' ORDER BY 1""")).all()]
        onhol = [d for d in dates if d in holidays]
        print(f"    new dates falling on an NSE holiday    = {len(onhol):,}   {'OK' if not onhol else 'REVIEW'}")
        if onhol:
            print(f"      {onhol[:8]}")

        print("\n§7.4  CURRENT (pre-migration) session validity, same population")
        w0 = (await q(f"SELECT count(*) FROM candles WHERE {LIVE} AND extract(dow from timestamp) IN (0,6)")).scalar()
        d0 = [str(r5[0]) for r5 in (await q(f"""
            SELECT DISTINCT timestamp::date d FROM candles WHERE {LIVE}
              AND timestamp >= '2026-01-01' ORDER BY 1""")).all()]
        h0 = [d for d in d0 if d in holidays]
        print(f"    weekend-dated rows TODAY               = {w0:,}")
        print(f"    dates on an NSE holiday TODAY          = {len(h0):,}  {h0[:6]}")

        print("\n§7.5  OHLC/VOLUME PRESERVATION")
        print("    The proposed UPDATE touches the `timestamp` column only;")
        print("    open/high/low/close/volume are not referenced in the SET clause.")
        chk = (await q(f"""SELECT round(sum(open)::numeric,2), round(sum(high)::numeric,2),
                                  round(sum(low)::numeric,2), round(sum(close)::numeric,2),
                                  round(sum(volume)::numeric,2), count(*)
                           FROM candles WHERE {LIVE}""")).first()
        print(f"    pre-migration checksums: O={chk[0]} H={chk[1]} L={chk[2]} C={chk[3]} V={chk[4]} n={chk[5]:,}")
        print("    (re-computed after migration and compared)")

        verdict = "SAFE TO MIGRATE" if (dup == 0 and internal == 0 and wknd == 0 and fut == 0 and not onhol) else "MIGRATION_BLOCKED"
        print(f"\n§7.6  DRY-RUN VERDICT: {verdict}")

asyncio.run(main())
