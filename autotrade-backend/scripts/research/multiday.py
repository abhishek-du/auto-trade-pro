"""Multi-session counterfactual on the live confirmation gate.

Reconstructs prev_close from each symbol's own prior session's last 1m bar, so
no external data is needed and there is no hindsight in the entry rule: the
entry triggers on the FIRST 1m bar whose extreme crosses the threshold, and the
exit is that session's last 1m close. Liquidity is judged from the session's own
traded value so it is knowable at the time, not from a later snapshot.
"""
import asyncio,warnings,statistics as st; warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
from collections import defaultdict

async def m():
    async with AsyncSessionLocal() as s:
        days=[r[0] for r in (await s.execute(text("""SELECT DISTINCT timestamp::date FROM candles
              WHERE timeframe='1m' AND timestamp > CURRENT_DATE - 20 ORDER BY 1"""))).fetchall()]
    print(f"sessions: {days[0]} .. {days[-1]}  ({len(days)})\n")
    per_day = defaultdict(lambda: defaultdict(list))   # rule -> day -> returns
    for i, d in enumerate(days):
        if i == 0: continue
        prev = days[i-1]
        async with AsyncSessionLocal() as s:
            # session bars + prior session close + this session's traded value
            rows=(await s.execute(text("""
                WITH pc AS (
                  SELECT DISTINCT ON (symbol) symbol, close AS prev_close
                  FROM candles WHERE timeframe='1m' AND timestamp::date=:prev
                  ORDER BY symbol, timestamp DESC
                ), liq AS (
                  SELECT symbol, SUM(volume*close) AS tv, COUNT(*) AS nbars
                  FROM candles WHERE timeframe='1m' AND timestamp::date=:d
                  GROUP BY symbol HAVING SUM(volume*close) >= 50000000 AND COUNT(*) >= 60
                )
                SELECT c.symbol, c.timestamp, c.high, c.low, c.close, pc.prev_close
                FROM candles c JOIN pc USING (symbol) JOIN liq USING (symbol)
                WHERE c.timeframe='1m' AND c.timestamp::date=:d
                ORDER BY c.symbol, c.timestamp
            """), {"d": d, "prev": prev})).fetchall()
        ser=defaultdict(list); pcs={}
        for sym,t,h,l,c,p in rows:
            ser[sym].append((t,float(h),float(l),float(c))); pcs[sym]=float(p)
        for th in (0.0,1.0,1.5,3.0):
            for side in ("long","short"):
                if side=="short" and th==0.0: continue
                key=f"{side} {th}"
                for sym,bars in ser.items():
                    pc=pcs[sym]
                    if pc<=0: continue
                    ent=None
                    for t,h,l,c in bars:
                        if side=="long"  and (h-pc)/pc*100 >=  th: ent=pc*(1+th/100); break
                        if side=="short" and (l-pc)/pc*100 <= -th: ent=pc*(1-th/100); break
                    if ent is None: continue
                    idx=[j for j,(t,h,l,c) in enumerate(bars)]
                    close=bars[-1][3]
                    r=(close-ent)/ent*100 if side=="long" else (ent-close)/ent*100
                    per_day[key][d].append(r)
        print(f"  {d} ({d.strftime('%a')}): {len(ser)} liquid symbols")
    print()
    print(f"{'RULE':<16}{'sessions':>9}{'trades':>9}{'median%':>10}{'mean%':>9}{'win%':>8}{'days +ve':>10}")
    for key in sorted(per_day, key=lambda k:(k.split()[0], float(k.split()[1]))):
        dd=per_day[key]
        allr=[x for v in dd.values() for x in v]
        if not allr: continue
        dmeans=[sum(v)/len(v) for v in dd.values() if v]
        pos=len([x for x in dmeans if x>0])
        print(f"  {key:<14}{len(dd):>9}{len(allr):>9}{st.median(allr):>10.3f}{sum(allr)/len(allr):>9.3f}"
              f"{len([x for x in allr if x>0])/len(allr)*100:>7.1f}%{pos:>6}/{len(dmeans)}")
    print("\n  LIVE SETTING = long 1.5 (engine/entry_confirmation.py MIN_DAY_CHANGE_PCT)")
asyncio.run(m())
