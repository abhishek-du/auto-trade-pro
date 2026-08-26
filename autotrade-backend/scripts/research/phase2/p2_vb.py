"""Stress the one surviving tactical candidate. Try to break it."""
import json, sys, statistics as st
from collections import defaultdict, Counter
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import boot_cluster, clustered, COST_RT
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=[json.loads(l) for l in open(f"{SP}/tac_obs.jsonl")]
V=[r for r in D if r["strat"]=="VOLUME_BREAKOUT" and r["f"].get("None") is not None]
print("="*104); print(f"VOLUME_BREAKOUT STRESS TEST   n={len(V)}"); print("="*104)

print("\n1. PER SESSION — a result on one day is FRAGILE by definition")
for d in sorted({r["d"] for r in V}):
    g=[r for r in V if r["d"]==d]
    v=[(r["sym"], r["f"]["None"]) for r in g]
    lo,hi=boot_cluster(clustered(v))
    ci=f"[{lo:+.3f},{hi:+.3f}]" if lo is not None else "INSUFFICIENT CLUSTERS"
    print(f"   {d}  n={len(v):>4} sym={len(clustered(v)):>3}  gross {st.mean([x for _,x in v]):+7.3f}  "
          f"net {st.mean([x for _,x in v])-COST_RT:+7.3f}  win {100*sum(1 for _,x in v if x>0)/len(v):5.1f}%  {ci}")

print("\n2. SYMBOL CONCENTRATION — is it one stock?")
bysym=defaultdict(list)
for r in V: bysym[r["sym"]].append(r["f"]["None"])
tot=sum(sum(v) for v in bysym.values())
top=sorted(bysym.items(), key=lambda kv:-sum(kv[1]))[:6]
print(f"   {len(bysym)} symbols, total summed return {tot:+.1f}pp")
for s_,v in top:
    print(f"     {s_:<16} n={len(v):>3}  sum {sum(v):+8.2f}pp  ({100*sum(v)/tot:5.1f}% of total)  mean {st.mean(v):+.3f}")
rest=[x for s_,v in bysym.items() if s_ not in dict(top) for x in v]
if rest:
    lo,hi=boot_cluster(clustered([(s_,x) for s_,v in bysym.items() if s_ not in dict(top) for x in v]))
    print(f"   EXCLUDING the top 6 symbols: n={len(rest)}  gross {st.mean(rest):+7.3f}  "
          f"net {st.mean(rest)-COST_RT:+7.3f}  [{lo:+.3f},{hi:+.3f}]" if lo is not None else "")

print("\n3. OUT OF SAMPLE — chronological, session level")
tr=[r for r in V if r["d"] in ("2026-08-20","2026-08-21")]
te=[r for r in V if r["d"] in ("2026-08-24","2026-08-25")]
for lab,S in (("TRAIN 08-20/21",tr),("TEST 08-24/25",te)):
    if len(S)<15: print(f"   {lab:<16} n={len(S)}  INSUFFICIENT SAMPLE / EVIDENCE NOT AVAILABLE"); continue
    v=[(r["sym"], r["f"]["None"]) for r in S]
    lo,hi=boot_cluster(clustered(v))
    ci=f"[{lo:+.3f},{hi:+.3f}]" if lo is not None else "n/a"
    print(f"   {lab:<16} n={len(v):>4}  gross {st.mean([x for _,x in v]):+7.3f}  "
          f"net {st.mean([x for _,x in v])-COST_RT:+7.3f}  {ci}")

print("\n4. HORIZON PROFILE — a real effect should not appear only at one horizon")
for h in ["5","15","30","60","120","None"]:
    v=[(r["sym"], r["f"][h]) for r in V if r["f"].get(h) is not None]
    if len(v)<40: continue
    lo,hi=boot_cluster(clustered(v))
    c=[(r["sym"], r["f"][h]-r["ctl"]["MATCH"]["f"][h]) for r in V
       if r["f"].get(h) is not None and r.get("ctl",{}).get("MATCH") and r["ctl"]["MATCH"]["f"].get(h) is not None]
    cs=""
    if len(c)>=25:
        clo,chi=boot_cluster(clustered(c))
        cs=f"  ctl-diff {st.mean([x for _,x in c]):+7.3f} [{clo:+.3f},{chi:+.3f}]"
    print(f"   {('+'+h+'m') if h!='None' else 'EOD':>6}  n={len(v):>4}  gross {st.mean([x for _,x in v]):+7.3f}  "
          f"net {st.mean([x for _,x in v])-COST_RT:+7.3f}  [{lo:+.3f},{hi:+.3f}]{cs}")

print("\n5. IS IT EXPLAINED BY A CONTROL VARIABLE? (compare VB to all other signals matched on the same feature)")
others=[r for r in D if r["strat"]!="VOLUME_BREAKOUT" and r["f"].get("None") is not None]
for k,lab in (("tv","liquidity"),("vol15","realised vol"),("surge","volume surge"),("r15","recent 15m ret")):
    vv=[r["pre"].get(k) for r in V if r["pre"].get(k) is not None]
    if not vv: continue
    lo_,hi_=min(vv),max(vv)
    peer=[r for r in others if r["pre"].get(k) is not None and lo_<=r["pre"][k]<=hi_]
    if len(peer)<40: 
        print(f"   {lab:<16} peer n={len(peer)} INSUFFICIENT"); continue
    pv=[(r["sym"], r["f"]["None"]) for r in peer]
    plo,phi=boot_cluster(clustered(pv))
    print(f"   {lab:<16} VB mean {st.mean([r['f']['None'] for r in V]):+7.3f}  |  "
          f"other rules in VB's {lab} range: n={len(pv):>5} mean {st.mean([x for _,x in pv]):+7.3f} "
          f"[{plo:+.3f},{phi:+.3f}]")

print("\n6. DIRECTION MIX")
print("   ", Counter(r["side"] for r in V))
print("\n7. WHAT ACTUALLY HAPPENED TO THESE — were they even executed?")
print("   ", Counter((r["ro"] or "none") for r in V))
