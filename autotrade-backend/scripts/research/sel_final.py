import json, random, statistics as st, datetime as dt
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=json.load(open(f"{SP}/matched.json"))
def boot(x,n=6000):
    if len(x)<8: return (None,None)
    m=sorted(sum(random.choices(x,k=len(x)))/len(x) for _ in range(n))
    return (m[int(.025*n)], m[int(.975*n)])
def pval(x,n=6000):
    """Two-sided permutation p: sign-flip the paired differences under H0 of no effect."""
    if len(x)<8: return None
    obs=abs(st.mean(x)); c=0
    for _ in range(n):
        c += abs(st.mean([v if random.random()<.5 else -v for v in x])) >= obs
    return (c+1)/(n+1)

E=[r for r in D if r.get("CMP2EOD") is not None]     # a close match existed
N=[r for r in D if r.get("CMP2EOD") is None]         # no close match existed
print("="*100)
print("WHERE THE EFFECT LIVES — signals that HAVE a close match vs signals that do NOT")
print("="*100)
print(f"{'group':28} {'n':>5} {'|mom|%':>8} {'vol%':>7} {'liq ₹cr':>9} {'sig EOD%':>9} {'LIQ-arm diff%':>14}")
for lab,G in (("close match exists (CMP2)",E),("no close match exists",N)):
    dl=[r["rEOD"]-r["LIQEOD"] for r in G if r.get("rEOD") is not None and r.get("LIQEOD") is not None]
    lo,hi=boot(dl)
    print(f"{lab:28} {len(G):5d} {st.mean([abs(r['mom']) for r in G]):8.3f} {st.mean([r['vol'] for r in G]):7.3f} "
          f"{st.mean([r['liq'] for r in G])/1e7:9.1f} {st.mean([r['rEOD'] for r in G if r.get('rEOD') is not None]):+9.3f} "
          f"{st.mean(dl):+8.3f} [{lo:+.2f},{hi:+.2f}]")

print()
print("="*100)
print("ISOLATION ACROSS HORIZONS — identical 505 signals, control definition varied")
print("="*100)
S=[r for r in D if all(r.get(f"{a}EOD") is not None for a in ("MOM","VOL","LIQ","CMP","CMP2"))]
print(f"{'arm':6} " + " ".join(f"{('H'+str(h)):>16}" for h in (5,30,120,"EOD")))
for a in ("MOM","VOL","LIQ","CMP","CMP2"):
    cells=[]
    for H in (5,30,120,"EOD"):
        d=[r[f"r{H}"]-r[f"{a}{H}"] for r in S if r.get(f"r{H}") is not None and r.get(f"{a}{H}") is not None]
        lo,hi=boot(d)
        mark="*" if (lo is not None and (lo>0 or hi<0)) else " "
        cells.append(f"{st.mean(d):+7.3f}{mark}{'':>8}" if lo is None else f"{st.mean(d):+7.3f}{mark} n={len(d):<4}")
    print(f"{a:6} " + " ".join(f"{c:>16}" for c in cells))
print("  (* = 95% CI excludes zero)")

print()
print("="*100)
print("MULTIPLICITY — 10 rule tests were run on the balanced arm; how many survive correction?")
print("="*100)
C=[r for r in D if r.get("CMP2EOD") is not None]
rows=[]
for s_ in sorted({r["strat"] for r in C}):
    G=[r for r in C if r["strat"]==s_ and r.get("rEOD") is not None]
    if len(G)<15: continue
    d=[r["rEOD"]-r["CMP2EOD"] for r in G]
    rows.append((s_, len(d), st.mean(d), pval(d)))
rows.sort(key=lambda r:r[3])
m=len(rows)
print(f"{'rule':20} {'n':>5} {'diff%':>8} {'raw p':>8} {'Bonf. α':>9} {'Holm α':>8}  survives?")
for i,(s_,n,mn,p) in enumerate(rows):
    holm=0.05/(m-i)
    surv = "YES" if p<holm else "no"
    print(f"{s_:20} {n:5d} {mn:+8.3f} {p:8.4f} {0.05/m:9.4f} {holm:8.4f}  {surv}")
print(f"\n{m} tests · expected false positives at raw α=0.05: {0.05*m:.1f}")

print()
print("="*100)
print("TIME OF DAY (IST — the stored timestamp is naive UTC, +5:30 applied)")
print("="*100)
def ist(r):
    h,mi=map(int,r["tod"].split(":"))
    t=(h*60+mi+330)%1440
    return f"{t//60:02d}:{t%60:02d}"
for lab,a,b in (("09:15-10:30","09:15","10:30"),("10:30-12:00","10:30","12:00"),
                ("12:00-14:00","12:00","14:00"),("14:00-15:30","14:00","15:30")):
    G=[r for r in C if a<=ist(r)<b and r.get("rEOD") is not None]
    if len(G)<15:
        print(f"{lab:14} n={len(G):4d}  INSUFFICIENT — EVIDENCE NOT AVAILABLE"); continue
    d=[r["rEOD"]-r["CMP2EOD"] for r in G]; lo,hi=boot(d)
    print(f"{lab:14} n={len(G):4d}  sig {st.mean([r['rEOD'] for r in G]):+7.3f}  diff {st.mean(d):+7.3f}  "
          f"[{lo:+.3f}, {hi:+.3f}]  {'SIG>CTL' if lo>0 else ('SIG<CTL' if hi<0 else 'inconclusive')}")

print()
print("="*100)
print("PLACEBO — replace each signal with a random symbol; the pipeline must show nothing")
print("="*100)
for a in ("LIQ","CMP2"):
    d=[]
    for r in C:
        if r.get(f"{a}EOD") is None: continue
        d.append(r[f"{a}EOD"] - r[f"{a}EOD"])       # control vs itself = exact zero by construction
    # real placebo: control-vs-control across arms on the same signal
    d2=[r["LIQEOD"]-r["CMP2EOD"] for r in C if r.get("LIQEOD") is not None and r.get("CMP2EOD") is not None]
    lo,hi=boot(d2)
    print(f"control(LIQ) vs control(CMP2), no signal involved: {st.mean(d2):+.3f} [{lo:+.3f}, {hi:+.3f}]  "
          f"{'CONTAMINATED — control choice alone moves the number' if (lo>0 or hi<0) else 'clean'}")
    break
