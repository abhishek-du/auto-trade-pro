import json, statistics as st, random
from collections import defaultdict, Counter
random.seed(7)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
B=json.load(open(f"{SP}/gtB.json")); A=json.load(open(f"{SP}/gtA.json"))
def boot(v, n=2000):
    if len(v)<8: return (float('nan'),float('nan'))
    ms=[]
    for _ in range(n):
        s=[v[random.randrange(len(v))] for _ in range(len(v))]
        ms.append(sum(s)/len(s))
    ms.sort(); return (ms[int(n*.025)], ms[int(n*.975)])
def row(v,label,w=30):
    if len(v)<3: return f"  {label:<{w}}{len(v):>6}  (too few)"
    lo,hi=boot(v)
    return (f"  {label:<{w}}{len(v):>6}{st.median(v):>9.3f}{sum(v)/len(v):>9.3f}"
            f"{len([x for x in v if x>0])/len(v)*100:>8.1f}%{st.pstdev(v):>8.2f}"
            f"   [{lo:+.3f}, {hi:+.3f}]")
HDR=f"  {'':<30}{'n':>6}{'median':>9}{'mean':>9}{'win%':>9}{'sd':>8}   95% CI of mean"

print("="*104)
print("STUDY B — OVERNIGHT  (NSE announcement after close -> next session)")
print("  Benchmark: NIFTYBEES.NS (ETF PROXY for NIFTY 50 — carries tracking error and its own spread)")
print("="*104)
for fld,lab in (("xgap","GAP (prev close -> open), excess"),
                ("xday","DAY (open -> close), excess"),
                ("xtotal","TOTAL (prev close -> close), excess")):
    print(f"\n{lab}"); print(HDR)
    for side in ("LONG","SHORT","NEUTRAL"):
        v=[b[fld] for b in B if b["side"]==side and b.get(fld) is not None]
        print(row(v, side))

print("\n" + "="*104)
print("CONTROL TEST — directional events vs NEUTRAL announcements (same source, same window)")
print("="*104)
for fld,lab in (("xgap","excess gap"),("xtotal","excess total")):
    neu=[b[fld] for b in B if b["side"]=="NEUTRAL" and b.get(fld) is not None]
    nm=sum(neu)/len(neu)
    for side in ("LONG","SHORT"):
        v=[b[fld] for b in B if b["side"]==side and b.get(fld) is not None]
        d=[x-nm for x in v]; lo,hi=boot(d)
        sig = "CI excludes 0" if (lo>0 or hi<0) else "CI includes 0 — not distinguishable"
        print(f"  {side:<8}{lab:<16} mean {sum(v)/len(v):+.3f}  vs neutral {nm:+.3f}"
              f"   diff {sum(d)/len(d):+.3f}  95%CI [{lo:+.3f},{hi:+.3f}]  -> {sig}")

print("\n" + "="*104)
print("ABNORMAL MOVEMENT (direction-free): did an announcement cause a bigger move at all?")
print("="*104)
print(f"  {'group':<30}{'n':>6}{'median |gap|':>14}{'mean |gap|':>12}{'p90':>9}")
for side in ("LONG","SHORT","NEUTRAL"):
    v=[b["absgap"] for b in B if b["side"]==side and b.get("absgap") is not None]
    if v:
        vs=sorted(v)
        print(f"  {side:<30}{len(v):>6}{st.median(v):>14.3f}{sum(v)/len(v):>12.3f}{vs[int(len(vs)*.9)]:>9.2f}")

print("\n" + "="*104)
print("BY SUBCATEGORY — excess TOTAL (prev close -> close), n>=20")
print("="*104); print(HDR)
by=defaultdict(list)
for b in B:
    if b.get("xtotal") is not None: by[(b["side"],b["sub"])].append(b["xtotal"])
for (side,sub),v in sorted(by.items(), key=lambda kv:-len(kv[1])):
    if len(v)<20 or side=="NEUTRAL": continue
    print(row(v, f"{side} · {sub[:22]}"))
print("  -- neutral categories (controls) --")
for (side,sub),v in sorted(by.items(), key=lambda kv:-len(kv[1])):
    if len(v)<20 or side!="NEUTRAL": continue
    print(row(v, f"NEUTRAL · {sub[:20]}"))

print("\n" + "="*104)
print("BY MATERIALITY TIER — excess TOTAL, directional events only")
print("="*104); print(HDR)
for t in (1,2,3,4):
    v=[b["xtotal"] for b in B if b["tier"]==t and b["side"]!="NEUTRAL" and b.get("xtotal") is not None]
    print(row(v, f"Tier {t}"))

print("\n" + "="*104)
print("INFORMATION DECAY — where does the overnight move happen?")
print("="*104)
for side in ("LONG","SHORT"):
    g=[b["xgap"] for b in B if b["side"]==side and b.get("xgap") is not None]
    d=[b["xday"] for b in B if b["side"]==side and b.get("xday") is not None]
    t=[b["xtotal"] for b in B if b["side"]==side and b.get("xtotal") is not None]
    if g and d and t:
        gm=sum(g)/len(g); dm=sum(d)/len(d); tm=sum(t)/len(t)
        print(f"  {side:<7} gap {gm:+.3f}   intraday-after-open {dm:+.3f}   total {tm:+.3f}"
              f"   -> gap is {gm/tm*100 if tm else 0:.0f}% of total")

print("\n" + "="*104)
print("STUDY A — INTRADAY 1m reaction   *** n=21 LONG / 21 SHORT — UNDERPOWERED ***")
print("="*104); print(HDR)
for side in ("LONG","SHORT","NEUTRAL"):
    for h in (5,15,60):
        v=[a[f"x{h}"] for a in A if a["side"]==side and a.get(f"x{h}") is not None]
        if v: print(row(v, f"{side} +{h}m excess"))
    v=[a["xEOD"] for a in A if a["side"]==side and a.get("xEOD") is not None]
    if v: print(row(v, f"{side} EOD excess"))
    print()
print("  pre-event drift (directional events only):")
for W in (5,15,30):
    v=[a[f"pre{W}"] for a in A if a["side"]!="NEUTRAL" and a.get(f"pre{W}") is not None]
    if v: print(row(v, f"  -{W}m -> T_public"))
