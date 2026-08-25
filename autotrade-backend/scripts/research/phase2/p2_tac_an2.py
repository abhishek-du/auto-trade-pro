import json, sys, statistics as st
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import boot_cluster, clustered, COST_RT
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=[json.loads(l) for l in open(f"{SP}/tac_obs.jsonl")]
MIN_N=40

def line(lab, R, h="None", key="MATCH"):
    v=[(r["sym"], r["f"][h]) for r in R if r["f"].get(h) is not None]
    if len(v)<MIN_N:
        print(f"  {lab:<20} n={len(v):>5}  INSUFFICIENT SAMPLE"); return
    g=clustered(v); lo,hi=boot_cluster(g)
    vals=[x for _,x in v]
    sess=len({r['d'] for r in R})
    c=[(r["sym"], r["f"][h]-r["ctl"][key]["f"][h]) for r in R
       if r["f"].get(h) is not None and r.get("ctl",{}).get(key) and r["ctl"][key]["f"].get(h) is not None]
    cd=""
    if len(c)>=MIN_N:
        clo,chi=boot_cluster(clustered(c))
        cd=f"  ctl-diff {st.mean([x for _,x in c]):+7.3f} [{clo:+.3f},{chi:+.3f}]"
    wins=[x for x in vals if x>0]; loss=[-x for x in vals if x<0]
    pf=(sum(wins)/sum(loss)) if loss and sum(loss)>0 else float('nan')
    print(f"  {lab:<20} n={len(vals):>5} sess={sess} sym={len(g):>4}  gross {st.mean(vals):+7.3f}  "
          f"net {st.mean(vals)-COST_RT:+7.3f}  med {st.median(vals):+7.3f}  win {100*len(wins)/len(vals):5.1f}%  "
          f"PF {pf:5.2f}  [{lo:+.3f},{hi:+.3f}]{cd}")

print("="*136); print("PART 5 — RULE BY RULE (EOD horizon, symbol-clustered CI, matched-control diff)"); print("="*136)
for s_ in sorted({r["strat"] for r in D}):
    line(s_, [r for r in D if r["strat"]==s_])

print()
print("="*136); print("PART 15 — SESSION ROBUSTNESS (EOD)"); print("="*136)
for d in sorted({r["d"] for r in D}):
    line(d, [r for r in D if r["d"]==d])
print("\n  per-rule x per-session (EOD gross), blank = INSUFFICIENT:")
rules=sorted({r["strat"] for r in D}); sessions=sorted({r["d"] for r in D})
print(f"  {'rule':<20}" + "".join(f"{s[5:]:>12}" for s in sessions))
for s_ in rules:
    row=f"  {s_:<20}"
    for d in sessions:
        v=[r["f"]["None"] for r in D if r["strat"]==s_ and r["d"]==d and r["f"].get("None") is not None]
        row += f"{(format(st.mean(v),'+.3f')+f'({len(v)})') if len(v)>=MIN_N else '—':>12}"
    print(row)

print()
print("="*136); print("PART 6 — FEATURE DECOMPOSITION (pre-signal only, terciles, EOD)"); print("="*136)
FEATS=[("r5","recent 5m return"),("r15","recent 15m return"),("surge","volume surge"),
       ("vwap_dist","VWAP distance %"),("rng_pct","range percentile"),("vol15","realised vol 15m"),
       ("tod","time of day (min)"),("tv","liquidity (traded value)")]
for k,lab in FEATS:
    vals=[(r["pre"].get(k), r) for r in D if r["pre"].get(k) is not None and r["f"].get("None") is not None]
    if len(vals)<3*MIN_N:
        print(f"\n  {lab:<26} INSUFFICIENT SAMPLE (n={len(vals)})"); continue
    vals.sort(key=lambda x:x[0])
    n=len(vals); cut=[vals[:n//3], vals[n//3:2*n//3], vals[2*n//3:]]
    print(f"\n  {lab}:")
    for name,grp in zip(("low  ","mid  ","high "),cut):
        v=[(r["sym"], r["f"]["None"]) for _,r in grp]
        g=clustered(v); lo,hi=boot_cluster(g)
        rng=f"[{grp[0][0]:.3f} .. {grp[-1][0]:.3f}]"
        # session stability: sign of the mean per session
        per={}
        for d in sorted({r['d'] for _,r in grp}):
            vv=[r["f"]["None"] for _,r in grp if r["d"]==d and r["f"].get("None") is not None]
            if len(vv)>=15: per[d[5:]]=st.mean(vv)
        stab="".join("+" if x>0 else "-" for x in per.values())
        print(f"    {name} {rng:<28} n={len(v):>5}  gross {st.mean([x for _,x in v]):+7.3f}  "
              f"net {st.mean([x for _,x in v])-COST_RT:+7.3f}  [{lo:+.3f},{hi:+.3f}]  sessions {stab or 'n/a'}")

print()
print("="*136); print("PART 16 — OUT OF SAMPLE (chronological, session-level split)"); print("="*136)
tr=[r for r in D if r["d"] in ("2026-08-20","2026-08-21")]
te=[r for r in D if r["d"] in ("2026-08-24","2026-08-25")]
print(f"  TRAIN 08-20,08-21: n={len(tr)}   TEST 08-24,08-25: n={len(te)}")
for lab,S in (("TRAIN",tr),("TEST",te)):
    v=[(r["sym"], r["f"]["None"]) for r in S if r["f"].get("None") is not None]
    g=clustered(v); lo,hi=boot_cluster(g)
    print(f"  {lab:<6} n={len(v):>5}  gross {st.mean([x for _,x in v]):+7.3f}  "
          f"net {st.mean([x for _,x in v])-COST_RT:+7.3f}  [{lo:+.3f},{hi:+.3f}]")
print("\n  best TRAIN rule carried forward to TEST (no re-selection):")
best=None
for s_ in sorted({r["strat"] for r in tr}):
    v=[r["f"]["None"] for r in tr if r["strat"]==s_ and r["f"].get("None") is not None]
    if len(v)>=MIN_N and (best is None or st.mean(v)>best[1]): best=(s_, st.mean(v), len(v))
if best:
    s_,m,n=best
    tv=[(r["sym"], r["f"]["None"]) for r in te if r["strat"]==s_ and r["f"].get("None") is not None]
    if len(tv)>=MIN_N:
        lo,hi=boot_cluster(clustered(tv))
        print(f"    TRAIN best = {s_}  (gross {m:+.3f}, n={n})")
        print(f"    TEST       = {s_}  gross {st.mean([x for _,x in tv]):+7.3f}  "
              f"net {st.mean([x for _,x in tv])-COST_RT:+7.3f}  n={len(tv)}  [{lo:+.3f},{hi:+.3f}]")
    else:
        print(f"    TRAIN best = {s_}, but TEST n={len(tv)} — INSUFFICIENT SAMPLE")
