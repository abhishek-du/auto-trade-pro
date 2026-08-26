"""MATCHED-CONTROL SYMBOL SELECTION TEST.

For each real signal at timestamp T on symbol S, find other symbols that looked
like S *at T* and compare what happened next, in the signal's own direction.

Every matching variable is computed from bars with timestamp <= T only. No
future price, volume, volatility or outcome is read when choosing a control.
That is enforced structurally: the state series is built once per symbol and
each signal indexes it at T.

Match dimensions (each also tested alone):
  MOM  trailing 15m return
  VOL  trailing 15m realised volatility (stdev of 1m returns)
  LIQ  session-to-date traded value
  CMP  composite: all three plus price decade
"""
import asyncio, json, math, random, warnings, datetime as dt, statistics as st, bisect
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
HOR=[5,15,30,60,120]

async def main():
    async with AsyncSessionLocal() as s:
        sigs=(await s.execute(text("""SELECT id, symbol, strategy, signal_type, entry_price,
            created_at FROM tactical_signals
            WHERE created_at::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24')
            AND entry_price IS NOT NULL ORDER BY created_at"""))).fetchall()
        pool=[r[0] for r in (await s.execute(text("""
            SELECT symbol FROM candles WHERE timeframe='1m'
            AND timestamp::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24')
            GROUP BY symbol HAVING COUNT(*) > 900 AND SUM(volume*close) > 200000000"""))).fetchall()]
    print(f"signals {len(sigs)} · candidate pool {len(pool)}")
    need=sorted(set(pool) | {r[1] for r in sigs})
    bars=defaultdict(list)
    async with AsyncSessionLocal() as s:
        for i in range(0,len(need),100):
            ch=need[i:i+100]
            conds=" OR ".join(f"symbol=:s{j}" for j in range(len(ch)))
            p={f"s{j}":c for j,c in enumerate(ch)}
            for sym,ts,c,v in (await s.execute(text(
                f"SELECT symbol,timestamp,close,volume FROM candles WHERE timeframe='1m' "
                f"AND timestamp::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24') "
                f"AND ({conds}) ORDER BY symbol,timestamp"),p)).fetchall():
                bars[(sym,ts.date())].append((ts,float(c),float(v or 0)))
    print(f"bar-days loaded: {len(bars)}")

    # ── per symbol-day, a state series keyed by minute index ────────────────
    state={}   # (sym,date) -> list of dicts aligned with bars
    for k,bl in bars.items():
        cl=[b[1] for b in bl]; vol=[b[2] for b in bl]
        rets=[0.0]+[(cl[i]-cl[i-1])/cl[i-1] for i in range(1,len(cl)) if cl[i-1]>0]
        if len(rets)!=len(cl): rets=[0.0]*len(cl)
        cumtv=0.0; ser=[]
        for i in range(len(bl)):
            cumtv+=cl[i]*vol[i]
            m15=(cl[i]-cl[i-15])/cl[i-15]*100 if i>=15 and cl[i-15]>0 else None
            w=rets[max(0,i-14):i+1]
            v15=st.pstdev(w)*100 if len(w)>=5 else None
            ser.append(dict(px=cl[i], mom=m15, vol=v15, liq=cumtv))
        state[k]=ser
    times={k:[b[0] for b in bl] for k,bl in bars.items()}
    def at(k, ts):
        """Index of the last bar with timestamp <= ts. None if the symbol has
        no bar yet at ts. Never looks forward."""
        t=times.get(k)
        if not t: return None
        j=bisect.bisect_right(t, ts)-1
        if j < 0: return None
        if (ts - t[j]).total_seconds() > 300: return None   # stale, not tradable
        return j

    def fwd(k, i, H, is_long):
        bl=bars.get(k)
        if not bl or i is None or i+1>=len(bl): return None
        ep=bl[i][1]
        j=min(i+H, len(bl)-1)
        if j<=i or ep<=0: return None
        c=bl[j][1]
        return ((c-ep) if is_long else (ep-c))/ep*100

    ARMS=("MOM","VOL","LIQ","CMP","CMP2","CMP3")
    out=[]
    bydate=defaultdict(list)
    for p in pool:
        for d in (dt.date(2026,8,20),dt.date(2026,8,21),dt.date(2026,8,24)):
            if (p,d) in state: bydate[d].append(p)

    for (sid,sym,strat,side,ep,ts) in sigs:
        d=ts.date(); k=(sym,d)
        i=at(k, ts)
        if i is None: continue
        ser=state.get(k)
        if not ser or i>=len(ser): continue
        me=ser[i]
        if me["mom"] is None or me["vol"] is None: continue
        is_long=(side or "BUY").upper()=="BUY"
        rec=dict(id=str(sid), sym=sym, strat=strat, side=side, sess=str(d),
                 tod=ts.strftime("%H:%M"), bars_left=len(bars[k])-1-i,
                 mom=me["mom"], vol=me["vol"], liq=me["liq"], px=me["px"])
        for H in HOR: rec[f"r{H}"]=fwd(k,i,H,is_long)
        rec["rEOD"]=fwd(k,i,10**6,is_long)
        # candidate states at the same minute
        cands=[]
        for c in bydate[d]:
            if c==sym: continue
            ck=(c,d); ci=at(ck, ts)
            if ci is None: continue
            cs=state[ck][ci]
            if cs["mom"] is None or cs["vol"] is None or cs["liq"]<=0: continue
            cands.append((c,ci,cs))
        if len(cands)<20: continue
        def pick(pred, n=5):
            g=[x for x in cands if pred(x[2])]
            if len(g)<3: return None
            random.shuffle(g); return g[:n]
        sel={}
        sel["MOM"]=pick(lambda s_: abs(s_["mom"]-me["mom"])<=0.25)
        sel["VOL"]=pick(lambda s_: abs(s_["vol"]-me["vol"])<=0.25*max(me["vol"],0.02))
        sel["LIQ"]=pick(lambda s_: 0.5<=s_["liq"]/me["liq"]<=2.0)
        sel["CMP"]=pick(lambda s_: abs(s_["mom"]-me["mom"])<=0.35
                        and abs(s_["vol"]-me["vol"])<=0.35*max(me["vol"],0.02)
                        and 0.33<=s_["liq"]/me["liq"]<=3.0)
        sel["CMP2"]=pick(lambda s_: abs(s_["mom"]-me["mom"])<=0.15
                        and abs(s_["vol"]-me["vol"])<=0.20*max(me["vol"],0.02)
                        and 0.5<=s_["liq"]/me["liq"]<=2.0)
        sel["CMP3"]=pick(lambda s_: abs(s_["mom"]-me["mom"])<=0.08
                        and abs(s_["vol"]-me["vol"])<=0.12*max(me["vol"],0.02)
                        and 0.67<=s_["liq"]/me["liq"]<=1.5
                        and 0.5<=s_["px"]/me["px"]<=2.0)
        for a in ARMS:
            g=sel[a]
            rec[f"n_{a}"]= (len(g) if g else 0)
            if not g:
                for H in HOR: rec[f"{a}{H}"]=None
                rec[f"{a}EOD"]=None
                rec[f"{a}_mom"]=None; rec[f"{a}_vol"]=None; rec[f"{a}_liq"]=None
                continue
            rec[f"{a}_mom"]=st.mean([x[2]["mom"] for x in g])
            rec[f"{a}_vol"]=st.mean([x[2]["vol"] for x in g])
            rec[f"{a}_liq"]=st.mean([x[2]["liq"] for x in g])
            for H in list(HOR)+[10**6]:
                key=f"{a}EOD" if H==10**6 else f"{a}{H}"
                vals=[fwd((x[0],d),x[1],H,is_long) for x in g]
                vals=[v for v in vals if v is not None]
                rec[key]= (sum(vals)/len(vals)) if vals else None
        out.append(rec)
    print(f"signals with a usable control pool: {len(out)}")
    json.dump(out, open(f"{SP}/matched.json","w"))
asyncio.run(main())
