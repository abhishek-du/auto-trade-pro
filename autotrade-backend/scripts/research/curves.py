import json, statistics as st, datetime as dt
from collections import defaultdict, Counter
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
R=json.load(open(f"{SP}/react.json"))
H=[1,3,5,10,15,30,60]
def q(v,p):
    v=sorted(v); 
    if not v: return float('nan')
    i=max(0,min(len(v)-1,int(round(p*(len(v)-1))))); return v[i]
def line(vals, label, width=30):
    if not vals: return f"  {label:<{width}} n=0"
    return (f"  {label:<{width}}{len(vals):>6}{st.median(vals):>9.3f}{sum(vals)/len(vals):>9.3f}"
            f"{len([x for x in vals if x>0])/len(vals)*100:>8.1f}%"
            f"{q(vals,.10):>9.2f}{q(vals,.25):>8.2f}{q(vals,.75):>8.2f}{q(vals,.90):>8.2f}")
HDR=f"  {'':<30}{'n':>6}{'median':>9}{'mean':>9}{'win%':>9}{'p10':>9}{'p25':>8}{'p75':>8}{'p90':>8}"

print("="*112)
print("RAW EVENT REACTION — signed by the event's own direction (a SHORT that falls is positive)")
print("="*112); print(HDR)
for h in H:
    v=[r[f"r{h}"] for r in R if r.get(f"r{h}") is not None]
    print(line(v, f"+{h}m"))
v=[r["rEOD"] for r in R if r.get("rEOD") is not None]; print(line(v,"EOD"))

print()
print("="*112)
print("BENCHMARK-ADJUSTED  (stock − NIFTYBEES.NS over the identical window)")
print("  NIFTYBEES is a PROXY for NIFTY 50: an ETF, so it carries tracking error,")
print("  its own bid-ask, and can trade at a premium/discount to the index.")
print("="*112); print(HDR)
for h in H:
    v=[r[f"r{h}"]-r[f"b{h}"] for r in R if r.get(f"r{h}") is not None and r.get(f"b{h}") is not None]
    print(line(v, f"+{h}m excess"))
v=[r["rEOD"]-r["bEOD"] for r in R if r.get("rEOD") is not None and r.get("bEOD") is not None]
print(line(v,"EOD excess"))

print()
print("="*112)
print("EXCURSIONS (rest of session, in the event's direction)")
print("="*112)
mfe=[r["mfe"] for r in R]; mae=[r["mae"] for r in R]
tm=[r["t_mfe"] for r in R]; ta=[r["t_mae"] for r in R]
print(HDR)
print(line(mfe,"MFE %")); print(line(mae,"MAE %"))
print(f"\n  time-to-MFE  median {st.median(tm):>6.0f} min   p25 {q(tm,.25):>5.0f}   p75 {q(tm,.75):>5.0f}")
print(f"  time-to-MAE  median {st.median(ta):>6.0f} min   p25 {q(ta,.25):>5.0f}   p75 {q(ta,.75):>5.0f}")

print()
print("="*112); print("BY DIRECTION"); print("="*112); print(HDR)
for side in ("LONG","SHORT"):
    for h in (5,15,60):
        v=[r[f"r{h}"] for r in R if r["side"]==side and r.get(f"r{h}") is not None]
        print(line(v, f"{side} +{h}m raw"))
    v=[r["rEOD"] for r in R if r["side"]==side and r.get("rEOD") is not None]
    print(line(v, f"{side} EOD raw"))
    v=[r["rEOD"]-r["bEOD"] for r in R if r["side"]==side and r.get("rEOD") is not None and r.get("bEOD") is not None]
    print(line(v, f"{side} EOD excess")); print()

print("="*112); print("BY IMPORTANCE TIER"); print("="*112); print(HDR)
tiers=[("Tier1 imp>=85",lambda x:x is not None and x>=85),("Tier2 70-84",lambda x:x is not None and 70<=x<85),
       ("Tier3 40-69",lambda x:x is not None and 40<=x<70),("Tier4 <40",lambda x:x is not None and x<40)]
for name,f in tiers:
    for h in (15,60):
        v=[r[f"r{h}"] for r in R if f(r.get("imp")) and r.get(f"r{h}") is not None]
        print(line(v, f"{name} +{h}m"))
    v=[r["rEOD"]-r["bEOD"] for r in R if f(r.get("imp")) and r.get("rEOD") is not None and r.get("bEOD") is not None]
    print(line(v, f"{name} EOD excess")); print()

print("="*112); print("BY EVENT CATEGORY (>=25 observations)"); print("="*112); print(HDR)
byc=defaultdict(list)
for r in R: byc[r["title"]].append(r)
for t,rows in sorted(byc.items(), key=lambda kv:-len(kv[1])):
    if len(rows)<25: continue
    v=[x["rEOD"]-x["bEOD"] for x in rows if x.get("rEOD") is not None and x.get("bEOD") is not None]
    print(line(v, f"{t[:28]} EOD excess"))
