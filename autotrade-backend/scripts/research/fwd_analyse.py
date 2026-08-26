import json, random, statistics as st
from collections import Counter
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
R=json.load(open(f"{SP}/fwd.json"))
res=[r for r in R if r["outcome"]!="UNRESOLVED_DATA"]
print(f"total signals        : {len(R)}")
print(f"UNRESOLVED_DATA      : {len(R)-len(res)}  (1,800 of these are 25-Aug signals with no next session yet)")
print(f"FORWARD-RESOLVED     : {len(res)}   <- every one ends in SL, TP or TIME_EXIT")
print()
c=Counter(r["outcome"] for r in res)
print("OUTCOME MIX (complete — nothing marked to an arbitrary bar)")
for k,v in c.most_common(): print(f"   {k:<12}{v:>6}  {v/len(res)*100:>5.1f}%")
print()
g=[r["gross"] for r in res]
print(f"gross: median {st.median(g):+.3f}%  mean {sum(g)/len(g):+.3f}%  "
      f"win {len([x for x in g if x>0])/len(g)*100:.1f}%")

def paired(real, null, n=4000):
    """Paired bootstrap on (real_i - null_i). Only pairs where both exist."""
    d=[a-b for a,b in zip(real,null) if a is not None and b is not None]
    if len(d)<30: return None
    m=sum(d)/len(d); ms=[]
    for _ in range(n):
        s=[d[random.randrange(len(d))] for _ in range(len(d))]; ms.append(sum(s)/len(s))
    ms.sort(); return len(d), m, ms[int(n*.025)], ms[int(n*.975)]

print()
print("="*94)
print("THREE MARKING-INVARIANT NULLS — paired differences, forward-resolved only")
print("="*94)
print(f"  {'comparison':<34}{'pairs':>7}{'real':>9}{'null':>9}{'diff':>9}   95% CI (paired)")
for key,lab in (("n_sym","real symbol vs random symbol"),
                ("n_dir","real direction vs random direction"),
                ("n_time","real timestamp vs random timestamp")):
    pr=[r["gross"] for r in res if r.get(key) is not None]
    pn=[r[key]     for r in res if r.get(key) is not None]
    out=paired(pr,pn)
    if out is None: print(f"  {lab:<34} insufficient pairs"); continue
    n,m,lo,hi=out
    verdict = "signal ADDS information" if lo>0 else ("signal SUBTRACTS information" if hi<0 else "INDISTINGUISHABLE from the null")
    print(f"  {lab:<34}{n:>7}{sum(pr)/len(pr):>9.3f}{sum(pn)/len(pn):>9.3f}{m:>+9.3f}   [{lo:+.3f}, {hi:+.3f}]  {verdict}")

print()
print("="*94)
print("FORWARD RETURNS after the signal timestamp vs after a RANDOM timestamp")
print("  (same symbol, same session, same forward window; paired)")
print("="*94)
print(f"  {'horizon':<10}{'pairs':>7}{'real med':>10}{'real mean':>11}{'rand mean':>11}{'diff':>9}   95% CI (paired)")
for h in [1,3,5,10,15,30,60]:
    pr=[r[f"f{h}"] for r in res if r.get(f"f{h}") is not None and (r.get("n_time_f") or {}).get(str(h)) is not None]
    pn=[(r.get("n_time_f") or {})[str(h)] for r in res if r.get(f"f{h}") is not None and (r.get("n_time_f") or {}).get(str(h)) is not None]
    out=paired(pr,pn)
    if out is None: print(f"  +{h}m       insufficient"); continue
    n,m,lo,hi=out
    star = " *" if (lo>0 or hi<0) else ""
    print(f"  +{h}m{'':<6}{n:>7}{st.median(pr):>10.3f}{sum(pr)/len(pr):>11.3f}{sum(pn)/len(pn):>11.3f}{m:>+9.3f}   [{lo:+.3f}, {hi:+.3f}]{star}")

print()
print("="*94)
print("EXCURSIONS over the full forward window")
print("="*94)
mfe=[r["mfe"] for r in res]; mae=[r["mae"] for r in res]
tm=[r["t_mfe"] for r in res]; ta=[r["t_mae"] for r in res]
def q(v,p):
    v=sorted(v); return v[max(0,min(len(v)-1,int(round(p*(len(v)-1)))))]
print(f"  MFE %   median {st.median(mfe):+.3f}   p75 {q(mfe,.75):+.3f}   p90 {q(mfe,.90):+.3f}")
print(f"  MAE %   median {st.median(mae):+.3f}   p25 {q(mae,.25):+.3f}   p10 {q(mae,.10):+.3f}")
print(f"  time-to-MFE  median {st.median(tm):>6.0f} min      time-to-MAE  median {st.median(ta):>6.0f} min")
print(f"  MFE/|MAE| ratio at the median: {st.median(mfe)/abs(st.median(mae)):.3f}")
