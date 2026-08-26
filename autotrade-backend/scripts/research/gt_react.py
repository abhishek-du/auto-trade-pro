"""Reaction measurement on the ground-truth NSE event set.

Study A (INTRADAY)  : T_public inside the session -> 1m reaction curve.
Study B (OVERNIGHT) : T_public after close -> next session's gap and day move.

NEUTRAL events are retained deliberately as the control group: an announcement
happened, but its category carries no direction. Comparing directional against
neutral is what separates "news moves stocks" from "these stocks move".
"""
import asyncio, json, warnings, datetime as dt, statistics as st
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
EV=json.load(open(f"{SP}/gt_events.json"))
for e in EV: e["pub"]=dt.datetime.fromisoformat(e["pub"])
BENCH="NIFTYBEES.NS"

async def load(pairs):
    out=defaultdict(list); pl=sorted(pairs)
    async with AsyncSessionLocal() as s:
        for i in range(0,len(pl),120):
            ch=pl[i:i+120]
            conds=" OR ".join(f"(symbol=:s{j} AND timestamp>=:a{j} AND timestamp<:b{j})" for j in range(len(ch)))
            p={}
            for j,(sym,d) in enumerate(ch):
                p[f"s{j}"]=sym; p[f"a{j}"]=dt.datetime.combine(d,dt.time(0,0)); p[f"b{j}"]=dt.datetime.combine(d,dt.time(23,59))
            for sym,ts,o,h,l,c in (await s.execute(text(
                f"SELECT symbol,timestamp,open,high,low,close FROM candles WHERE timeframe='1m' AND ({conds}) ORDER BY symbol,timestamp"),p)).fetchall():
                out[(sym,ts.date())].append((ts,float(o),float(h),float(l),float(c)))
    return out

