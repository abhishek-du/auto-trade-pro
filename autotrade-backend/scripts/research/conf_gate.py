import asyncio,json,warnings,statistics as st,datetime as dt; warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
K={r["symbol"]:r for r in json.load(open("/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad/kite_close.json"))}
LIQ={s:r for s,r in K.items() if r["turnover_cr"]>=5}
THRESH=1.5
async def m():
    async with AsyncSessionLocal() as s:
        syms=[r[0] for r in (await s.execute(text("""SELECT DISTINCT symbol FROM candles
              WHERE timeframe='1m' AND timestamp::date=CURRENT_DATE AND symbol LIKE '%.NS'"""))).fetchall()]
    have={s2.rsplit(".",1)[0]:s2 for s2 in syms}
    cand=[b for b in LIQ if b in have and LIQ[b]["pct"]>0]
    print(f"universe for this test: {len(cand)} NSE names, >=Rs 5cr turnover, closed UP, with 1m candles\n")
    rows=[]
    async with AsyncSessionLocal() as s:
        for b in cand:
            bars=(await s.execute(text("""SELECT timestamp, high, low, close FROM candles
                WHERE symbol=:s AND timeframe='1m' AND timestamp::date=CURRENT_DATE
                ORDER BY timestamp"""),{"s":have[b]})).fetchall()
            if len(bars)<60: continue
            pc=LIQ[b]["prev_close"]
            if not pc: continue
            # first bar whose HIGH crosses +1.5% -> the earliest the gate could pass
            cross=None
            for t,h,l,c in bars:
                if (float(h)-pc)/pc*100 >= THRESH: cross=(t,float(h)); break
            dayhigh=max(float(x[1]) for x in bars)
            close=float(bars[-1][3])
            if cross is None:
                rows.append((b,None,None,None,LIQ[b]["pct"])); continue
            t0,p0=cross
            after=[x for x in bars if x[0]>=t0]
            rem_high=(max(float(x[1]) for x in after)-p0)/p0*100
            rem_close=(close-p0)/p0*100
            mins=(t0-bars[0][0]).total_seconds()/60
            rows.append((b,mins,rem_high,rem_close,LIQ[b]["pct"]))
    got=[r for r in rows if r[1] is not None]
    never=[r for r in rows if r[1] is None]
    print(f"crossed +{THRESH}% at some point : {len(got)}")
    print(f"never crossed +{THRESH}%          : {len(never)}  <- gate would block these ALL DAY")
    if got:
        mins=[r[1] for r in got]; rh=[r[2] for r in got]; rc=[r[3] for r in got]; day=[r[4] for r in got]
        print(f"\nWHEN the gate first opens (minutes after 09:15 IST):")
        print(f"   median {st.median(mins):.0f} min  (= {9+int((15+st.median(mins))//60)}:{int((15+st.median(mins))%60):02d} IST)   p25={sorted(mins)[len(mins)//4]:.0f}  p75={sorted(mins)[3*len(mins)//4]:.0f}")
        print(f"\nHOW MUCH MOVE IS LEFT once the gate opens:")
        print(f"   median remaining to day HIGH  : {st.median(rh):+.2f}%")
        print(f"   median remaining to day CLOSE : {st.median(rc):+.2f}%")
        print(f"   median FULL day move of these : {st.median(day):+.2f}%")
        print(f"   -> the gate hands you {st.median(rc)/st.median(day)*100:.0f}% of the move (to close)")
        neg=[r for r in got if r[3]<0]
        print(f"\n   names that were LOWER at close than at the moment the gate opened: {len(neg)}/{len(got)} = {len(neg)/len(got)*100:.0f}%")
        print(f"   i.e. buying on confirmation was a LOSING entry {len(neg)/len(got)*100:.0f}% of the time")
asyncio.run(m())
