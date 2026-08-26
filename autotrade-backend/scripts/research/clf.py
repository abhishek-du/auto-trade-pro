"""Directional classifier validation, benchmark-adjusted, 22 sessions.

'Correct' = the stock's EXCESS return over NIFTYBEES moved the way the tag said.
Base rate = share of these same observations whose excess return was positive,
which is what a coin flip that always said LONG would achieve.
"""
import json, statistics as st
from collections import Counter, defaultdict
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
R=[r for r in json.load(open(f"{SP}/react.json")) if r.get("rEOD") is not None and r.get("bEOD") is not None]
for H,label in ((15,"+15m"),(60,"+60m"),(None,"EOD")):
    if H is None: get=lambda r: r["rEOD"]-r["bEOD"]
    else: get=lambda r,h=H: (r[f"r{h}"]-r[f"b{h}"]) if r.get(f"r{h}") is not None and r.get(f"b{h}") is not None else None
    obs=[(r["side"], get(r)) for r in R if get(r) is not None]
    tp=sum(1 for s,x in obs if s=="LONG"  and x>0)   # predicted up, went up
    fp=sum(1 for s,x in obs if s=="LONG"  and x<=0)
    tn=sum(1 for s,x in obs if s=="SHORT" and x<0)   # predicted down, went down
    fn=sum(1 for s,x in obs if s=="SHORT" and x>=0)
    n=len(obs)
    acc=(tp+tn)/n*100
    rec_up=tp/max(tp+fn,1)*100          # of all that went up, how many did we call up
    prec_up=tp/max(tp+fp,1)*100
    prec_dn=tn/max(tn+fn,1)*100
    sens=tp/max(sum(1 for s,x in obs if s=="LONG"),1)*100
    spec=tn/max(sum(1 for s,x in obs if s=="SHORT"),1)*100
    bal=(sens+spec)/2
    base_up=len([1 for _,x in obs if x>0])/n*100
    f1=2*prec_up*sens/max(prec_up+sens,1e-9)
    print(f"=== {label}  (n={n}) ===")
    print(f"   confusion:   LONG->up {tp:>5}   LONG->down {fp:>5}")
    print(f"                SHORT->down {tn:>4}   SHORT->up {fn:>5}")
    print(f"   accuracy            {acc:>6.1f}%")
    print(f"   balanced accuracy   {bal:>6.1f}%   (LONG hit {sens:.1f}% · SHORT hit {spec:.1f}%)")
    print(f"   precision LONG      {prec_up:>6.1f}%   precision SHORT {prec_dn:.1f}%   F1(LONG) {f1:.1f}%")
    print(f"   BASE RATE (share of these obs with positive excess) {base_up:>5.1f}%")
    print(f"   -> always-LONG would score {base_up:.1f}%; classifier scores {acc:.1f}%  "
          f"({acc-base_up:+.1f} pts)")
    # economic expectancy, not just accuracy
    pay=[x if s=="LONG" else -x for s,x in obs]   # what a trade following the tag earns
    pay=[(x if s=="LONG" else -x) for s,x in obs]
    signed=[(x if s=="LONG" else -x) for s,x in obs]
    # NOTE: excess is already signed by direction upstream, so follow-the-tag payoff IS x
    follow=[x for _,x in obs]
    print(f"   economic: following the tag -> median {st.median(follow):+.4f}%  mean {sum(follow)/len(follow):+.4f}%")
    print(f"             INVERTING the tag -> median {-st.median(follow):+.4f}%  mean {-sum(follow)/len(follow):+.4f}%")
    print()
# high-confidence subset
print("=== does a HIGH-CONFIDENCE subset pay, even if overall accuracy is poor? ===")
print(f"  {'subset':<34}{'n':>6}{'median':>10}{'mean':>10}{'win%':>8}")
for lo,hi,lab in ((0.9,1.01,"confidence >= 0.90"),(0.8,0.9,"confidence 0.80-0.89"),
                  (0.0,0.8,"confidence < 0.80")):
    v=[r["rEOD"]-r["bEOD"] for r in R if r.get("conf") is not None and lo<=r["conf"]<hi]
    if v: print(f"  {lab:<34}{len(v):>6}{st.median(v):>10.4f}{sum(v)/len(v):>10.4f}{len([x for x in v if x>0])/len(v)*100:>7.1f}%")
for lo,lab in ((85,"importance>=85 AND conf>=0.85"),):
    v=[r["rEOD"]-r["bEOD"] for r in R if (r.get("imp") or 0)>=lo and (r.get("conf") or 0)>=0.85]
    if v: print(f"  {lab:<34}{len(v):>6}{st.median(v):>10.4f}{sum(v)/len(v):>10.4f}{len([x for x in v if x>0])/len(v)*100:>7.1f}%")
v=[r["rEOD"]-r["bEOD"] for r in R if r.get("linked_news")]
if v: print(f"  {'linked to a news_item':<34}{len(v):>6}{st.median(v):>10.4f}{sum(v)/len(v):>10.4f}{len([x for x in v if x>0])/len(v)*100:>7.1f}%")
