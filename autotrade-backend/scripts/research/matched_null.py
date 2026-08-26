"""WINDOW-MATCHED timestamp null.

The previous null drew a uniform random minute and ran to the next session's
close. Real signals fire at a median of 286 minutes into the session against a
uniform expectation of 187, so the null received ~99 minutes more forward window
— a mechanical advantage unrelated to information. That confound invalidates the
-0.297% result as stated.

Fix: both arms get an IDENTICAL fixed horizon, and the random timestamp is drawn
only from minutes that leave the same horizon available inside the same session.
No overnight gap is crossed by either arm.
"""
import asyncio, json, random, warnings, datetime as dt, statistics as st
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"

async def main():
    async with AsyncSessionLocal() as s:
        sigs=(await s.execute(text("""SELECT id, symbol, signal_type, entry_price, stop_loss,
            target, created_at FROM tactical_signals
            WHERE created_at::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24')
            AND entry_price IS NOT NULL ORDER BY created_at"""))).fetchall()
    syms={r[1] for r in sigs}
    bars=defaultdict(list); sl=sorted(syms)
    async with AsyncSessionLocal() as s:
        for i in range(0,len(sl),100):
            ch=sl[i:i+100]
            conds=" OR ".join(f"symbol=:s{j}" for j in range(len(ch)))
            p={f"s{j}":c for j,c in enumerate(ch)}
            for sym,ts,h,l,c in (await s.execute(text(
                f"SELECT symbol,timestamp,high,low,close FROM candles WHERE timeframe='1m' "
                f"AND timestamp::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24') "
                f"AND ({conds}) ORDER BY symbol,timestamp"),p)).fetchall():
                bars[(sym,ts.date())].append((ts,float(h),float(l),float(c)))

    def run(bl, ep, is_long, sp, tpp, horizon):
        """Fixed-horizon walk. Stop wins ties. Returns gross % at horizon end."""
        stop = ep*(1-sp/100) if is_long else ep*(1+sp/100)
        tgt  = ep*(1+tpp/100) if is_long else ep*(1-tpp/100)
        t0=bl[0][0]
        for (bt,hi,lo,cl) in bl:
            if (bt-t0).total_seconds()/60 > horizon: break
            if (lo<=stop) if is_long else (hi>=stop): return -sp
            if (hi>=tgt)  if is_long else (lo<=tgt):  return  tpp
        seg=[b for b in bl if (b[0]-t0).total_seconds()/60<=horizon]
        c=seg[-1][3] if seg else bl[-1][3]
        return ((c-ep) if is_long else (ep-c))/ep*100

    def paired(d,n=4000):
        if len(d)<30: return None
        m=sum(d)/len(d); ms=[]
        for _ in range(n):
            s_=[d[random.randrange(len(d))] for _ in range(len(d))]; ms.append(sum(s_)/len(s_))
        ms.sort(); return len(d), m, ms[int(n*.025)], ms[int(n*.975)]

    print(f"  {'horizon':<10}{'pairs':>7}{'real':>10}{'null':>10}{'diff':>9}   95% paired CI")
    for H in (5,15,30,60,120):
        d=[]; rr=[]; nn=[]
        for (sid,sym,side,ep,slp,tp,ts) in sigs:
            day=bars.get((sym,ts.date())) or []
            if len(day)<60: continue
            ep=float(ep); slp=float(slp); tp=float(tp)
            risk=abs(ep-slp)
            if risk<=0: continue
            is_long=(side or "BUY").upper()=="BUY"
            sp=risk/ep*100; tpp=abs(tp-ep)/ep*100
            real=[b for b in day if b[0]>ts]
            # real must have the full horizon inside this session
            if not real or (real[-1][0]-ts).total_seconds()/60 < H: continue
            # null: random start that ALSO leaves the full horizon in this session
            last=day[-1][0]
            elig=[i for i,b in enumerate(day) if (last-b[0]).total_seconds()/60 >= H]
            if len(elig)<5: continue
            k=random.choice(elig[:-1]); nb=day[k+1:]
            if not nb: continue
            r_=run(real,ep,is_long,sp,tpp,H)
            nep=nb[0][3]
            n_=run(nb,nep,is_long,sp,tpp,H)
            rr.append(r_); nn.append(n_); d.append(r_-n_)
        out=paired(d)
        if out is None: print(f"  +{H}m{'':<6} insufficient pairs"); continue
        n,m,lo,hi=out
        v = "signal ADDS" if lo>0 else ("signal SUBTRACTS" if hi<0 else "INDISTINGUISHABLE")
        print(f"  +{H}m{'':<6}{n:>7}{sum(rr)/len(rr):>10.3f}{sum(nn)/len(nn):>10.3f}{m:>+9.3f}   [{lo:+.3f}, {hi:+.3f}]  {v}")
asyncio.run(main())
