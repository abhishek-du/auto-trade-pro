import json, random, statistics as st
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=json.load(open(f"{SP}/matched.json")); COST=0.222
def boot(x,n=6000):
    if len(x)<8: return (None,None)
    m=sorted(sum(random.choices(x,k=len(x)))/len(x) for _ in range(n)); return (m[int(.025*n)],m[int(.975*n)])

print("="*98)
print("THE UNMATCHABLE HALF — can anything be concluded about it?")
print("="*98)
N=[r for r in D if r.get("CMP2EOD") is None and r.get("rEOD") is not None]
a=[r["rEOD"] for r in N]; lo,hi=boot(a)
print(f"n={len(a)}  absolute EOD {st.mean(a):+.3f}% [{lo:+.3f}, {hi:+.3f}]  median {st.median(a):+.3f}%  "
      f"win {100*sum(1 for x in a if x>0)/len(a):.1f}%")
print(f"  net of {COST}% round-trip cost: {st.mean(a)-COST:+.3f}%  "
      f"CI net [{lo-COST:+.3f}, {hi-COST:+.3f}]  -> {'positive' if lo-COST>0 else 'CI includes/below zero'}")
print(f"  MATCHED CONTROL: does not exist by construction -> EVIDENCE NOT AVAILABLE for a selection claim")

print()
print("="*98)
print("GAP_AND_GO — the one rule surviving Holm correction. Is the information monetisable?")
print("="*98)
G=[r for r in D if r["strat"]=="GAP_AND_GO"]
for lab,sub,arm in (("all GAP_AND_GO signals", G, None),
                    ("  ...with a close match", [r for r in G if r.get("CMP2EOD") is not None], "CMP2")):
    ab=[r["rEOD"] for r in sub if r.get("rEOD") is not None]
    if not ab: continue
    lo,hi=boot(ab)
    line=(f"{lab:26} n={len(ab):4d}  absolute {st.mean(ab):+.3f}% [{lo:+.3f},{hi:+.3f}]  "
          f"net {st.mean(ab)-COST:+.3f}%  win {100*sum(1 for x in ab if x>0)/len(ab):.1f}%")
    if arm:
        d=[r["rEOD"]-r[f"{arm}EOD"] for r in sub if r.get("rEOD") is not None]
        dlo,dhi=boot(d)
        line+=f"  | vs matched ctl {st.mean(d):+.3f}% [{dlo:+.3f},{dhi:+.3f}]"
    print(line)
print("\n  horizon profile (matched arm CMP2):")
Gc=[r for r in G if r.get("CMP2EOD") is not None]
for H in (5,15,30,60,120,"EOD"):
    P=[(r[f"r{H}"], r[f"CMP2{H}"]) for r in Gc if r.get(f"r{H}") is not None and r.get(f"CMP2{H}") is not None]
    if len(P)<15: continue
    d=[x-y for x,y in P]; lo,hi=boot(d)
    print(f"    H{str(H):>4}  n={len(P):3d}  sig {st.mean([x for x,_ in P]):+7.3f}%  "
          f"diff {st.mean(d):+7.3f}% [{lo:+.3f},{hi:+.3f}]  net-of-cost {st.mean([x for x,_ in P])-COST:+7.3f}%")

print()
print("="*98)
print("HOW MANY OF THE 20 ISOLATION CELLS WOULD BE SIGNIFICANT BY CHANCE?")
print("="*98)
print("  20 cells tested at alpha=0.05 -> expected false positives 1.0; observed significant cells: 1")