async def main():
    async with AsyncSessionLocal() as s:
        sess=[r[0] for r in (await s.execute(text("""SELECT DISTINCT timestamp::date FROM candles
              WHERE timeframe='1m' ORDER BY 1"""))).fetchall()]
    nxt={sess[i]: sess[i+1] for i in range(len(sess)-1)}
    prv={sess[i+1]: sess[i] for i in range(len(sess)-1)}
    sset=set(sess)

    # ---------- Study A: intraday ----------
    A=[e for e in EV if e["window"]=="INTRADAY" and e["sym"] and e["pub"].date() in sset]
    pairsA={(e["sym"], e["pub"].date()) for e in A}
    # ---------- Study B: overnight ----------
    B=[e for e in EV if e["window"] in ("POSTMARKET","PREMARKET") and e["sym"]]
    for e in B:
        d=e["pub"].date()
        e["tgt"]= nxt.get(d) if e["window"]=="POSTMARKET" else (d if d in sset else nxt.get(d))
    B=[e for e in B if e.get("tgt") in sset]
    pairsB={(e["sym"], e["tgt"]) for e in B} | {(e["sym"], prv[e["tgt"]]) for e in B if e["tgt"] in prv}
    dates={d for _,d in pairsA} | {d for _,d in pairsB}
    print(f"loading bars: A={len(pairsA)} symbol-days, B={len(pairsB)} symbol-days")
    bars=await load(pairsA|pairsB)
    bench=await load({(BENCH,d) for d in dates})
    print(f"  benchmark days present: {len([d for d in dates if bench.get((BENCH,d))])}/{len(dates)}\n")

    def bmove(d, t0=None, t1=None):
        bl=bench.get((BENCH,d)) or []
        if not bl: return None
        seg=[b for b in bl if (t0 is None or b[0]>t0) and (t1 is None or b[0]<=t1)]
        if len(seg)<2: return None
        return (seg[-1][4]-seg[0][4])/seg[0][4]*100

    # ===== Study A =====
    HOR=[1,3,5,10,15,30,60]
    outA=[]
    for e in A:
        bl=bars.get((e["sym"], e["pub"].date())) or []
        pre=[b for b in bl if b[0]<=e["pub"]]; post=[b for b in bl if b[0]>e["pub"]]
        if not pre or len(post)<5: continue
        p0=pre[-1][4]
        if p0<=0: continue
        sgn = 1.0 if e["side"]=="LONG" else (-1.0 if e["side"]=="SHORT" else 1.0)
        r=dict(e); r["p0"]=p0
        for h in HOR:
            seg=[b for b in post if (b[0]-e["pub"]).total_seconds()<=h*60]
            r[f"r{h}"]= sgn*(seg[-1][4]-p0)/p0*100 if seg else None
            bm=bmove(e["pub"].date(), e["pub"], e["pub"]+dt.timedelta(minutes=h))
            r[f"x{h}"]= (r[f"r{h}"] - sgn*bm) if (r[f"r{h}"] is not None and bm is not None) else None
        r["rEOD"]=sgn*(post[-1][4]-p0)/p0*100
        bm=bmove(e["pub"].date(), e["pub"], None)
        r["xEOD"]= r["rEOD"]-sgn*bm if bm is not None else None
        r["absEOD"]=abs((post[-1][4]-p0)/p0*100)
        if sgn>0:
            r["mfe"]=max((b[2]-p0)/p0*100 for b in post); r["mae"]=min((b[3]-p0)/p0*100 for b in post)
        else:
            r["mfe"]=max((p0-b[3])/p0*100 for b in post); r["mae"]=min((p0-b[2])/p0*100 for b in post)
        # pre-event drift
        for W in (5,15,30):
            back=[b for b in pre if b[0]>=e["pub"]-dt.timedelta(minutes=W)]
            r[f"pre{W}"]= sgn*(pre[-1][4]-back[0][4])/back[0][4]*100 if len(back)>=2 and back[0][4]>0 else None
        outA.append(r)

    # ===== Study B =====
    outB=[]
    for e in B:
        tb=bars.get((e["sym"], e["tgt"])) or []
        pd_=prv.get(e["tgt"])
        pb=bars.get((e["sym"], pd_)) if pd_ else None
        if len(tb)<20 or not pb: continue
        pc=pb[-1][4]; op=tb[0][1]; cl=tb[-1][4]
        if pc<=0 or op<=0: continue
        sgn = 1.0 if e["side"]=="LONG" else (-1.0 if e["side"]=="SHORT" else 1.0)
        bl=bench.get((BENCH,e["tgt"])) or []; bpv=bench.get((BENCH,pd_)) if pd_ else None
        bgap=bday=None
        if bl and bpv:
            bpc=bpv[-1][4]; bop=bl[0][1]; bcl=bl[-1][4]
            if bpc>0 and bop>0: bgap=(bop-bpc)/bpc*100; bday=(bcl-bop)/bop*100
        r=dict(e)
        r["gap"]=sgn*(op-pc)/pc*100
        r["day"]=sgn*(cl-op)/op*100
        r["total"]=sgn*(cl-pc)/pc*100
        r["xgap"]= r["gap"]-sgn*bgap if bgap is not None else None
        r["xday"]= r["day"]-sgn*bday if bday is not None else None
        r["xtotal"]= r["total"]-sgn*((bgap or 0)+(bday or 0)) if bgap is not None else None
        r["absgap"]=abs((op-pc)/pc*100)
        outB.append(r)

    print(f"Study A (intraday)  observations: {len(outA)}  "
          f"[LONG {sum(1 for x in outA if x['side']=='LONG')} · SHORT {sum(1 for x in outA if x['side']=='SHORT')} · NEUTRAL {sum(1 for x in outA if x['side']=='NEUTRAL')}]")
    print(f"Study B (overnight) observations: {len(outB)}  "
          f"[LONG {sum(1 for x in outB if x['side']=='LONG')} · SHORT {sum(1 for x in outB if x['side']=='SHORT')} · NEUTRAL {sum(1 for x in outB if x['side']=='NEUTRAL')}]")
    for o in (outA,outB):
        for r in o: r["pub"]=r["pub"].isoformat(); r["tgt"]=str(r.get("tgt")) if r.get("tgt") else None
    json.dump(outA, open(f"{SP}/gtA.json","w")); json.dump(outB, open(f"{SP}/gtB.json","w"))
asyncio.run(main())
