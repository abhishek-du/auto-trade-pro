import json, random, statistics as st
from collections import Counter, defaultdict
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
R=json.load(open(f"{SP}/stop_cf.json"))
GEOS=["ORIG"]+[f"PCT{x}" for x in (0.5,0.75,1.0,1.25,1.5,2.0,3.0)]+[f"ATR{k}" for k in (0.5,1.0,1.5,2.0)]

def stats(rows, key):
    v=[r[key] for r in rows if r.get(key)]
    if len(v)<15: return None
    g=[x["g"] for x in v]; n=len(g)
    wins=[x for x in g if x>0]; los=[x for x in g if x<=0]
    pf=(sum(wins)/abs(sum(los))) if los and sum(los)!=0 else float("inf")
    c=Counter(x["oc"] for x in v)
    return dict(n=n, med=st.median(g), mean=sum(g)/n,
                win=len(wins)/n*100, pf=pf,
                sl=c["SL"]/n*100, tp=c["TP"]/n*100, te=c["TIME_EXIT"]/n*100,
                rr=st.median([x["rr"] for x in v]),
                stop=st.median([x["stop_pct"] for x in v]),
                awin=(sum(wins)/len(wins) if wins else 0),
                alos=(sum(los)/len(los) if los else 0))

def paired(rows, key, base="ORIG", n=4000):
    d=[r[key]["g"]-r[base]["g"] for r in rows if r.get(key) and r.get(base)]
    if len(d)<30: return None
    m=sum(d)/len(d); ms=[]
    for _ in range(n):
        s=[d[random.randrange(len(d))] for _ in range(len(d))]; ms.append(sum(s)/len(s))
    ms.sort(); return len(d), m, ms[int(n*.025)], ms[int(n*.975)]

H=f"  {'geometry':<10}{'n':>5}{'stop%':>8}{'R:R':>7}{'med%':>8}{'mean%':>8}{'win%':>7}{'PF':>7}{'SL%':>7}{'TP%':>7}{'TIME%':>7}{'avgW':>7}{'avgL':>7}"
print("="*118)
print("PART 3/4 — OVERALL FORWARD RESULTS BY STOP GEOMETRY (target unchanged, no costs)")
print("="*118); print(H)
for gkey in GEOS:
    s=stats(R,gkey)
    if not s: print(f"  {gkey:<10}  INSUFFICIENT"); continue
    tag=" <- control" if gkey=="ORIG" else ""
    print(f"  {gkey:<10}{s['n']:>5}{s['stop']:>8.2f}{s['rr']:>7.2f}{s['med']:>8.3f}{s['mean']:>8.3f}"
          f"{s['win']:>6.1f}%{s['pf']:>7.2f}{s['sl']:>6.1f}%{s['tp']:>6.1f}%{s['te']:>6.1f}%"
          f"{s['awin']:>7.2f}{s['alos']:>7.2f}{tag}")

print()
print("="*118)
print("PART 7/12 — PAIRED DIFFERENCE vs ORIGINAL STOP (same signal, only the stop differs)")
print("="*118)
print(f"  {'geometry':<10}{'pairs':>7}{'diff mean%':>13}   95% paired CI            verdict")
for gkey in GEOS[1:]:
    out=paired(R,gkey)
    if not out: print(f"  {gkey:<10}  INSUFFICIENT"); continue
    n,m,lo,hi=out
    v = "BETTER than original" if lo>0 else ("WORSE than original" if hi<0 else "inconclusive (CI crosses 0)")
    print(f"  {gkey:<10}{n:>7}{m:>13.3f}   [{lo:+.3f}, {hi:+.3f}]   {v}")

print()
print("="*118)
print("PART 5 — DOES R:R DEGRADATION WITH PRIOR MOVE DISAPPEAR?  (median initial R:R)")
print("="*118)
BINS=[(-99,0,"<0"),(0,0.25,"0-0.25"),(0.25,0.5,"0.25-0.5"),(0.5,0.75,"0.5-0.75"),
      (0.75,1.0,"0.75-1"),(1.0,1.5,"1-1.5"),(1.5,2.0,"1.5-2"),(2.0,3.0,"2-3"),(3.0,99,">3")]
show=["ORIG","PCT1.0","PCT2.0","ATR1.0","ATR2.0"]
print(f"  {'prior move':<12}{'n':>5}" + "".join(f"{g:>10}" for g in show))
for lo_,hi_,lab in BINS:
    sub=[r for r in R if r.get("prior") is not None and lo_<=r["prior"]<hi_]
    if len(sub)<15: continue
    line=f"  {lab:<12}{len(sub):>5}"
    for g in show:
        v=[r[g]["rr"] for r in sub if r.get(g)]
        line+=f"{(st.median(v) if v else float('nan')):>10.2f}"
    print(line)

print()
print("  ...and median FORWARD RETURN in the same buckets:")
print(f"  {'prior move':<12}{'n':>5}" + "".join(f"{g:>10}" for g in show))
for lo_,hi_,lab in BINS:
    sub=[r for r in R if r.get("prior") is not None and lo_<=r["prior"]<hi_]
    if len(sub)<15: continue
    line=f"  {lab:<12}{len(sub):>5}"
    for g in show:
        v=[r[g]["g"] for r in sub if r.get(g)]
        line+=f"{(st.median(v) if v else float('nan')):>10.3f}"
    print(line)
