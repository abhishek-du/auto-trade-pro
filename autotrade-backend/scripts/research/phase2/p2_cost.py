import json, sys, statistics as st
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import boot_cluster, clustered
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=[json.loads(l) for l in open(f"{SP}/tac_obs.jsonl")]
DEL, INT = 0.3942, 0.2072
print("="*118); print("PART 18 — COST / FRICTION GATE, under BOTH defensible cost bases"); print("="*118)
print(f"  delivery basis (the project's own estimate_trade_cost + slippage mid): {DEL:.4f}%")
print(f"  intraday basis (MIS: STT 0.025% sell-only — correct after Phase 1A):   {INT:.4f}%")
print()
print(f"  {'group':<24} {'n':>5} {'gross%':>9} {'net(del)%':>10} {'net(MIS)%':>10} {'95% CI gross':>22}  monetizable?")
def row(lab, R):
    v=[(r["sym"], r["f"]["None"]) for r in R if r["f"].get("None") is not None]
    if len(v)<40: print(f"  {lab:<24} n={len(v):>5}  INSUFFICIENT SAMPLE"); return
    lo,hi=boot_cluster(clustered(v)); m=st.mean([x for _,x in v])
    ok = "YES (MIS only)" if (m-INT>0 and lo is not None and lo>INT) else ("marginal" if m-INT>0 else "NO")
    print(f"  {lab:<24} {len(v):5d} {m:+9.3f} {m-DEL:+10.3f} {m-INT:+10.3f} "
          f"{('['+format(lo,'+.3f')+','+format(hi,'+.3f')+']') if lo is not None else 'n/a':>22}  {ok}")
row("ALL tactical", D)
for s_ in sorted({r["strat"] for r in D}):
    row(s_, [r for r in D if r["strat"]==s_])
print()
V=[r for r in D if r["strat"]=="VOLUME_BREAKOUT" and r["f"].get("None") is not None]
bysym=defaultdict(list)
for r in V: bysym[r["sym"]].append(r["f"]["None"])
top6={s_ for s_,_ in sorted(bysym.items(), key=lambda kv:-sum(kv[1]))[:6]}
row("VOLUME_BREAKOUT ex-top6", [r for r in V if r["sym"] not in top6])
P=[r for r in D if r["strat"]=="PIVOT_BREAKOUT"]
bysym2=defaultdict(list)
for r in P:
    if r["f"].get("None") is not None: bysym2[r["sym"]].append(r["f"]["None"])
top6b={s_ for s_,_ in sorted(bysym2.items(), key=lambda kv:-sum(kv[1]))[:6]}
row("PIVOT_BREAKOUT ex-top6", [r for r in P if r["sym"] not in top6b])
