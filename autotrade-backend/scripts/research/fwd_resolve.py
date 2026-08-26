"""Forward-resolution experiment.

Holding window: from the signal timestamp to the close of the NEXT trading
session. Deterministic, identical for every signal, chosen before any result was
seen. This removes the defect that invalidated the previous per-strategy
numbers, where 59.7% of signals never resolved and were marked to an arbitrary
last bar.

Every signal ends as exactly one of:
    SL              stop touched first (stop wins ties)
    TP              target touched first
    TIME_EXIT       neither touched; marked at the next session's close
    UNRESOLVED_DATA the candles required to decide do not exist

Nulls hold trade GEOMETRY constant and destroy only the claimed information.
All three are evaluated over the SAME forward window as their paired real
signal, so the comparison is marking-invariant.
"""
import asyncio, json, random, warnings, datetime as dt
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
HORIZONS=[1,3,5,10,15,30,60]

async def main():
    async with AsyncSessionLocal() as s:
        sigs=(await s.execute(text("""
            SELECT id, symbol, strategy, sub_pipeline, signal_type,
                   entry_price, stop_loss, target, created_at
            FROM tactical_signals
            WHERE created_at > CURRENT_DATE - 30
              AND entry_price IS NOT NULL AND stop_loss IS NOT NULL AND target IS NOT NULL
            ORDER BY created_at"""))).fetchall()
        sessions=[r[0] for r in (await s.execute(text("""
            SELECT DISTINCT timestamp::date FROM candles
            WHERE timeframe='1m' AND timestamp > CURRENT_DATE - 40 ORDER BY 1"""))).fetchall()]
        pool=[r[0] for r in (await s.execute(text("""
            SELECT symbol FROM candles WHERE timeframe='1m' AND timestamp > CURRENT_DATE - 30
            GROUP BY symbol HAVING COUNT(*) > 3000 AND SUM(volume*close) > 500000000"""))).fetchall()]
    nxt={sessions[i]: sessions[i+1] for i in range(len(sessions)-1)}
    print(f"signals {len(sigs)} · sessions {len(sessions)} · null pool {len(pool)}")

    need=set()
    for r in sigs:
        d=r[8].date(); need.add((r[1],d))
        if d in nxt: need.add((r[1],nxt[d]))
    for p in pool:
        for d in sessions[-30:]:
            need.add((p,d))
    bars=defaultdict(list); pl=sorted(need, key=lambda x:(x[0],x[1]))
    syms=sorted({x[0] for x in pl})
    async with AsyncSessionLocal() as s:
        for i in range(0,len(syms),100):
            ch=syms[i:i+100]
            conds=" OR ".join(f"symbol=:s{j}" for j in range(len(ch)))
            p={f"s{j}":c for j,c in enumerate(ch)}
            for sym,ts,h,l,c in (await s.execute(text(
                f"SELECT symbol,timestamp,high,low,close FROM candles WHERE timeframe='1m' "
                f"AND timestamp > CURRENT_DATE - 40 AND ({conds}) ORDER BY symbol,timestamp"),p)).fetchall():
                bars[(sym,ts.date())].append((ts,float(h),float(l),float(c)))
    print(f"loaded bar-days: {len(bars)}")

    def window(sym, d, after_ts):
        """1m bars from after_ts to the close of the NEXT session."""
        a=[b for b in (bars.get((sym,d)) or []) if b[0]>after_ts]
        nd=nxt.get(d)
        b2=(bars.get((sym,nd)) or []) if nd else []
        return a+b2, (nd is not None and len(b2)>0)

    def resolve(bl, ep, is_long, stop, tgt):
        """-> (outcome, gross%, mfe%, mae%, t_mfe_min, t_mae_min)"""
        mfe=0.0; mae=0.0; tm=0.0; ta=0.0; t0=bl[0][0]
        for (bt,hi,lo,cl) in bl:
            f=((hi-ep) if is_long else (ep-lo))/ep*100
            a=((lo-ep) if is_long else (ep-hi))/ep*100
            if f>mfe: mfe=f; tm=(bt-t0).total_seconds()/60
            if a<mae: mae=a; ta=(bt-t0).total_seconds()/60
            hs=(lo<=stop) if is_long else (hi>=stop)
            ht=(hi>=tgt)  if is_long else (lo<=tgt)
            if hs: return "SL", ((stop-ep) if is_long else (ep-stop))/ep*100, mfe,mae,tm,ta
            if ht: return "TP", ((tgt-ep)  if is_long else (ep-tgt))/ep*100, mfe,mae,tm,ta
        c=bl[-1][3]
        return "TIME_EXIT", ((c-ep) if is_long else (ep-c))/ep*100, mfe,mae,tm,ta

    rows=[]; unresolved=0
    for (sid,sym,strat,sub,side,ep,sl,tp,ts) in sigs:
        d=ts.date()
        bl,has_next = window(sym,d,ts)
        if len(bl)<5 or not has_next:
            unresolved+=1
            rows.append(dict(id=str(sid),sym=sym,strat=strat,sub=sub,outcome="UNRESOLVED_DATA"))
            continue
        ep=float(ep); sl=float(sl); tp=float(tp)
        is_long=(side or "BUY").upper()=="BUY"
        risk=abs(ep-sl)
        if risk<=0:
            unresolved+=1
            rows.append(dict(id=str(sid),sym=sym,strat=strat,sub=sub,outcome="UNRESOLVED_DATA"))
            continue
        sp=risk/ep*100; tpp=abs(tp-ep)/ep*100
        oc,g,mfe,mae,tm,ta = resolve(bl,ep,is_long,sl,tp)
        r=dict(id=str(sid),sym=sym,strat=strat,sub=sub,side=side,outcome=oc,gross=g,
               mfe=mfe,mae=mae,t_mfe=tm,t_mae=ta,rr=tpp/sp,risk_pct=sp,bars=len(bl))
        for h in HORIZONS:
            seg=[b for b in bl if (b[0]-ts).total_seconds()<=h*60]
            r[f"f{h}"]= (((seg[-1][3]-ep) if is_long else (ep-seg[-1][3]))/ep*100) if seg else None

        # ── NULL 1: random symbol, same clock, same geometry ─────────────────
        r["n_sym"]=None
        for _ in range(6):
            alt=random.choice(pool)
            ab,ok = window(alt,d,ts)
            if ok and len(ab)>=5:
                aep=ab[0][3]
                astop = aep*(1-sp/100) if is_long else aep*(1+sp/100)
                atgt  = aep*(1+tpp/100) if is_long else aep*(1-tpp/100)
                r["n_sym"]=resolve(ab,aep,is_long,astop,atgt)[1]; break
        # ── NULL 2: random direction, everything else identical ──────────────
        flip = random.random()<0.5
        dl = (not is_long) if flip else is_long
        fstop = ep*(1-sp/100) if dl else ep*(1+sp/100)
        ftgt  = ep*(1+tpp/100) if dl else ep*(1-tpp/100)
        r["n_dir"]=resolve(bl,ep,dl,fstop,ftgt)[1]
        r["n_dir_flipped"]=flip
        # ── NULL 3: random timestamp in the SAME symbol+session ──────────────
        r["n_time"]=None; r["n_time_f"]={}
        same=bars.get((sym,d)) or []
        if len(same)>30:
            k=random.randrange(0,len(same)-5)
            rts=same[k][0]
            rb,ok = window(sym,d,rts)
            if ok and len(rb)>=5:
                rep=rb[0][3]
                rstop = rep*(1-sp/100) if is_long else rep*(1+sp/100)
                rtgt  = rep*(1+tpp/100) if is_long else rep*(1-tpp/100)
                r["n_time"]=resolve(rb,rep,is_long,rstop,rtgt)[1]
                for h in HORIZONS:
                    seg=[b for b in rb if (b[0]-rts).total_seconds()<=h*60]
                    r["n_time_f"][str(h)]= (((seg[-1][3]-rep) if is_long else (rep-seg[-1][3]))/rep*100) if seg else None
        rows.append(r)
    print(f"rows: {len(rows)}   UNRESOLVED_DATA: {unresolved}")
    json.dump(rows, open(f"{SP}/fwd.json","w"))
asyncio.run(main())
