import json, sys, statistics as st
from collections import defaultdict, Counter
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import boot_cluster, clustered
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
COST=0.2072
D=[json.loads(l) for l in open(f"{SP}/p5_buy.jsonl")]
H=["5","15","30","60","120","None"]; HL={"5":"+5m","15":"+15m","30":"+30m","60":"+60m","120":"+120m","None":"EOD"}
B=[r for r in D if r["sig"]=="BUY"]
S=sorted({r["d"] for r in B})
print("="*112)
print(f"PART D — BUY HISTORICAL SAMPLE   n={len(B):,}  symbols={len({r['sym'] for r in B}):,}  sessions={len(S)}")
print(f"  BUY is the stored label only. No threshold substituted, nothing tuned.")
print(f"  cost basis: MIS {COST}% round-trip")
print("="*112)
print(f"  {'horizon':>8} {'n':>7} {'sym':>5} {'gross%':>9} {'net%':>9} {'median%':>9} {'win%':>7}  {'95% CI (symbol-clustered)':>26}")
for h in H:
    v=[(r["sym"], r["f"][h]) for r in B if r["f"].get(h) is not None]
    if len(v)<50: continue
    lo,hi=boot_cluster(clustered(v)); m=st.mean([x for _,x in v])
    print(f"  {HL[h]:>8} {len(v):7,} {len(clustered(v)):5} {m:+9.3f} {m-COST:+9.3f} "
          f"{st.median([x for _,x in v]):+9.3f} {100*sum(1 for _,x in v if x>0)/len(v):7.1f}  "
          f"{('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']'):>26}")

print()
print("="*112); print("PART E — MATCHED CONTROL (paired, same instant, pre-observation matching only)"); print("="*112)
WC=[r for r in B if r.get("ctl") and r["ctl"].get("_n")]
print(f"  BUY observations with a usable matched control: {len(WC):,} of {len(B):,} ({100*len(WC)/len(B):.1f}%)")
if WC:
    def smd(a,b):
        p=(st.pstdev(a)**2+st.pstdev(b)**2)/2
        return (st.mean(a)-st.mean(b))/(p**0.5) if p>0 else 0.0
    print(f"  match quality  SMD r15 {smd([r['r15'] for r in WC],[r['ctl']['_r15'] for r in WC]):+.3f}  "
          f"SMD vol {smd([r['vol'] for r in WC],[r['ctl']['_vol'] for r in WC]):+.3f}  "
          f"SMD liquidity {smd([r['tv'] for r in WC],[r['ctl']['_tv'] for r in WC]):+.3f}")
    print(f"\n  {'horizon':>8} {'n':>7} {'BUY%':>9} {'control%':>9} {'diff%':>9}  {'95% CI':>26}  verdict")
    for h in H:
        p=[(r["sym"], r["f"][h]-r["ctl"][h]) for r in WC
           if r["f"].get(h) is not None and r["ctl"].get(h) is not None]
        if len(p)<50: continue
        R=[r for r in WC if r["f"].get(h) is not None and r["ctl"].get(h) is not None]
        lo,hi=boot_cluster(clustered(p))
        v="BUY>CTL" if lo>0 else ("BUY<CTL" if hi<0 else "inconclusive")
        print(f"  {HL[h]:>8} {len(p):7,} {st.mean([r['f'][h] for r in R]):+9.3f} "
              f"{st.mean([r['ctl'][h] for r in R]):+9.3f} {st.mean([x for _,x in p]):+9.3f}  "
              f"{('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']'):>26}  {v}")

print()
print("="*112); print("PART F — SESSION ROBUSTNESS (EOD) — mandatory"); print("="*112)
print(f"  {'session':<12} {'n':>6} {'sym':>5} {'gross%':>9} {'net%':>9} {'median%':>9} {'win%':>7}  {'95% CI':>24}")
signs=[]
for d in S:
    v=[(r["sym"], r["f"]["None"]) for r in B if r["d"]==d and r["f"].get("None") is not None]
    if len(v)<30: print(f"  {d:<12} {len(v):>6}  INSUFFICIENT SAMPLE"); continue
    lo,hi=boot_cluster(clustered(v)); m=st.mean([x for _,x in v])
    signs.append("+" if m>0 else "-")
    print(f"  {d:<12} {len(v):6,} {len(clustered(v)):5} {m:+9.3f} {m-COST:+9.3f} "
          f"{st.median([x for _,x in v]):+9.3f} {100*sum(1 for _,x in v if x>0)/len(v):7.1f}  "
          f"{('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']'):>24}")
print(f"\n  positive sessions: {signs.count('+')}/{len(signs)}   pattern {''.join(signs)}")

