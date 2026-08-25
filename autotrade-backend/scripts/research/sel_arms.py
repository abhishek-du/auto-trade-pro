import json, random, statistics as st
from collections import defaultdict
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=json.load(open(f"{SP}/matched.json"))
HOR=[5,15,30,60,120,"EOD"]; ARMS=("MOM","VOL","LIQ","CMP","CMP2","CMP3")

def boot(x, n=4000):
    if len(x)<8: return (None,None)
    m=[]
    for _ in range(n):
        m.append(sum(random.choices(x,k=len(x)))/len(x))
    m.sort(); return (m[int(.025*n)], m[int(.975*n)])

def smd(a,b):
    """Standardised mean difference — match quality. |SMD|<0.10 = good balance."""
    if len(a)<3 or len(b)<3: return None
    sa,sb=st.pstdev(a),st.pstdev(b)
    p=((sa**2+sb**2)/2)**0.5
    return (st.mean(a)-st.mean(b))/p if p>0 else 0.0

print("="*104)
print("MATCH QUALITY — standardised mean difference, signal vs its controls (|SMD| < 0.10 = balanced)")
print("="*104)
print(f"{'arm':5} {'n':>5} {'ctrl/sig':>8} | {'SMD mom':>8} {'SMD vol':>8} {'SMD liq':>8} | {'sig mom%':>9} {'ctl mom%':>9} {'sig vol%':>9} {'ctl vol%':>9}")
for a in ARMS:
    R=[r for r in D if r.get(f"{a}_mom") is not None]
    if not R: continue
    sm=[r["mom"] for r in R]; cm=[r[f"{a}_mom"] for r in R]
    sv=[r["vol"] for r in R]; cv=[r[f"{a}_vol"] for r in R]
    sl=[r["liq"] for r in R]; cl=[r[f"{a}_liq"] for r in R]
    nb=st.mean([r[f"n_{a}"] for r in R])
    def f(x): return f"{x:+.3f}" if x is not None else "   n/a"
    print(f"{a:5} {len(R):5d} {nb:8.2f} | {f(smd(sm,cm)):>8} {f(smd(sv,cv)):>8} {f(smd(sl,cl)):>8} | "
          f"{st.mean(sm):+9.3f} {st.mean(cm):+9.3f} {st.mean(sv):9.3f} {st.mean(cv):9.3f}")

print()
print("="*104)
print("PAIRED TEST — signal forward return minus its matched controls', same direction, same T")
print("="*104)
res={}
for a in ARMS:
    print(f"\n--- arm {a} ---")
    print(f"{chr(72):>5} {chr(110):>5} {'signal%':>9} {'control%':>9} {'diff%':>9} {'95% CI':>22} {'sig win%':>9} {'ctl win%':>9}  verdict")
    for H in HOR:
        P=[(r[f"r{H}"], r[f"{a}{H}"]) for r in D
           if r.get(f"r{H}") is not None and r.get(f"{a}{H}") is not None]
        if len(P)<20: continue
        d=[x-y for x,y in P]
        lo,hi=boot(d)
        sw=100*sum(1 for x,y in P if x>0)/len(P)
        cw=100*sum(1 for x,y in P if y>0)/len(P)
        v="SIG>CTL" if lo is not None and lo>0 else ("SIG<CTL" if hi is not None and hi<0 else "inconclusive")
        res[(a,H)]=(len(P), st.mean(d), lo, hi)
        print(f"{str(H):>5} {len(P):5d} {st.mean([x for x,_ in P]):+9.3f} {st.mean([y for _,y in P]):+9.3f} "
              f"{st.mean(d):+9.3f} {('['+format(lo,'+.3f')+', '+format(hi,'+.3f')+']') if lo is not None else '':>22} "
              f"{sw:9.1f} {cw:9.1f}  {v}")
json.dump({f"{a}|{H}":v for (a,H),v in res.items()}, open(f"{SP}/marm.json","w"))
