import json, random, statistics as st
from collections import defaultdict
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=json.load(open(f"{SP}/matched.json"))
ARMS=("MOM","VOL","LIQ","CMP","CMP2")
def boot(x,n=4000):
    if len(x)<8: return (None,None)
    m=sorted(sum(random.choices(x,k=len(x)))/len(x) for _ in range(n))
    return (m[int(.025*n)], m[int(.975*n)])

# ── isolate match quality: hold the signal set fixed at the CMP2-eligible subset
S=[r for r in D if all(r.get(f"{a}EOD") is not None for a in ARMS)]
print("="*100)
print(f"MATCH-QUALITY ISOLATION — identical {len(S)} signals, only the control definition changes")
print("="*100)
print(f"{'arm':6} {'SMDmom':>7} {'SMDliq':>7} | {'signal EOD%':>12} {'control EOD%':>13} {'diff%':>8} {'95% CI':>20}")
for a in ARMS:
    sm=[r["mom"] for r in S]; cm=[r[f"{a}_mom"] for r in S]
    sl=[r["liq"] for r in S]; cl=[r[f"{a}_liq"] for r in S]
    def smd(x,y):
        p=((st.pstdev(x)**2+st.pstdev(y)**2)/2)**0.5
        return (st.mean(x)-st.mean(y))/p if p>0 else 0.0
    d=[r["rEOD"]-r[f"{a}EOD"] for r in S if r.get("rEOD") is not None]
    lo,hi=boot(d)
    print(f"{a:6} {smd(sm,cm):+7.3f} {smd(sl,cl):+7.3f} | {st.mean([r['rEOD'] for r in S if r.get('rEOD') is not None]):+12.3f} "
          f"{st.mean([r[f'{a}EOD'] for r in S if r.get('rEOD') is not None]):+13.3f} {st.mean(d):+8.3f} "
          f"{'['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']':>20}")

print()
print("="*100)
print("SUBGROUPS — balanced arm (CMP2) only, the one arm where |SMD| < 0.12 on all three axes")
print("="*100)
def rep(label, R, H="EOD", a="CMP2"):
    P=[(r[f"r{H}"], r[f"{a}{H}"]) for r in R if r.get(f"r{H}") is not None and r.get(f"{a}{H}") is not None]
    if len(P)<15:
        print(f"{label:26} n={len(P):4d}   INSUFFICIENT (n<15) — EVIDENCE NOT AVAILABLE"); return
    d=[x-y for x,y in P]; lo,hi=boot(d)
    v="SIG>CTL" if lo>0 else ("SIG<CTL" if hi<0 else "inconclusive")
    print(f"{label:26} n={len(P):4d}  sig {st.mean([x for x,_ in P]):+7.3f}  ctl {st.mean([y for _,y in P]):+7.3f}  "
          f"diff {st.mean(d):+7.3f}  [{lo:+.3f}, {hi:+.3f}]  {v}")

C=[r for r in D if r.get("CMP2EOD") is not None]
print("\n-- by rule --")
for s_ in sorted({r["strat"] for r in C}):
    rep(s_, [r for r in C if r["strat"]==s_])
print("\n-- momentum rules vs mean-reversion rules --")
MOMR={"ORB","VWAP","GAP_AND_GO","PIVOT_BREAKOUT","DAY_MOMENTUM","VOLUME_BREAKOUT","VWAP_CROSSOVER","SCALP"}
MRR={"OVERBOUGHT_FADE","OVERSOLD_REBOUND","PIVOT_BOUNCE","DAY_WEAKNESS"}
rep("momentum family", [r for r in C if r["strat"] in MOMR])
rep("mean-reversion family", [r for r in C if r["strat"] in MRR])
print("\n-- by direction --")
for sd in ("BUY","SELL"):
    rep(sd, [r for r in C if (r["side"] or "BUY").upper()==sd])
print("\n-- by session (fragility: only 3 sessions exist) --")
for s_ in sorted({r["sess"] for r in C}):
    rep(s_, [r for r in C if r["sess"]==s_])
print("\n-- by time of day --")
for lab,lo_,hi_ in (("09:15-11:00","09:15","11:00"),("11:00-13:00","11:00","13:00"),("13:00-15:30","13:00","15:30")):
    rep(lab, [r for r in C if lo_<=r["tod"]<hi_])

print()
print("="*100)
print("TRADABILITY — is the measured difference larger than the round-trip cost?")
print("="*100)
COST=0.222
for a in ARMS:
    P=[(r["rEOD"], r[f"{a}EOD"]) for r in D if r.get("rEOD") is not None and r.get(f"{a}EOD") is not None]
    d=st.mean([x-y for x,y in P]); sg=st.mean([x for x,_ in P])
    print(f"{a:6} n={len(P):4d}  relative diff {d:+.3f}%  |  signal absolute {sg:+.3f}%  "
          f"net of {COST}% cost: {sg-COST:+.3f}%  {'TRADABLE' if sg-COST>0 else 'NOT TRADABLE'}")
