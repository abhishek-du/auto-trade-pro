"""Two questions:
 1. Had the stock ALREADY moved in the tagged direction before T_event?
 2. Is the (absent) reaction driven by a few extremes, or broadly flat?
"""
import asyncio, json, warnings, datetime as dt, statistics as st
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
R=[dict(r, t_event=dt.datetime.fromisoformat(r["t_event"])) for r in json.load(open(f"{SP}/react.json"))]

async def main():
    pairs={(r["symbol"], r["t_event"].date()) for r in R}
    bars=defaultdict(list); pl=sorted(pairs)
    async with AsyncSessionLocal() as s:
        for i in range(0,len(pl),150):
            ch=pl[i:i+150]
            conds=" OR ".join(f"(symbol=:s{j} AND timestamp>=:a{j} AND timestamp<:b{j})" for j in range(len(ch)))
            p={}
            for j,(sym,d) in enumerate(ch):
                p[f"s{j}"]=sym; p[f"a{j}"]=dt.datetime.combine(d,dt.time(0,0)); p[f"b{j}"]=dt.datetime.combine(d,dt.time(23,59))
            for sym,ts,c in (await s.execute(text(
                f"SELECT symbol,timestamp,close FROM candles WHERE timeframe='1m' AND ({conds}) ORDER BY symbol,timestamp"),p)).fetchall():
                bars[(sym,ts.date())].append((ts,float(c)))
    print("=== PRE-EVENT DRIFT: had the stock already moved the tagged way BEFORE we tagged it? ===")
    print(f"  {'window':<24}{'n':>6}{'median':>9}{'mean':>9}{'already moved >0':>19}")
    for W in (5,15,30,60):
        vals=[]
        for r in R:
            bl=bars.get((r["symbol"], r["t_event"].date())) or []
            t0=r["t_event"]; sgn=1.0 if r["side"]=="LONG" else -1.0
            pre=[b for b in bl if b[0]<=t0]
            if len(pre)<2: continue
            p_now=pre[-1][1]
            back=[b for b in pre if b[0]>=t0-dt.timedelta(minutes=W)]
            if len(back)<2: continue
            p_then=back[0][1]
            if p_then<=0: continue
            vals.append(sgn*(p_now-p_then)/p_then*100)
        if vals:
            print(f"  {f'-{W}m to T_event':<24}{len(vals):>6}{st.median(vals):>9.3f}{sum(vals)/len(vals):>9.3f}"
                  f"{len([x for x in vals if x>0])/len(vals)*100:>18.1f}%")
    # open -> T_event
    vals=[]
    for r in R:
        bl=bars.get((r["symbol"], r["t_event"].date())) or []
        t0=r["t_event"]; sgn=1.0 if r["side"]=="LONG" else -1.0
        pre=[b for b in bl if b[0]<=t0]
        if len(pre)<2: continue
        if pre[0][1]<=0: continue
        vals.append(sgn*(pre[-1][1]-pre[0][1])/pre[0][1]*100)
    print(f"  {'session open to T_event':<24}{len(vals):>6}{st.median(vals):>9.3f}{sum(vals)/len(vals):>9.3f}"
          f"{len([x for x in vals if x>0])/len(vals)*100:>18.1f}%")

    print("\n=== CONCENTRATION: is the EOD excess driven by a few extremes? ===")
    ex=sorted([r["rEOD"]-r["bEOD"] for r in R if r.get("rEOD") is not None and r.get("bEOD") is not None])
    n=len(ex); tot=sum(ex)
    print(f"  n={n}  total excess={tot:+.1f} pts  mean={tot/n:+.4f}")
    for k in (1,5,10,25):
        cut=max(1,int(n*k/100))
        top=sum(ex[-cut:]); bot=sum(ex[:cut])
        print(f"  top {k:>2}% ({cut:>4}) contribute {top:+9.1f}   bottom {k:>2}% contribute {bot:+9.1f}")
    trimmed=ex[int(n*0.05):int(n*0.95)]
    print(f"  mean after trimming the extreme 5% each side: {sum(trimmed)/len(trimmed):+.4f}  (n={len(trimmed)})")
    print(f"  share of observations with |excess| < 0.25% : {len([x for x in ex if abs(x)<0.25])/n*100:.1f}%")

    print("\n=== PER-SESSION consistency (EOD excess) ===")
    bysess=defaultdict(list)
    for r in R:
        if r.get("rEOD") is not None and r.get("bEOD") is not None:
            bysess[r["t_event"][:10] if isinstance(r["t_event"],str) else str(r["t_event"].date())].append(r["rEOD"]-r["bEOD"])
    pos=0
    for d in sorted(bysess):
        v=bysess[d]; m=sum(v)/len(v)
        if m>0: pos+=1
        print(f"   {d}  n={len(v):>4}  mean={m:+.3f}  median={st.median(v):+.3f}")
    print(f"\n   sessions with positive mean excess: {pos}/{len(bysess)}")
asyncio.run(main())
