"""Unbiased counterfactual: sweep the confirmation threshold over ALL liquid
NSE names with 1m data today — winners, losers and flat alike. No hindsight
selection."""
import asyncio,json,warnings,statistics as st; warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
K={r["symbol"]:r for r in json.load(open("/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad/kite_close.json"))}
LIQ={s:r for s,r in K.items() if r["turnover_cr"]>=5}
async def m():
    async with AsyncSessionLocal() as s:
        syms=[r[0] for r in (await s.execute(text("""SELECT DISTINCT symbol FROM candles
              WHERE timeframe='1m' AND timestamp::date=CURRENT_DATE AND symbol LIKE '%.NS'"""))).fetchall()]
    have={x.rsplit(".",1)[0]:x for x in syms}
    cand=[b for b in LIQ if b in have]
    print(f"universe: {len(cand)} NSE names >=Rs 5cr turnover with 1m candles today")
    print("NO selection on outcome — winners, losers and flat all included.\n")
    series={}
    async with AsyncSessionLocal() as s:
        for b in cand:
            bars=(await s.execute(text("""SELECT timestamp, high, low, close FROM candles
                WHERE symbol=:s AND timeframe='1m' AND timestamp::date=CURRENT_DATE
                ORDER BY timestamp"""),{"s":have[b]})).fetchall()
            if len(bars)>=60 and LIQ[b]["prev_close"]:
                series[b]=(bars, LIQ[b]["prev_close"])
    print(f"usable series: {len(series)}\n")
    print(f"{'RULE':<34}{'entries':>9}{'median ret%':>13}{'mean ret%':>11}{'win rate':>10}{'median MFE%':>13}")
    def run(th, side):
        rets=[]; mfes=[]
        for b,(bars,pc) in series.items():
            entry=None
            for t,h,l,c in bars:
                mv_hi=(float(h)-pc)/pc*100; mv_lo=(float(l)-pc)/pc*100
                if side=="long" and mv_hi>=th: entry=(t,pc*(1+th/100)); break
                if side=="short" and mv_lo<=-th: entry=(t,pc*(1-th/100)); break
            if entry is None: continue
            t0,p0=entry
            after=[x for x in bars if x[0]>=t0]
            if len(after)<5: continue
            close=float(after[-1][3])
            r=(close-p0)/p0*100 if side=="long" else (p0-close)/p0*100
            mfe=(max(float(x[1]) for x in after)-p0)/p0*100 if side=="long" \
                else (p0-min(float(x[2]) for x in after))/p0*100
            rets.append(r); mfes.append(mfe)
        if not rets: return None
        return len(rets), st.median(rets), sum(rets)/len(rets), len([x for x in rets if x>0])/len(rets)*100, st.median(mfes)
    for th in (0.0,0.5,1.0,1.5,2.0,3.0,5.0):
        r=run(th,"long")
        if r: print(f"  LONG after +{th:>4.1f}% (hold to close){r[0]:>7}{r[1]:>13.2f}{r[2]:>11.2f}{r[3]:>9.1f}%{r[4]:>13.2f}")
    print()
    for th in (1.0,1.5,2.0,3.0):
        r=run(th,"short")
        if r: print(f"  SHORT after -{th:>4.1f}% (hold to close){r[0]:>6}{r[1]:>13.2f}{r[2]:>11.2f}{r[3]:>9.1f}%{r[4]:>13.2f}")
    print(f"\n  (LIVE SETTING IS: MIN_DAY_CHANGE_PCT = 1.5)")
asyncio.run(m())
