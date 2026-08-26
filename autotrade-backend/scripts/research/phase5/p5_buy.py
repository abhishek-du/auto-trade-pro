"""PHASE 5 PART D-L — does the stored Hub `BUY` label carry repeatable
forward information?

BUY is defined ONLY as signal = 'BUY' from master_intelligence_scores. No score
threshold is substituted, no label is re-derived, nothing is tuned. Controls are
built from bars at or before the observation timestamp; no future information
enters candidate or control selection.
"""
import asyncio, json, sys, bisect, random, datetime as dt, statistics as st
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from sqlalchemy import text
from db.database import AsyncSessionLocal
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
HOR=[5,15,30,60,120,None]
SESSIONS=["2026-08-03","2026-08-04","2026-08-05","2026-08-06","2026-08-07",
          "2026-08-10","2026-08-11","2026-08-12","2026-08-13","2026-08-14",
          "2026-08-17","2026-08-18","2026-08-19","2026-08-20","2026-08-21",
          "2026-08-24","2026-08-25"]
LABELS=("BUY","STRONG_BUY","NEUTRAL","SELL")

def prestate(bars, times, sym, ts):
    t=times.get(sym)
    if not t: return None
    i=bisect.bisect_right(t,ts)-1
    if i<0: return None
    b=bars[sym]; px=b[i][1]
    if px<=0: return None
    if (ts-b[i][0]).total_seconds()>300: return None
    def ret(n):
        j=i-n
        return (px-b[j][1])/b[j][1]*100 if j>=0 and b[j][1]>0 else None
    w=b[max(0,i-14):i+1]
    rs=[(w[k][1]-w[k-1][1])/w[k-1][1] for k in range(1,len(w)) if w[k-1][1]>0]
    vol=st.pstdev(rs)*100 if len(rs)>=5 else None
    tv=sum(x[1]*x[2] for x in b[:i+1])
    return dict(i=i, px=px, r15=ret(15), vol=vol, tv=tv)

async def main():
    out=open(f"{SP}/p5_buy.jsonl","w"); total=0
    for ds in SESSIONS:
        d=dt.date.fromisoformat(ds)
        async with AsyncSessionLocal() as db:
            obs=(await db.execute(text("""
                SELECT DISTINCT ON (symbol, date_trunc('hour', bar_time))
                       symbol, bar_time, master_score, signal
                FROM master_intelligence_scores
                WHERE scored_at::date=:d AND master_score IS NOT NULL AND bar_time IS NOT NULL
                  AND signal = ANY(:labs) AND symbol LIKE '%.NS'
                ORDER BY symbol, date_trunc('hour', bar_time), bar_time"""),
                {"d": d, "labs": list(LABELS)})).fetchall()
            pool=[r[0] for r in (await db.execute(text("""
                SELECT symbol FROM candles WHERE timeframe='1m' AND timestamp::date=:d
                GROUP BY symbol HAVING COUNT(*)>250 AND SUM(volume*close)>2e8"""),
                {"d": d})).fetchall()]
            need=sorted({r[0] for r in obs} | set(pool))
            bars=defaultdict(list)
            for i in range(0,len(need),300):
                for s,t,c,v in (await db.execute(text("""
                    SELECT symbol,timestamp,close,volume FROM candles WHERE timeframe='1m'
                    AND timestamp::date=:d AND symbol = ANY(:s) ORDER BY symbol,timestamp"""),
                    {"d": d, "s": need[i:i+300]})).fetchall():
                    bars[s].append((t,float(c),float(v or 0)))
        times={k:[b[0] for b in v] for k,v in bars.items()}
        def fwd(sym,ts,h):
            t=times.get(sym)
            if not t: return None
            i=bisect.bisect_right(t,ts)-1
            if i<0 or i+1>=len(bars[sym]): return None
            ep=bars[sym][i][1]
            if ep<=0: return None
            j=min(i+h,len(bars[sym])-1) if h is not None else len(bars[sym])-1
            if j<=i: return None
            return (bars[sym][j][1]-ep)/ep*100
        # pre-state cache for the control pool, per distinct timestamp
        kept=0
        pcache={}
        for sym, bt, ms, sig in obs:
            me=prestate(bars,times,sym,bt)
            if me is None or me["r15"] is None or me["vol"] is None: continue
            f={str(h): fwd(sym,bt,h) for h in HOR}
            if f["5"] is None and f["None"] is None: continue
            # ── matched control: same instant, liquidity/return/vol bands ────
            key=bt
            if key not in pcache:
                pcache[key]=[(c, prestate(bars,times,c,bt)) for c in pool]
                pcache[key]=[(c,s_) for c,s_ in pcache[key]
                             if s_ and s_["r15"] is not None and s_["vol"] is not None and s_["tv"]>0]
            cands=[(c,s_) for c,s_ in pcache[key] if c!=sym]
            ctl={}
            if len(cands)>=20:
                g=[x for x in cands
                   if abs(x[1]["r15"]-me["r15"])<=0.15
                   and abs(x[1]["vol"]-me["vol"])<=0.20*max(me["vol"],0.02)
                   and 0.5<=x[1]["tv"]/max(me["tv"],1)<=2.0]
                if len(g)>=3:
                    random.shuffle(g); g=g[:5]
                    ctl={str(h): (lambda v:(sum(v)/len(v)) if v else None)(
                            [z for z in (fwd(x[0],bt,h) for x in g) if z is not None])
                         for h in HOR}
                    ctl["_n"]=len(g)
                    ctl["_r15"]=st.mean([x[1]["r15"] for x in g])
                    ctl["_vol"]=st.mean([x[1]["vol"] for x in g])
                    ctl["_tv"]=st.mean([x[1]["tv"] for x in g])
            # ── random timestamp, SAME symbol/session, ±60 bars (Part L) ─────
            lo=max(0,me["i"]-60); hi=min(len(bars[sym])-2, me["i"]+60)
            rt={}
            if hi>lo+5:
                j=random.randint(lo,hi)
                jts=bars[sym][j][0]
                rt={str(h): fwd(sym,jts,h) for h in HOR if h is not None}
            out.write(json.dumps(dict(d=ds,sym=sym,bt=str(bt),ms=float(ms),sig=sig,
                f=f,ctl=ctl,rt=rt,r15=me["r15"],vol=me["vol"],tv=me["tv"]))+"\n")
            kept+=1
        total+=kept
        print(f"  {ds}: {len(obs):>6} labelled obs -> {kept:>6} scored  (pool {len(pool)})", flush=True)
        del bars, times, pcache
    out.close(); print(f"\nTOTAL: {total}", flush=True)
asyncio.run(main())
