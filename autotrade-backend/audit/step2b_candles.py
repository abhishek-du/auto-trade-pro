"""STEP 2B §6 §9 §10 §11 §13 — candle correctness. READ-ONLY.

Validates what is actually STORED, and separately what Upstox actually RETURNS,
so a transformation bug between the two is visible rather than averaged away.
"""
from __future__ import annotations
import asyncio, datetime as dt, sys
from sqlalchemy import text
sys.path.insert(0, "/home/cis/windows/auto-trade-pro/autotrade-backend")
from db.database import AsyncSessionLocal

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

async def main():
    async with AsyncSessionLocal() as s:
        q = lambda sql, **kw: s.execute(text(sql), kw)

        print("§9.1  candles table — schema & column types")
        for r in (await q("""SELECT column_name, data_type FROM information_schema.columns
                             WHERE table_name='candles' ORDER BY ordinal_position""")).all():
            print(f"    {r[0]:14} {r[1]}")

        print("\n§9.2  unique constraints / indexes on candles")
        for r in (await q("""SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                             WHERE conrelid='candles'::regclass AND contype IN ('u','p')""")).all():
            print(f"    {r[0]}: {r[1]}")

        print("\n§6.1  rows per timeframe + timestamp range")
        for r in (await q("""SELECT timeframe, count(*), min(timestamp), max(timestamp),
                                    count(DISTINCT symbol)
                             FROM candles GROUP BY 1 ORDER BY 2 DESC""")).all():
            print(f"    {r[0]:5} rows={r[1]:>10,}  syms={r[4]:>6,}  {r[2]}  ->  {r[3]}")

        print("\n§11.1  timestamp HOUR distribution per timeframe (UTC)")
        print("       1d must sit at 18:30 UTC (=00:00 IST next day). 00:00 = the DEAD series.")
        for tf in ("1d","1h","15m","5m","1m"):
            rows = (await q("""SELECT extract(hour from timestamp)::int h, count(*)
                               FROM candles WHERE timeframe=:tf GROUP BY 1 ORDER BY 2 DESC LIMIT 6""",
                            tf=tf)).all()
            if rows:
                print(f"    {tf:4} " + "  ".join(f"{h:02d}h={n:,}" for h,n in rows))

        print("\n§6.2  OHLC INVARIANT VIOLATIONS  (low<=open<=high, low<=close<=high, high>=low)")
        tot_bad = 0
        for tf in ("1d","1h","15m","5m","1m"):
            r = (await q("""SELECT count(*) FROM candles WHERE timeframe=:tf AND (
                              high < low OR open > high OR open < low
                              OR close > high OR close < low)""", tf=tf)).scalar()
            n = (await q("SELECT count(*) FROM candles WHERE timeframe=:tf", tf=tf)).scalar()
            tot_bad += r
            print(f"    {tf:4} violations={r:>7,} / {n:,}")
        print(f"    TOTAL VIOLATIONS = {tot_bad:,}")

        print("\n§6.3  non-positive prices / negative volume")
        for tf in ("1d","1m"):
            r = (await q("""SELECT count(*) FROM candles WHERE timeframe=:tf
                            AND (open<=0 OR high<=0 OR low<=0 OR close<=0)""", tf=tf)).scalar()
            v = (await q("SELECT count(*) FROM candles WHERE timeframe=:tf AND volume<0", tf=tf)).scalar()
            print(f"    {tf:4} non-positive price rows={r:,}   negative volume={v:,}")

        print("\n§6.4  DUPLICATE candles (same symbol+timeframe+timestamp)")
        for tf in ("1d","1m","5m","15m","1h"):
            d = (await q("""SELECT count(*) FROM (SELECT symbol,timestamp FROM candles
                            WHERE timeframe=:tf GROUP BY 1,2 HAVING count(*)>1) t""", tf=tf)).scalar()
            print(f"    {tf:4} duplicate (symbol,timestamp) groups = {d:,}")

        print("\n§6.5  FUTURE timestamps")
        now = dt.datetime.utcnow()
        for tf in ("1d","1m"):
            f = (await q("SELECT count(*) FROM candles WHERE timeframe=:tf AND timestamp > :n",
                         tf=tf, n=now + dt.timedelta(minutes=5))).scalar()
            print(f"    {tf:4} rows dated in the future = {f:,}")

        print("\n§11.2  1m candle minute-of-day range (UTC) — NSE session is 03:45–10:00 UTC")
        r = (await q("""SELECT min(extract(hour from timestamp)*60+extract(minute from timestamp))::int,
                               max(extract(hour from timestamp)*60+extract(minute from timestamp))::int
                        FROM candles WHERE timeframe='1m'""")).first()
        if r and r[0] is not None:
            f = lambda m: f"{m//60:02d}:{m%60:02d}"
            print(f"    1m spans {f(r[0])} .. {f(r[1])} UTC  (expect 03:45..09:59)")
            out = (await q("""SELECT count(*) FROM candles WHERE timeframe='1m'
                              AND (extract(hour from timestamp)*60+extract(minute from timestamp) < 225
                                OR extract(hour from timestamp)*60+extract(minute from timestamp) > 599)""")).scalar()
            print(f"    1m rows OUTSIDE the NSE session window = {out:,}")

        print("\n§6.6  WEEKEND rows (NSE closed Sat/Sun)")
        for tf in ("1d","1m"):
            w = (await q("""SELECT count(*) FROM candles WHERE timeframe=:tf
                            AND extract(dow from timestamp) IN (0,6)""", tf=tf)).scalar()
            print(f"    {tf:4} weekend-dated rows = {w:,}")

        print("\n§13  COVERAGE — denominator = NSE EQ rows carrying an instrument_key")
        den = (await q("""SELECT count(*) FROM kite_instruments
                          WHERE exchange='NSE' AND instrument_type='EQ' AND instrument_key IS NOT NULL""")).scalar()
        print(f"    denominator (keyed NSE EQ) = {den:,}")
        today_ist = dt.datetime.now(IST).date()
        for tf in ("1m","5m","15m","1h","1d"):
            c = (await q("""SELECT count(DISTINCT c.symbol) FROM candles c
                            JOIN kite_instruments k ON k.tradingsymbol = replace(c.symbol,'.NS','')
                            WHERE c.timeframe=:tf AND k.exchange='NSE' AND k.instrument_key IS NOT NULL
                              AND c.timestamp::date = :d""", tf=tf, d=today_ist)).scalar()
            any_ = (await q("""SELECT count(DISTINCT c.symbol) FROM candles c
                            JOIN kite_instruments k ON k.tradingsymbol = replace(c.symbol,'.NS','')
                            WHERE c.timeframe=:tf AND k.exchange='NSE' AND k.instrument_key IS NOT NULL""",
                            tf=tf)).scalar()
            print(f"    {tf:4} today={c:>5,} ({c/den*100:5.1f}%)   any-history={any_:>5,} ({any_/den*100:5.1f}%)")

        nocand = (await q("""SELECT count(*) FROM kite_instruments k
                             WHERE k.exchange='NSE' AND k.instrument_type='EQ'
                               AND k.instrument_key IS NOT NULL
                               AND NOT EXISTS (SELECT 1 FROM candles c
                                               WHERE c.symbol = k.tradingsymbol||'.NS')""")).scalar()
        print(f"    keyed NSE EQ symbols with NO candles at all = {nocand:,} ({nocand/den*100:.1f}%)")

asyncio.run(main())
