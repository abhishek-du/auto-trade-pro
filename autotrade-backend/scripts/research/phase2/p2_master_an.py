import json, sys, statistics as st
from collections import defaultdict, Counter
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import boot_cluster, clustered, COST_RT
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=[json.loads(l) for l in open(f"{SP}/master_obs.jsonl")]
H=["5","15","30","60","None"]; HL={"5":"+5m","15":"+15m","30":"+30m","60":"+60m","None":"EOD"}
S=sorted({r["d"] for r in D})
print("="*118); print(f"PART 13 — MASTER INTELLIGENCE AS AN INDEPENDENT SIGNAL")
print(f"n={len(D)}  sessions={len(S)} ({S[0]} .. {S[-1]})  symbols={len({r['sym'] for r in D})}")
print("  Long-only convention: forward return measured LONG. A bullish score should be positive.")
print("="*118)

def rep(lab, R, h="None", indent="  "):
    v=[(r["sym"], r["f"][h]) for r in R if r["f"].get(h) is not None]
    if len(v)<100:
        print(f"{indent}{lab:<24} n={len(v):>6}  INSUFFICIENT SAMPLE"); return None
    lo,hi=boot_cluster(clustered(v))
    vals=[x for _,x in v]
    print(f"{indent}{lab:<24} n={len(vals):>6} sym={len(clustered(v)):>4}  gross {st.mean(vals):+7.3f}  "
          f"net {st.mean(vals)-COST_RT:+7.3f}  med {st.median(vals):+7.3f}  win {100*sum(1 for x in vals if x>0)/len(vals):5.1f}%  "
          f"[{lo:+.3f},{hi:+.3f}]" if lo is not None else "")
    return st.mean(vals)

print("\nA. OVERALL (all scores, long) — the unconditional baseline")
for h in H: rep(HL[h], D, h)

print("\nB. SCORE PERCENTILE BUCKETS (bucket edges fixed by percentile, not chosen after seeing results)")
sc=sorted(r["ms"] for r in D)
def pct(p): return sc[int(p/100*(len(sc)-1))]
EDGES=[(0,20),(20,40),(40,60),(60,80),(80,90),(90,95),(95,100)]
print(f"   score range: {sc[0]:.1f} .. {sc[-1]:.1f}   median {pct(50):.1f}")
for h in ["30","None"]:
    print(f"\n   --- horizon {HL[h]} ---")
    for a,b in EDGES:
        lo_,hi_=pct(a),pct(b)
        R=[r for r in D if lo_<=r["ms"]<=hi_]
        rep(f"p{a}-{b} [{lo_:.1f},{hi_:.1f}]", R, h, indent="   ")

print("\nC. SIGNAL LABEL (the Hub's own categorical output)")
for h in ["30","None"]:
    print(f"\n   --- horizon {HL[h]} ---")
    for lab in sorted({r["sig"] for r in D if r["sig"]}):
        rep(str(lab), [r for r in D if r["sig"]==lab], h, indent="   ")

print("\nD. TOP-RANK (rank is the Hub's own ordering)")
for h in ["30","None"]:
    print(f"\n   --- horizon {HL[h]} ---")
    for lo_,hi_,lab in ((1,5,"rank 1-5"),(1,20,"rank 1-20"),(1,50,"rank 1-50"),(51,200,"rank 51-200")):
        rep(lab, [r for r in D if r["rank"] and lo_<=r["rank"]<=hi_], h, indent="   ")

print("\nE. SPREAD TEST — top decile minus bottom decile (the cleanest statement of rank information)")
for h in H:
    top=[(r["sym"], r["f"][h]) for r in D if r["ms"]>=pct(90) and r["f"].get(h) is not None]
    bot=[(r["sym"], r["f"][h]) for r in D if r["ms"]<=pct(10) and r["f"].get(h) is not None]
    if len(top)<100 or len(bot)<100: continue
    tlo,thi=boot_cluster(clustered(top)); blo,bhi=boot_cluster(clustered(bot))
    sp=st.mean([x for _,x in top])-st.mean([x for _,x in bot])
    print(f"   {HL[h]:>5}  top-decile {st.mean([x for _,x in top]):+7.3f} [{tlo:+.3f},{thi:+.3f}]   "
          f"bottom-decile {st.mean([x for _,x in bot]):+7.3f} [{blo:+.3f},{bhi:+.3f}]   spread {sp:+7.3f}pp")

print("\n" + "="*118); print("PART 15 — SESSION ROBUSTNESS of the top decile (EOD)"); print("="*118)
signs=[]
for d in S:
    R=[r for r in D if r["d"]==d and r["ms"]>=pct(90)]
    m=rep(d, R, "None", indent="   ")
    if m is not None: signs.append("+" if m>0 else "-")
print(f"\n   session sign pattern: {''.join(signs)}   positive {signs.count('+')}/{len(signs)}")

print("\n" + "="*118); print("PART 14 — COMPONENT SCORES (does master add anything beyond them?)"); print("="*118)
for comp in ("tech","news","sector","macro","earn","fund"):
    vv=[r for r in D if r.get(comp) is not None and r["f"].get("None") is not None]
    if len(vv)<300:
        print(f"\n   {comp:<10} INSUFFICIENT SAMPLE (n={len(vv)})"); continue
    s2=sorted(r[comp] for r in vv)
    p90=s2[int(.90*(len(s2)-1))]; p10=s2[int(.10*(len(s2)-1))]
    top=[(r["sym"], r["f"]["None"]) for r in vv if r[comp]>=p90]
    bot=[(r["sym"], r["f"]["None"]) for r in vv if r[comp]<=p10]
    if len(top)<100 or len(bot)<100:
        print(f"\n   {comp:<10} INSUFFICIENT SAMPLE in tails"); continue
    tlo,thi=boot_cluster(clustered(top))
    sp=st.mean([x for _,x in top])-st.mean([x for _,x in bot])
    print(f"   {comp:<10} top-decile n={len(top):>6} {st.mean([x for _,x in top]):+7.3f} [{tlo:+.3f},{thi:+.3f}]  "
          f"bottom {st.mean([x for _,x in bot]):+7.3f}  spread {sp:+7.3f}pp")
