"""Parts 9-12: news. Split by WHEN the event arrived, never pooled.

  A  PRE-OPEN     crawled before 09:15 IST  -> measured from the 09:15 open
  B  POST-CLOSE   crawled after 15:30 IST   -> next session's open, if it exists
  C  IN-SESSION   crawled 09:15-15:30 IST

Direction comes from the causal_event that named the ticker; the LLM's own
verdict is NEVER used to select which events are scored (Part 12).
"""
import asyncio, json, sys, datetime as dt
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import Session
from sqlalchemy import text
from db.database import AsyncSessionLocal

HOR = [5, 15, 30, 60, None]
SESSIONS = ["2026-08-03","2026-08-04","2026-08-05","2026-08-06","2026-08-07",
            "2026-08-17","2026-08-18","2026-08-19","2026-08-20","2026-08-21",
            "2026-08-24","2026-08-25"]
OUT = "/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad/news_obs.jsonl"
def norm(x): return (x or "").upper().replace(".NS","").replace(".BO","").strip()

async def main():
    fh = open(OUT,"w"); total=0; bucket_tally=defaultdict(int)
    for ds in SESSIONS:
        d = dt.date.fromisoformat(ds)
        async with AsyncSessionLocal() as db:
            # events, with their news arrival time and category
            ev = (await db.execute(text("""
                SELECT ce.id, ce.created_at, ce.event_title, ce.importance, ce.confidence,
                       ce.bullish_stocks::text, ce.bearish_stocks::text,
                       ni.crawled_at, ni.source, ni.category, ni.sentiment
                FROM causal_events ce LEFT JOIN news_items ni ON ni.id = ce.news_id
                WHERE ce.created_at::date=:d"""), {"d": d})).fetchall()
            dec = (await db.execute(text("""
                SELECT symbol, ts, action, confidence, skip_reason
                FROM agent_decisions WHERE ts::date=:d"""), {"d": d})).fetchall()
            pool = [r[0] for r in (await db.execute(text("""
                SELECT symbol FROM candles WHERE timeframe='1m' AND timestamp::date=:d
                GROUP BY symbol HAVING COUNT(*) > 250 AND SUM(volume*close) > 2e8"""),
                {"d": d})).fetchall()]
        # build (ticker, direction, arrival) observations
        raw=[]
        for (eid, cts, title, imp, conf, bull, bear, crawled, src, cat, sent) in ev:
            arrival = crawled or cts
            for lst, dirn in ((bull,"LONG"),(bear,"SHORT")):
                try: tks = json.loads(lst) if lst else []
                except Exception: tks = []
                for t in tks:
                    if not t or " " in t: continue
                    raw.append((norm(t), dirn, arrival, imp, conf, src, cat, sent, eid))
        if not raw:
            print(f"  {ds}: EVIDENCE NOT AVAILABLE (no events)", flush=True); continue
        need = sorted({r[0]+".NS" for r in raw} | {r[0]+".BO" for r in raw} | set(pool))
        sess = Session()
        async with AsyncSessionLocal() as db:
            for i in range(0,len(need),300):
                rows=(await db.execute(text("""
                    SELECT symbol,timestamp,close,volume FROM candles
                    WHERE timeframe='1m' AND timestamp::date=:d AND symbol = ANY(:syms)
                    ORDER BY symbol,timestamp"""),{"d":d,"syms":need[i:i+300]})).fetchall()
                for sym,ts,cl,vol in rows: sess.bars[sym].append((ts,float(cl),float(vol or 0)))
        sess.finalise()
        # decision lookup by normalised symbol
        dmap=defaultdict(list)
        for sy,ts,act,cf,sr in dec: dmap[norm(sy)].append((ts,act,cf,sr))
        OPEN_U = dt.time(3,45); CLOSE_U = dt.time(10,0)     # 09:15 / 15:30 IST in UTC
        kept=0
        for (tk, dirn, arrival, imp, conf, src, cat, sent, eid) in raw:
            sym = tk+".NS" if (tk+".NS") in sess.bars else (tk+".BO" if (tk+".BO") in sess.bars else None)
            if sym is None: continue
            at = arrival.time()
            if at < OPEN_U:   bucket, entry_ts = "A_PRE_OPEN", sess.bars[sym][0][0]
            elif at > CLOSE_U: bucket, entry_ts = "B_POST_CLOSE", None
            else:              bucket, entry_ts = "C_IN_SESSION", arrival
            bucket_tally[bucket]+=1
            if entry_ts is None: continue          # B needs the next session — handled below
            is_long = dirn=="LONG"
            f = {str(h): sess.fwd(sym, entry_ts, h, is_long) for h in HOR}
            if f["5"] is None and f["None"] is None: continue
            # market baseline at the same instant
            import random as _r; _r.seed(hash(sym) & 0xffff)
            samp=_r.sample(pool,min(40,len(pool)))
            mkt={}
            for h in HOR:
                xs=[sess.fwd(c,entry_ts,h,is_long) for c in samp]; xs=[v for v in xs if v is not None]
                mkt[str(h)]=(sum(xs)/len(xs)) if xs else None
            # the agent's own verdict on this symbol that day, if any
            dd = dmap.get(tk, [])
            act = dd[0][1] if dd else None; dconf = dd[0][2] if dd else None
            fh.write(json.dumps(dict(d=ds,bucket=bucket,sym=sym,tk=tk,dirn=dirn,
                entry=str(entry_ts),imp=imp,econf=conf,src=src,cat=cat,sent=sent,
                act=act,dconf=dconf,f=f,mkt=mkt))+"\n")
            kept+=1
        total+=kept
        print(f"  {ds}: {len(raw):>5} event-tickers -> {kept:>5} scored", flush=True)
        del sess
    fh.close()
    print(f"\nTOTAL scored: {total}", flush=True)
    print("arrival buckets (all event-tickers, before candle filtering):", dict(bucket_tally), flush=True)
asyncio.run(main())
