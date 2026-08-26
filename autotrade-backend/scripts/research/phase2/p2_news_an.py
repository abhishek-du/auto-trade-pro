import json, sys, re, statistics as st
from collections import defaultdict, Counter
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import boot_cluster, clustered, COST_RT
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
D=[json.loads(l) for l in open(f"{SP}/news_obs.jsonl")]
H=["5","15","30","60","None"]; HL={"5":"+5m","15":"+15m","30":"+30m","60":"+60m","None":"EOD"}
S=sorted({r["d"] for r in D})
print("="*116)
print(f"PARTS 9-12 — NEWS   n={len(D)}  sessions={len(S)} ({S[0]} .. {S[-1]})  symbols={len({r['sym'] for r in D})}")
print("="*116)
print("\nPART 7/9 — ARRIVAL BUCKETS (never pooled)")
print("  A PRE-OPEN     : 0 observations in causal_events — these are RSS-derived events created")
print("                   by the running engine, so their timestamps fall inside the session.")
print("                   EVIDENCE NOT AVAILABLE for a pre-open bucket from this table.")
print("  C IN-SESSION   : the bucket actually populated below.")
print("  B POST-CLOSE   : 595 event-tickers, but scoring them needs the NEXT session's open;")
print("                   not measured here -> EVIDENCE NOT AVAILABLE.")
print("\n  NOTE: NSE corporate announcements are absent in-session for the whole window")
print("  (Phase 1B, CONFIRMED). This test therefore covers RSS/news-derived causal events only,")
print("  NOT exchange filings, and must not be read as a complete intraday-news test.")

def rep(lab, R, h, indent="  ", minn=60):
    v=[(r["sym"], r["f"][h]) for r in R if r["f"].get(h) is not None]
    if len(v)<minn:
        print(f"{indent}{lab:<28} n={len(v):>5}  INSUFFICIENT SAMPLE"); return None
    lo,hi=boot_cluster(clustered(v)); vals=[x for _,x in v]
    # vs market baseline, paired
    c=[(r["sym"], r["f"][h]-r["mkt"][h]) for r in R if r["f"].get(h) is not None and r.get("mkt",{}).get(h) is not None]
    cs=""
    if len(c)>=minn:
        clo,chi=boot_cluster(clustered(c))
        cs=f"  vs-mkt {st.mean([x for _,x in c]):+7.3f} [{clo:+.3f},{chi:+.3f}]"
    print(f"{indent}{lab:<28} n={len(vals):>5} sym={len(clustered(v)):>4}  gross {st.mean(vals):+7.3f}  "
          f"net {st.mean(vals)-COST_RT:+7.3f}  win {100*sum(1 for x in vals if x>0)/len(vals):5.1f}%  "
          f"[{lo:+.3f},{hi:+.3f}]{cs}")
    return st.mean(vals)

C=[r for r in D if r["bucket"]=="C_IN_SESSION"]
print(f"\nPART 10 — DIRECTION (event's own tag; return measured in that direction)  n={len(C)}")
for h in H: rep(HL[h], C, h)
print("\n  by tagged direction:")
for dirn in ("LONG","SHORT"):
    print(f"   --- {dirn} ---")
    for h in ["30","None"]: rep(HL[h], [r for r in C if r["dirn"]==dirn], h, indent="     ")

print("\n  SEPARATION: long-tagged minus short-tagged, both measured LONG (raw market move)")
for h in H:
    L=[(r["sym"], r["f"][h]) for r in C if r["dirn"]=="LONG" and r["f"].get(h) is not None]
    Sh=[(r["sym"], -r["f"][h]) for r in C if r["dirn"]=="SHORT" and r["f"].get(h) is not None]
    if len(L)<60 or len(Sh)<60: continue
    llo,lhi=boot_cluster(clustered(L)); slo,shi=boot_cluster(clustered(Sh))
    sep=st.mean([x for _,x in L])-st.mean([x for _,x in Sh])
    print(f"   {HL[h]:>5}  long-tagged {st.mean([x for _,x in L]):+7.3f} [{llo:+.3f},{lhi:+.3f}]   "
          f"short-tagged(raw) {st.mean([x for _,x in Sh]):+7.3f} [{slo:+.3f},{shi:+.3f}]   separation {sep:+7.3f}pp")

print(f"\nPART 11 — EVENT-TYPE FAMILIES (predefined, not mined)")
FAM=[("earnings",r"result|earning|quarter|q[1-4]|profit|revenue"),
     ("order/contract",r"order|contract|win|award|bag"),
     ("management",r"resign|appoint|director|kmp|ceo|cfo|md\b"),
     ("regulatory",r"sebi|rbi|regulat|penalt|notice|compliance"),
     ("corporate action",r"dividend|bonus|split|buyback|rights"),
     ("rating",r"rating|upgrade|downgrade|outlook"),
     ("M&A",r"acquisit|merger|stake|takeover|amalgam"),
     ("capacity",r"expansion|capacity|plant|commission"),
     ("fundraising",r"fund rais|qip|preferential|debenture|ncd")]
for h in ["30","None"]:
    print(f"\n   --- horizon {HL[h]} ---")
    used=set()
    for lab,rx in FAM:
        R=[r for r in C if re.search(rx, f"{r.get('cat') or ''} {r.get('src') or ''}", re.I)]
        rep(lab, R, h, indent="   ")
        used |= {id(r) for r in R}
    rep("other/unmatched", [r for r in C if id(r) not in used], h, indent="   ")

print(f"\nPART 12 — DOES THE LLM ADD INFORMATION BEYOND THE EVENT?")
print("   The LLM verdict is NOT used to select which events are scored — every event with")
print("   forward data is scored, then split by what the agent happened to decide.")
for h in ["30","None"]:
    print(f"\n   --- horizon {HL[h]} ---")
    rep("EVENT ONLY (all events)", C, h, indent="   ")
    rep("  agent SKIPped it", [r for r in C if r["act"]=="SKIP"], h, indent="   ")
    rep("  agent said BUY/SELL", [r for r in C if r["act"] in ("BUY","SELL")], h, indent="   ")
    rep("  agent never saw it", [r for r in C if r["act"] is None], h, indent="   ")
    for lo_,hi_ in ((70,101),(60,70),(0,60)):
        rep(f"  SKIP, conf {lo_}-{hi_-1}", [r for r in C if r["act"]=="SKIP" and r["dconf"] is not None and lo_<=r["dconf"]<hi_], h, indent="   ")

print(f"\nPART 15 — SESSION ROBUSTNESS (in-session events, EOD)")
signs=[]
for d in S:
    m=rep(d, [r for r in C if r["d"]==d], "None", indent="   ", minn=40)
    if m is not None: signs.append("+" if m>0 else "-")
print(f"\n   session sign pattern: {''.join(signs)}  positive {signs.count('+')}/{len(signs)}")
