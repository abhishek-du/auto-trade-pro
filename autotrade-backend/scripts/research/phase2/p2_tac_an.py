import json, sys, statistics as st
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import describe, fmt, boot_cluster, clustered, COST_RT
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=[json.loads(l) for l in open(f"{SP}/tac_obs.jsonl")]
H=["1","3","5","10","15","30","60","120","None"]
HL={"1":"+1m","3":"+3m","5":"+5m","10":"+10m","15":"+15m","30":"+30m","60":"+60m","120":"+120m","None":"EOD"}
print("="*112); print(f"PART 2 — TACTICAL FORWARD RETURNS   n={len(D)}  sessions={sorted({r['d'] for r in D})}")
print(f"cost model: {COST_RT:.4f}% round-trip (project's own estimate_trade_cost at Rs50k + slippage mid)")
print("="*112)
print(f"{'horizon':>8} {'n':>6} {'sym':>5} {'gross%':>9} {'net%':>9} {'median%':>9} {'win%':>7}  {'95% CI (symbol-clustered)':>28}")
for h in H:
    p=[(r["sym"], r["f"][h]) for r in D if r["f"].get(h) is not None]
    d=describe(p, HL[h])
    if d: print(f"{HL[h]:>8} {d['n']:6d} {d['symbols']:5d} {d['mean']:+9.3f} {d['net']:+9.3f} "
                f"{d['median']:+9.3f} {d['win']:7.1f}  {('['+format(d['lo'],'+.3f')+', '+format(d['hi'],'+.3f')+']') if d['lo'] is not None else 'n/a':>28}")
print()
mfe=[r["mfe"] for r in D if r.get("mfe") is not None]; mae=[r["mae"] for r in D if r.get("mae") is not None]
tf=[r["t_mfe"] for r in D if r.get("t_mfe")]; tm=[r["t_mae"] for r in D if r.get("t_mae")]
print(f"MFE(60m) mean {st.mean(mfe):+.3f}%  median {st.median(mfe):+.3f}%   |  MAE(60m) mean {st.mean(mae):+.3f}%  median {st.median(mae):+.3f}%")
print(f"time-to-MFE median {st.median(tf):.0f}m   time-to-MAE median {st.median(tm):.0f}m")

print()
print("="*112); print("PART 4 — BASELINES (paired: signal minus its control, same instant, same direction)"); print("="*112)
for lab,key in (("A market (40 liquid syms)","MKT"),("B matched control","MATCH")):
    print(f"\n--- {lab} ---")
    print(f"{'horizon':>8} {'n':>6} {'signal%':>9} {'control%':>9} {'diff%':>9}  {'95% CI':>26}  verdict")
    for h in H:
        p=[(r["sym"], r["f"][h]-r["ctl"][key]["f"][h])
           for r in D if r["f"].get(h) is not None and r.get("ctl",{}).get(key)
           and r["ctl"][key]["f"].get(h) is not None]
        if len(p)<20: continue
        g=clustered(p); lo,hi=boot_cluster(g)
        sg=st.mean([r["f"][h] for r in D if r["f"].get(h) is not None and r.get("ctl",{}).get(key) and r["ctl"][key]["f"].get(h) is not None])
        cl=st.mean([r["ctl"][key]["f"][h] for r in D if r["f"].get(h) is not None and r.get("ctl",{}).get(key) and r["ctl"][key]["f"].get(h) is not None])
        m=st.mean([v for _,v in p])
        v="SIG>CTL" if lo is not None and lo>0 else ("SIG<CTL" if hi is not None and hi<0 else "inconclusive")
        print(f"{HL[h]:>8} {len(p):6d} {sg:+9.3f} {cl:+9.3f} {m:+9.3f}  "
              f"{('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']') if lo is not None else 'n/a':>26}  {v}")
# match quality
print("\nmatch quality (standardised mean difference, |SMD|<0.10 = balanced):")
for key in ("MKT","MATCH"):
    R=[r for r in D if r.get("ctl",{}).get(key)]
    if len(R)<20: continue
    def smd(a,b):
        pa=(st.pstdev(a)**2+st.pstdev(b)**2)/2
        return (st.mean(a)-st.mean(b))/(pa**0.5) if pa>0 else 0.0
    sr=[r["pre"]["r15"] for r in R if r["pre"].get("r15") is not None]
    cr=[r["ctl"][key]["r15"] for r in R if r["pre"].get("r15") is not None]
    sv=[r["pre"]["vol15"] for r in R if r["pre"].get("vol15") is not None]
    cv=[r["ctl"][key]["vol15"] for r in R if r["pre"].get("vol15") is not None]
    sl_=[r["pre"]["tv"] for r in R]; cl_=[r["ctl"][key]["tv"] for r in R]
    print(f"  {key:6} n={len(R):5d}  SMD r15 {smd(sr,cr):+.3f}  SMD vol15 {smd(sv,cv):+.3f}  SMD liquidity {smd(sl_,cl_):+.3f}")

print()
print("="*112); print("PART 7 — DIRECTION: real vs opposite vs random"); print("="*112)
import random as _r; _r.seed(20260825)
print(f"{'horizon':>8} {'n':>6} {'real%':>9} {'opposite%':>10} {'random%':>9} {'real-random':>12}  {'95% CI':>24}")
for h in H:
    R=[r for r in D if r["f"].get(h) is not None]
    if len(R)<30: continue
    real=[(r["sym"], r["f"][h]) for r in R]
    opp =[(r["sym"], -r["f"][h]) for r in R]
    rnd =[(r["sym"], r["f"][h] if _r.random()<.5 else -r["f"][h]) for r in R]
    diff=[(r[0], r[1]-x[1]) for r,x in zip(real,rnd)]
    lo,hi=boot_cluster(clustered(diff))
    print(f"{HL[h]:>8} {len(R):6d} {st.mean([v for _,v in real]):+9.3f} {st.mean([v for _,v in opp]):+10.3f} "
          f"{st.mean([v for _,v in rnd]):+9.3f} {st.mean([v for _,v in diff]):+12.3f}  "
          f"{('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']') if lo is not None else 'n/a':>24}")

print()
print("="*112); print("PART 8 — TIMING: real timestamp vs random timestamp, SAME symbol/session/±60m"); print("="*112)
print(f"{'horizon':>8} {'n':>6} {'real%':>9} {'random-t%':>10} {'diff%':>9}  {'95% CI':>26}  verdict")
for h in H:
    p=[(r["sym"], r["f"][h]-r["rt"][h]) for r in D
       if r["f"].get(h) is not None and r.get("rt",{}).get(h) is not None]
    if len(p)<30: continue
    R=[r for r in D if r["f"].get(h) is not None and r.get("rt",{}).get(h) is not None]
    lo,hi=boot_cluster(clustered(p))
    v="REAL>RANDOM" if lo is not None and lo>0 else ("REAL<RANDOM" if hi is not None and hi<0 else "inconclusive")
    print(f"{HL[h]:>8} {len(p):6d} {st.mean([r['f'][h] for r in R]):+9.3f} {st.mean([r['rt'][h] for r in R]):+10.3f} "
          f"{st.mean([v2 for _,v2 in p]):+9.3f}  {('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']') if lo is not None else 'n/a':>26}  {v}")
