"""COUNTERFACTUAL STOP-ANCHOR EXPERIMENT.

Only the stop changes. Entry timestamp, entry price, direction, TARGET, rule,
signal selection and holding window are all held identical to the baseline.

Window: signal timestamp -> close of the NEXT trading session.
Tie rule: STOP WINS TIES.
ATR: Wilder-style true range over the 14 CLOSED 1m bars strictly BEFORE the
signal timestamp. No future bar is read anywhere.
"""
import asyncio, json, warnings, datetime as dt, statistics as st
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
SESSIONS=[dt.date(2026,8,20),dt.date(2026,8,21),dt.date(2026,8,24)]
PCTS=[0.5,0.75,1.0,1.25,1.5,2.0,3.0]
ATRS=[0.5,1.0,1.5,2.0]

async def main():
    async with AsyncSessionLocal() as s:
        sigs=(await s.execute(text("""SELECT id, symbol, strategy, signal_type, entry_price,
            stop_loss, target, created_at FROM tactical_signals
            WHERE created_at::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24')
            AND entry_price IS NOT NULL ORDER BY created_at"""))).fetchall()
        allsess=[r[0] for r in (await s.execute(text("""SELECT DISTINCT timestamp::date FROM candles
            WHERE timeframe='1m' AND timestamp > CURRENT_DATE - 12 ORDER BY 1"""))).fetchall()]
    nxt={allsess[i]: allsess[i+1] for i in range(len(allsess)-1)}
    syms={r[1] for r in sigs}
    bars=defaultdict(list); sl_=sorted(syms)
    async with AsyncSessionLocal() as s:
        for i in range(0,len(sl_),100):
            ch=sl_[i:i+100]
            conds=" OR ".join(f"symbol=:s{j}" for j in range(len(ch)))
            p={f"s{j}":c for j,c in enumerate(ch)}
            for sym,ts,h,l,c in (await s.execute(text(
                f"SELECT symbol,timestamp,high,low,close FROM candles WHERE timeframe='1m' "
                f"AND timestamp::date IN (DATE '2026-08-20',DATE '2026-08-21',DATE '2026-08-24',DATE '2026-08-25') "
                f"AND ({conds}) ORDER BY symbol,timestamp"),p)).fetchall():
                bars[(sym,ts.date())].append((ts,float(h),float(l),float(c)))

    def atr14(pre):
        """Wilder TR mean over the last 14 CLOSED bars before the signal."""
        if len(pre)<15: return None
        trs=[]
        for i in range(len(pre)-14, len(pre)):
            h,l = pre[i][1], pre[i][2]; pc = pre[i-1][3]
            trs.append(max(h-l, abs(h-pc), abs(l-pc)))
        return sum(trs)/len(trs) if trs else None

    def walk(bl, ep, is_long, stop, tgt):
        mfe=0.0; mae=0.0
        for (bt,hi,lo,cl) in bl:
            f=((hi-ep) if is_long else (ep-lo))/ep*100
            a=((lo-ep) if is_long else (ep-hi))/ep*100
            mfe=max(mfe,f); mae=min(mae,a)
            hs=(lo<=stop) if is_long else (hi>=stop)
            ht=(hi>=tgt)  if is_long else (lo<=tgt)
            if hs: return "SL", ((stop-ep) if is_long else (ep-stop))/ep*100, mfe, mae
            if ht: return "TP", ((tgt-ep)  if is_long else (ep-tgt))/ep*100, mfe, mae
        c=bl[-1][3]
        return "TIME_EXIT", ((c-ep) if is_long else (ep-c))/ep*100, mfe, mae

    rows=[]
    for (sid,sym,strat,side,ep,sl,tp,ts) in sigs:
        d=ts.date()
        if d not in nxt: continue
        day=bars.get((sym,d)) or []
        pre=[b for b in day if b[0]<=ts]
        post=[b for b in day if b[0]>ts]
        nb=bars.get((sym,nxt[d])) or []
        bl=post+nb
        if len(pre)<15 or len(bl)<5 or not nb: continue
        ep=float(ep); sl=float(sl); tp=float(tp)
        is_long=(side or "BUY").upper()=="BUY"
        if abs(ep-sl)<=0: continue
        o=pre[0][3]
        prior = (1.0 if is_long else -1.0)*(ep-o)/o*100 if o>0 else None
        a=atr14(pre)
        r=dict(id=str(sid), sym=sym, strat=strat, side=side, ep=ep, tp=tp,
               prior=prior, atr_pct=(a/ep*100 if a else None),
               tgt_pct=abs(tp-ep)/ep*100)
        # control
        oc,g,mfe,mae = walk(bl,ep,is_long,sl,tp)
        r["ORIG"]=dict(oc=oc,g=g,rr=abs(tp-ep)/abs(ep-sl),stop_pct=abs(ep-sl)/ep*100)
        r["mfe"]=mfe; r["mae"]=mae
        for x in PCTS:
            cs = ep*(1-x/100) if is_long else ep*(1+x/100)
            oc2,g2,_,_ = walk(bl,ep,is_long,cs,tp)
            r[f"PCT{x}"]=dict(oc=oc2,g=g2,rr=abs(tp-ep)/abs(ep-cs),stop_pct=x)
        if a:
            for k in ATRS:
                dist=k*a
                cs = ep-dist if is_long else ep+dist
                if (is_long and cs<=0): continue
                oc2,g2,_,_ = walk(bl,ep,is_long,cs,tp)
                r[f"ATR{k}"]=dict(oc=oc2,g=g2,rr=abs(tp-ep)/dist,stop_pct=dist/ep*100)
        rows.append(r)
    print(f"signals in experiment: {len(rows)}   (ATR available on {len([r for r in rows if r.get('ATR1.0')])})")
    json.dump(rows, open(f"{SP}/stop_cf.json","w"))
asyncio.run(main())
