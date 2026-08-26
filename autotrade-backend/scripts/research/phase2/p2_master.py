"""Parts 13-14: Master Intelligence as an INDEPENDENT historical signal.

Sampling rule, fixed before any result was seen: the FIRST score per
(symbol, hour). Scores are rewritten ~26x per symbol per session; using all of
them would inflate n without adding information and would weight heavily-scored
symbols arbitrarily. One per hour is deterministic and outcome-independent.
"""
import asyncio, json, sys, datetime as dt, statistics as st
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import Session
from sqlalchemy import text
from db.database import AsyncSessionLocal

HOR = [5, 15, 30, 60, None]
SESSIONS = ["2026-08-03","2026-08-04","2026-08-05","2026-08-06","2026-08-07",
            "2026-08-10","2026-08-11","2026-08-12","2026-08-13","2026-08-14",
            "2026-08-17","2026-08-18","2026-08-19","2026-08-20","2026-08-21",
            "2026-08-24","2026-08-25"]
OUT = "/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad/master_obs.jsonl"

async def main():
    fh = open(OUT, "w")
    total = 0
    for ds in SESSIONS:
        d = dt.date.fromisoformat(ds)
        async with AsyncSessionLocal() as db:
            scores = (await db.execute(text("""
                SELECT DISTINCT ON (symbol, date_trunc('hour', bar_time))
                       symbol, bar_time, master_score, signal, rank,
                       technical_score, news_score, sector_score, macro_score,
                       earnings_score, fundamental_score, is_blocked
                FROM master_intelligence_scores
                WHERE scored_at::date = :d AND master_score IS NOT NULL AND bar_time IS NOT NULL
                ORDER BY symbol, date_trunc('hour', bar_time), bar_time"""), {"d": d})).fetchall()
        if not scores:
            print(f"  {ds}: EVIDENCE NOT AVAILABLE (no scores)", flush=True); continue
        syms = sorted({r[0] for r in scores})
        sess = Session()
        async with AsyncSessionLocal() as db:
            for i in range(0, len(syms), 300):
                ch = syms[i:i+300]
                rows = (await db.execute(text("""
                    SELECT symbol, timestamp, close, volume FROM candles
                    WHERE timeframe='1m' AND timestamp::date=:d AND symbol = ANY(:syms)
                    ORDER BY symbol, timestamp"""), {"d": d, "syms": ch})).fetchall()
                for sym, ts, cl, vol in rows:
                    sess.bars[sym].append((ts, float(cl), float(vol or 0)))
        sess.finalise()
        kept = 0
        for r in scores:
            sym, bt, ms = r[0], r[1], float(r[2])
            f = {str(h): sess.fwd(sym, bt, h, True) for h in HOR}
            if f["5"] is None and f["None"] is None:
                continue
            mfe, mae, tf, tm = sess.excursions(sym, bt, 60, True)
            fh.write(json.dumps(dict(
                d=ds, sym=sym, bt=str(bt), ms=ms, sig=r[3], rank=r[4],
                blocked=bool(r[11]),
                tech=r[5], news=r[6], sector=r[7], macro=r[8], earn=r[9], fund=r[10],
                f=f, mfe=mfe, mae=mae, tv=sess.tv.get(sym, 0.0))) + "\n")
            kept += 1
        total += kept
        print(f"  {ds}: {len(scores):>6} sampled scores -> {kept:>6} usable  "
              f"({len(sess.bars)} symbols with 1m data)", flush=True)
        del sess
    fh.close()
    print(f"\nTOTAL: {total} observations", flush=True)
asyncio.run(main())
