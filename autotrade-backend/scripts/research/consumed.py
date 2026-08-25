"""How much of the move is already consumed when the signal fires, and does
that predict the forward outcome?

No news event exists on this path (engine/tactical_executor.py docstring:
"Path F originates trades from technical conditions with no news event"), so
there is no T_event_public. The only defensible reference points that use
information available at signal time are:
  · the session open
  · the trailing 15 / 30 / 60 minutes before the signal
Both are computed from bars strictly BEFORE the signal timestamp.
"""
import asyncio, json, warnings, datetime as dt, statistics as st
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
FWD=json.load(open(f"{SP}/fwd.json"))
RES={r["id"]: r for r in FWD if r["outcome"]!="UNRESOLVED_DATA"}

async def main():
    async with AsyncSessionLocal() as s:
        sigs=(await s.execute(text("""SELECT id, symbol, strategy, sub_pipeline, signal_type,
            entry_price, created_at FROM tactical_signals
            WHERE created_at::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24')
            ORDER BY created_at"""))).fetchall()
    syms={r[1] for r in sigs}
    bars=defaultdict(list); sl=sorted(syms)
    async with AsyncSessionLocal() as s:
        for i in range(0,len(sl),100):
            ch=sl[i:i+100]
            conds=" OR ".join(f"symbol=:s{j}" for j in range(len(ch)))
            p={f"s{j}":c for j,c in enumerate(ch)}
            for sym,ts,h,l,c in (await s.execute(text(
                f"SELECT symbol,timestamp,high,low,close FROM candles WHERE timeframe='1m' "
                f"AND timestamp::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24') AND ({conds}) ORDER BY symbol,timestamp"),p)).fetchall():
                bars[(sym,ts.date())].append((ts,float(h),float(l),float(c)))

    rows=[]
    for (sid,sym,strat,sub,side,ep,ts) in sigs:
        r=RES.get(str(sid))
        if r is None: continue
        d=ts.date(); day=bars.get((sym,d)) or []
        pre=[b for b in day if b[0]<=ts]
        if len(pre)<5: continue
        ep=float(ep); is_long=(side or "BUY").upper()=="BUY"
        sgn = 1.0 if is_long else -1.0
        o=pre[0][3]
        rec=dict(id=str(sid), strat=strat, sub=sub, side=side,
                 gross=r["gross"], mfe=r["mfe"], mae=r["mae"], outcome=r["outcome"])
        rec["from_open"]= sgn*(ep-o)/o*100 if o>0 else None
        for w in (15,30,60):
            back=[b for b in pre if b[0]>=ts-dt.timedelta(minutes=w)]
            rec[f"prior{w}"]= sgn*(ep-back[0][3])/back[0][3]*100 if len(back)>=2 and back[0][3]>0 else None
        # where in the day's range so far did we enter?
        hi=max(b[1] for b in pre); lo=min(b[2] for b in pre)
        rec["range_pos"]= ((ep-lo)/(hi-lo)*100 if hi>lo else 50.0) if is_long else ((hi-ep)/(hi-lo)*100 if hi>lo else 50.0)
        rec["mins_into_session"]=(ts-pre[0][0]).total_seconds()/60
        rows.append(rec)
    print(f"signals with pre-signal history: {len(rows)}")
    json.dump(rows, open(f"{SP}/consumed.json","w"))

    def q(v,p):
        v=sorted(v); return v[max(0,min(len(v)-1,int(round(p*(len(v)-1)))))]
    print("\n=== PRICE MOVE ALREADY CONSUMED AT THE SIGNAL (in the signal's own direction) ===")
    print(f"  {'measure':<22}{'n':>6}{'p25':>8}{'median':>9}{'p75':>8}{'p90':>8}{'p95':>8}")
    for key,lab in (("from_open","session open -> signal"),("prior60","last 60 min"),
                    ("prior30","last 30 min"),("prior15","last 15 min")):
        v=[r[key] for r in rows if r.get(key) is not None]
        print(f"  {lab:<22}{len(v):>6}{q(v,.25):>8.3f}{st.median(v):>9.3f}{q(v,.75):>8.3f}{q(v,.90):>8.3f}{q(v,.95):>8.3f}")
    for sd in ("BUY","SELL"):
        v=[r["from_open"] for r in rows if r["side"]==sd and r.get("from_open") is not None]
        if v: print(f"  {'  '+sd+' open->signal':<22}{len(v):>6}{q(v,.25):>8.3f}{st.median(v):>9.3f}{q(v,.75):>8.3f}{q(v,.90):>8.3f}{q(v,.95):>8.3f}")
    v=[r["range_pos"] for r in rows]
    print(f"\n  position in the day's range so far (100 = at the favourable extreme):")
    print(f"    p25 {q(v,.25):.0f}%   median {st.median(v):.0f}%   p75 {q(v,.75):.0f}%   p90 {q(v,.90):.0f}%")

    print("\n=== POINT OF NO RETURN: forward outcome bucketed by prior move (open -> signal) ===")
    print(f"  {'bucket':<16}{'n':>6}{'med fwd%':>11}{'mean fwd%':>11}{'win%':>8}{'medMFE':>9}{'medMAE':>9}{'hitSL%':>8}")
    bins=[(-99,0),(0,0.25),(0.25,0.5),(0.5,0.75),(0.75,1.0),(1.0,1.5),(1.5,2.0),(2.0,3.0),(3.0,99)]
    for lo_,hi_ in bins:
        sub=[r for r in rows if r.get("from_open") is not None and lo_<=r["from_open"]<hi_]
        if len(sub)<15: 
            if sub: print(f"  {f'{lo_} to {hi_}':<16}{len(sub):>6}   (n<15)")
            continue
        g=[r["gross"] for r in sub]
        print(f"  {f'{lo_} to {hi_}':<16}{len(sub):>6}{st.median(g):>11.3f}{sum(g)/len(g):>11.3f}"
              f"{len([x for x in g if x>0])/len(g)*100:>7.1f}%{st.median([r['mfe'] for r in sub]):>9.3f}"
              f"{st.median([r['mae'] for r in sub]):>9.3f}"
              f"{len([r for r in sub if r['outcome']=='SL'])/len(sub)*100:>7.1f}%")
asyncio.run(main())