print()
print("="*112); print("PART G — SYMBOL CONCENTRATION"); print("="*112)
bysym=defaultdict(list)
for r in B:
    if r["f"].get("None") is not None: bysym[r["sym"]].append(r["f"]["None"])
tot=sum(sum(v) for v in bysym.values())
ranked=sorted(bysym.items(), key=lambda kv:-sum(kv[1]))
print(f"  unique symbols: {len(bysym):,}   total summed return {tot:+,.1f}pp")
for n in (5,10,20):
    print(f"  top {n:>2} contribution: {100*sum(sum(v) for _,v in ranked[:n])/tot:6.1f}%")
for n in (5,10):
    excl={s_ for s_,_ in ranked[:n]}
    v=[(r["sym"], r["f"]["None"]) for r in B if r["sym"] not in excl and r["f"].get("None") is not None]
    lo,hi=boot_cluster(clustered(v)); m=st.mean([x for _,x in v])
    print(f"  EXCLUDING top {n:>2}: n={len(v):,}  gross {m:+.3f}  net {m-COST:+.3f}  [{lo:+.3f}, {hi:+.3f}]")

print()
print("="*112); print("PART I — CHRONOLOGICAL OUT-OF-SAMPLE (no tuning on TRAIN; BUY fixed in advance)"); print("="*112)
mid=len(S)//2
TR,TE=S[:mid],S[mid:]
print(f"  TRAIN {TR[0]}..{TR[-1]} ({len(TR)} sessions)   TEST {TE[0]}..{TE[-1]} ({len(TE)} sessions)")
for lab,ss in (("TRAIN",TR),("TEST",TE)):
    v=[(r["sym"], r["f"]["None"]) for r in B if r["d"] in ss and r["f"].get("None") is not None]
    lo,hi=boot_cluster(clustered(v)); m=st.mean([x for _,x in v])
    print(f"  {lab:<6} n={len(v):>6,} sym={len(clustered(v)):>5}  gross {m:+.3f}  net {m-COST:+.3f}  [{lo:+.3f}, {hi:+.3f}]")
    c=[(r["sym"], r["f"]["None"]-r["ctl"]["None"]) for r in B if r["d"] in ss
       and r["f"].get("None") is not None and r.get("ctl",{}).get("None") is not None]
    if len(c)>=50:
        clo,chi=boot_cluster(clustered(c))
        print(f"         vs matched control: {st.mean([x for _,x in c]):+.3f}  [{clo:+.3f}, {chi:+.3f}]")

print()
print("="*112); print("PART J — BUY vs THE OTHER STORED LABELS (categorical only, nothing optimised)"); print("="*112)
print(f"  {'label':<14} {'n':>7} {'sym':>5} {'+30m%':>9} {'EOD gross%':>11} {'EOD net%':>10}  {'95% CI EOD':>26}")
for lab in ("BUY","STRONG_BUY","NEUTRAL","SELL"):
    G=[r for r in D if r["sig"]==lab]
    v=[(r["sym"], r["f"]["None"]) for r in G if r["f"].get("None") is not None]
    if len(v)<50: print(f"  {lab:<14} {len(v):>7}  INSUFFICIENT SAMPLE"); continue
    v30=[x for _,x in ((r["sym"], r["f"]["30"]) for r in G) if x is not None]
    lo,hi=boot_cluster(clustered(v)); m=st.mean([x for _,x in v])
    print(f"  {lab:<14} {len(v):7,} {len(clustered(v)):5} {st.mean(v30):+9.3f} {m:+11.3f} {m-COST:+10.3f}  "
          f"{('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']'):>26}")

print()
print("="*112); print("PART K — BUY vs RANDOM TIMESTAMP, same symbol & session (fixed horizons only)"); print("="*112)
print("  EOD is excluded: a random timestamp up to 60 bars earlier gets a longer window to close.")
print(f"  {'horizon':>8} {'n':>7} {'BUY%':>9} {'random-t%':>10} {'diff%':>9}  {'95% CI':>26}  verdict")
for h in ["5","15","30","60","120"]:
    p=[(r["sym"], r["f"][h]-r["rt"][h]) for r in B
       if r["f"].get(h) is not None and r.get("rt",{}).get(h) is not None]
    if len(p)<50: continue
    R=[r for r in B if r["f"].get(h) is not None and r.get("rt",{}).get(h) is not None]
    lo,hi=boot_cluster(clustered(p))
    v="BUY>RANDOM" if lo>0 else ("BUY<RANDOM" if hi<0 else "inconclusive")
    print(f"  {HL[h]:>8} {len(p):7,} {st.mean([r['f'][h] for r in R]):+9.3f} "
          f"{st.mean([r['rt'][h] for r in R]):+10.3f} {st.mean([x for _,x in p]):+9.3f}  "
          f"{('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']'):>26}  {v}")
