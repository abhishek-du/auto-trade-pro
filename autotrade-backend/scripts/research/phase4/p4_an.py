import json, sys, statistics as st, datetime as dt
from collections import defaultdict, Counter
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=json.load(open(f"{SP}/p4_2026-08-25.json"))
C=D["cycles"]; K=D["candidates"]
def ist(s): return (dt.datetime.fromisoformat(s)+dt.timedelta(hours=5,minutes=30))

print("="*104); print("PART 3 — HUB FUNNEL, 2026-08-25 (366 one-minute cycles, 09:15-15:20 IST)"); print("="*104)
tot=defaultdict(int)
for c in C:
    for k,v in c["terminal"].items(): tot[k]+=v
avg=lambda f: sum(f(c) for c in C)/len(C)
print(f"  {'stage':<34} {'per cycle (avg)':>16} {'session total':>16}")
print(f"  {'Hub rows in the 45-min window':<34} {avg(lambda c:c['hub_rows']):16.0f} {'—':>16}")
print(f"  {'  ...actionable label + unblocked':<34} {avg(lambda c:c['actionable_rows']):16.0f} {'—':>16}")
print(f"  {'    STRONG_BUY':<34} {avg(lambda c:c['sb']):16.0f} {'—':>16}")
print(f"  {'    BUY':<34} {avg(lambda c:c['b']):16.0f} {'—':>16}")
print(f"  {'    SELL':<34} {avg(lambda c:c['s']):16.0f} {'—':>16}")
print(f"  {'    STRONG_SELL':<34} {avg(lambda c:c['ss']):16.0f} {'—':>16}")
print(f"  {'  signals (passed all filters)':<34} {avg(lambda c:c['signals']):16.1f} {'—':>16}")
print(f"  {'  level_pool (after the cap)':<34} {avg(lambda c:c['level_pool']):16.1f} {sum(c['level_pool'] for c in C):16,}")
print()
print("PART 4 — TERMINAL-STATE HISTOGRAM (candidate-cycles)")
gt=sum(tot.values())
for k,v in sorted(tot.items(), key=lambda x:-x[1]):
    print(f"  {k:<30} {v:>9,}  {100*v/gt:5.1f}%")
print(f"  {'TOTAL':<30} {gt:>9,}")

print()
print("="*104); print("PART 5 — CANDIDATE-CAP ANALYSIS"); print("="*104)
before=sum(c["actionable"] for c in C); after=sum(c["level_pool"] for c in C)
print(f"  cap formula: actionable[: min(len, max(MAX_NEW*3,12), 24)]  with MAX_NEW=5  ->  15")
print(f"  candidates before cap : {before:>9,}")
print(f"  candidates after cap  : {after:>9,}")
print(f"  removed by cap        : {before-after:>9,}  ({100*(before-after)/before:.1f}%)")
print(f"  ordering              : actionable.sort(key=confidence, reverse=True)  (india_tasks.py:884)")
print(f"                          -> DETERMINISTIC, score-descending. Not timestamp or insertion order.")
cyc=[c for c in C if c["actionable"]>0]
print(f"  cycles where the cap bound : {sum(1 for c in cyc if c['actionable']>15)}/{len(cyc)} "
      f"({100*sum(1 for c in cyc if c['actionable']>15)/max(len(cyc),1):.0f}%)")
print(f"  median actionable per cycle: {st.median([c['actionable'] for c in cyc]):.0f}  (cap is 15)")

print()
print("="*104); print("PART 6 — SYMBOL CONCENTRATION (shadow candidates)"); print("="*104)
cnt=Counter(k["sym"] for k in K)
print(f"  candidate-cycles {len(K):,} across {len(cnt)} unique symbols")
top=cnt.most_common(10)
print(f"  top 10 symbols carry {100*sum(v for _,v in top)/len(K):.1f}% of candidate-cycles:")
for s_,v in top:
    sc=[k["score"] for k in K if k["sym"]==s_]
    print(f"     {s_:<18} {v:>5} cycles  ({100*v/len(K):4.1f}%)  score {st.mean(sc):+6.1f}")
print(f"  top 20 symbols carry {100*sum(v for _,v in cnt.most_common(20))/len(K):.1f}%")
mx=max(cnt.values())
print(f"  a symbol can appear at most {len(C)} times (once per cycle); max observed {mx} "
      f"({100*mx/len(C):.0f}% of cycles)")

print()
print("="*104); print("PART 7 — SCORE DISTRIBUTION AT EACH STAGE"); print("="*104)
def q(v,p): 
    v=sorted(v); return v[min(len(v)-1,int(p/100*len(v)))]
stages={}
allsc=[]; sb=[]; b=[]
for c in C: pass
# rebuild from the raw score table for stage 1-3
import asyncio
from sqlalchemy import text
from db.database import AsyncSessionLocal
async def raw():
    async with AsyncSessionLocal() as db:
        return (await db.execute(text("""
            SELECT master_score, signal FROM master_intelligence_scores
            WHERE scored_at::date=DATE '2026-08-25' AND master_score IS NOT NULL AND symbol LIKE '%.NS'"""))).fetchall()
rows=asyncio.run(raw())
stages["all Hub rows"]=[float(r[0]) for r in rows]
stages["STRONG_BUY"]=[float(r[0]) for r in rows if r[1]=="STRONG_BUY"]
stages["BUY"]=[float(r[0]) for r in rows if r[1]=="BUY"]
stages["shadow candidates"]=[k["score"] for k in K]
stages["shadow-eligible (=cands)"]=[k["score"] for k in K]
print(f"  {'stage':<26} {'n':>8} {'median':>8} {'p75':>8} {'p90':>8} {'p95':>8} {'p99':>8}")
for lab,v in stages.items():
    if not v: continue
    print(f"  {lab:<26} {len(v):>8,} {st.median(v):8.1f} {q(v,75):8.1f} {q(v,90):8.1f} {q(v,95):8.1f} {q(v,99):8.1f}")

print()
print("="*104); print("PART 8 — TIME-OF-DAY DISTRIBUTION (shadow candidates by IST hour band)"); print("="*104)
bands=[("09:15-10:00",9*60+15,10*60),("10:00-11:00",600,660),("11:00-12:00",660,720),
       ("12:00-13:00",720,780),("13:00-14:00",780,840),("14:00-15:00",840,900),("15:00-15:20",900,920)]
for lab,lo,hi in bands:
    n=sum(1 for k in K if lo <= ist(k["t"]).hour*60+ist(k["t"]).minute < hi)
    cy=sum(1 for c in C if lo <= ist(c["t"]).hour*60+ist(c["t"]).minute < hi)
    print(f"  {lab:<14} cycles {cy:>4}   shadow candidates {n:>6}   per cycle {n/max(cy,1):5.1f}")
