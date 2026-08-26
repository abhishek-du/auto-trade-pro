"""Parts 9-14: BUG-2 impact, Hub-vs-tactical, forward-return diagnostic."""
import asyncio, json, sys, bisect, statistics as st, datetime as dt, random
from collections import defaultdict, Counter
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from p2core import boot_cluster, clustered
from sqlalchemy import text
from db.database import AsyncSessionLocal
random.seed(20260825)
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
COST_MIS = 0.2072
D=json.load(open(f"{SP}/p4_2026-08-25.json")); K=D["candidates"]; C=D["cycles"]
HOR=[5,15,30,60,120,None]; HL={5:"+5m",15:"+15m",30:"+30m",60:"+60m",120:"+120m",None:"EOD"}

async def main():
    syms = sorted({k["sym"] for k in K})
    async with AsyncSessionLocal() as db:
        pool=[r[0] for r in (await db.execute(text("""
            SELECT symbol FROM candles WHERE timeframe='1m' AND timestamp::date=DATE '2026-08-25'
            GROUP BY symbol HAVING COUNT(*)>250 AND SUM(volume*close)>2e8"""))).fetchall()]
        need=sorted(set(syms)|set(pool))
        bars=defaultdict(list)
        for i in range(0,len(need),300):
            for s,t,c in (await db.execute(text("""
                SELECT symbol,timestamp,close FROM candles WHERE timeframe='1m'
                AND timestamp::date=DATE '2026-08-25' AND symbol = ANY(:s)
                ORDER BY symbol,timestamp"""),{"s":need[i:i+300]})).fetchall():
                bars[s].append((t,float(c)))
        tac=(await db.execute(text("""
            SELECT symbol, strategy, signal_type, created_at, routing_outcome
            FROM tactical_signals WHERE created_at::date=DATE '2026-08-25'"""))).fetchall()
        allrows=(await db.execute(text("""
            SELECT symbol, bar_time, master_score, signal FROM master_intelligence_scores
            WHERE scored_at::date=DATE '2026-08-25' AND master_score IS NOT NULL
              AND symbol LIKE '%.NS' AND bar_time IS NOT NULL"""))).fetchall()
    times={k:[b[0] for b in v] for k,v in bars.items()}
    def fwd(sym,ts,h,is_long=True):
        t=times.get(sym)
        if not t: return None
        i=bisect.bisect_right(t,ts)-1
        if i<0 or i+1>=len(bars[sym]): return None
        ep=bars[sym][i][1]
        if ep<=0: return None
        j=min(i+h,len(bars[sym])-1) if h is not None else len(bars[sym])-1
        if j<=i: return None
        px=bars[sym][j][1]
        return ((px-ep) if is_long else (ep-px))/ep*100

    print("="*104); print("PART 9 — BUG-2 IMPACT (measured, not fixed)"); print("="*104)
    print(f"  expected india_trade_loop cycles inside 09:15-15:30 IST at 60s : ~375")
    print(f"  observed (logs/celery-worker.log 'Starting cycle')             :   11")
    print(f"  missing                                                        :  ~364  (97%)")
    print(f"\n  cause attribution for the 329-minute gap 09:13:21 -> 14:41:58 IST:")
    print(f"    beat task expired          : EVIDENCE NOT AVAILABLE — expired tasks log nothing")
    print(f"    worker unavailable         : RULED OUT — systemd shows active, NRestarts=0")
    print(f"    queued behind Master work  : STRONGLY SUPPORTED — the worker log during the gap")
    print(f"                                 is continuous engine.indicators + Keras model loads")
    print(f"                                 (ForkPoolWorker-3), i.e. the Hub scoring 1,663 symbols")
    print(f"    process blocked            : RULED OUT — other tasks logged throughout")
    print(f"    task exception             : RULED OUT for the gap — the UnboundLocalError")
    print(f"                                 traces start 15:00:21, after the gap ended")
    print(f"    unknown                    : the precise mechanism (expiry vs starvation) is")
    print(f"                                 NOT PROVEN; celery does not log a dropped task")

    print()
    print("="*104); print("PART 10 — HUB SHADOW vs TACTICAL (same session, not merged)"); print("="*104)
    tsyms={r[0] for r in tac}; ksyms={k["sym"] for k in K}
    print(f"  {'':<28} {'Hub shadow':>14} {'tactical':>14}")
    print(f"  {'candidate/signal count':<28} {len(K):>14,} {len(tac):>14,}")
    print(f"  {'unique symbols':<28} {len(ksyms):>14} {len(tsyms):>14}")
    print(f"  {'overlap (symbols in both)':<28} {len(ksyms & tsyms):>14} {'':>14}")
    print(f"  {'direction BUY':<28} {sum(1 for k in K if k['action']=='BUY'):>14,} "
          f"{sum(1 for r in tac if (r[2] or '')=='BUY'):>14,}")
    print(f"  {'direction SELL':<28} {sum(1 for k in K if k['action']=='SELL'):>14,} "
          f"{sum(1 for r in tac if (r[2] or '')=='SELL'):>14,}")
    kc=Counter(k["sym"] for k in K); tc=Counter(r[0] for r in tac)
    print(f"  {'top-10 concentration':<28} {100*sum(v for _,v in kc.most_common(10))/len(K):13.1f}% "
          f"{100*sum(v for _,v in tc.most_common(10))/len(tac):13.1f}%")
    print(f"\n  overlapping symbols: {sorted(ksyms & tsyms)}")
    print(f"\n  -> The two populations are {'DIFFERENT' if len(ksyms & tsyms) < 0.25*min(len(ksyms),len(tsyms)) else 'OVERLAPPING'}: "
          f"{len(ksyms & tsyms)} of {len(ksyms)} Hub symbols also produced a tactical signal.")

    print()
    print("="*104); print("PARTS 12-14 — FORWARD-RETURN DIAGNOSTIC (long, 1m candles, symbol-clustered)")
    print(f"  cost basis: MIS {COST_MIS}% round-trip (Phase 2). This is a DIAGNOSTIC, not an edge claim.")
    print("="*104)
    # populations
    pops={}
    pops["all Hub rows"]=[(r[0], r[1], True) for r in allrows]
    pops["STRONG_BUY"]=[(r[0], r[1], True) for r in allrows if r[3]=="STRONG_BUY"]
    pops["BUY"]=[(r[0], r[1], True) for r in allrows if r[3]=="BUY"]
    pops["shadow candidates"]=[(k["sym"], dt.datetime.fromisoformat(k["t"]), k["action"]=="BUY") for k in K]
    # first entry per symbol only — 5,160 cycle-repeats are not 5,160 opportunities
    seen=set(); first=[]
    for k in sorted(K, key=lambda z:z["t"]):
        if k["sym"] in seen: continue
        seen.add(k["sym"]); first.append((k["sym"], dt.datetime.fromisoformat(k["t"]), k["action"]=="BUY"))
    pops["shadow, first entry only"]=first
    # matched control: for each candidate, 5 random liquid symbols at the same instant
    print(f"  {'population':<28} {'n':>7} {'sym':>5} " + " ".join(f"{HL[h]:>9}" for h in HOR) + f" {'net EOD':>9}")
    store={}
    for lab,P in pops.items():
        row=[]; eod=[]
        for h in HOR:
            v=[(s,fwd(s,t,h,l)) for s,t,l in P]
            v=[(s,x) for s,x in v if x is not None]
            if len(v)<20: row.append(None); continue
            row.append(st.mean([x for _,x in v]))
            if h is None: eod=v
        if not eod: continue
        store[lab]=eod
        lo,hi=boot_cluster(clustered(eod))
        cells=" ".join((f"{c:+9.3f}" if c is not None else f"{'n/a':>9}") for c in row)
        print(f"  {lab:<28} {len(eod):>7,} {len(clustered(eod)):>5} {cells} {st.mean([x for _,x in eod])-COST_MIS:+9.3f}")
        print(f"  {'':<28} {'':>7} {'':>5} 95% CI on EOD: [{lo:+.3f}, {hi:+.3f}]")
    # matched control for the shadow candidates
    print(f"\n  matched control (5 random liquid symbols per candidate, same instant):")
    diffs=[]
    for s,t,l in pops["shadow candidates"]:
        x=fwd(s,t,None,l)
        if x is None: continue
        cs=random.sample(pool,5)
        m=[fwd(c,t,None,l) for c in cs]; m=[z for z in m if z is not None]
        if not m: continue
        diffs.append((s, x-sum(m)/len(m)))
    if len(diffs)>=20:
        lo,hi=boot_cluster(clustered(diffs))
        print(f"    shadow candidate minus control, EOD: {st.mean([v for _,v in diffs]):+.3f}pp  "
              f"[{lo:+.3f}, {hi:+.3f}]  n={len(diffs):,}  symbols={len(clustered(diffs))}")
    print(f"\n  PART 14 — does candidate FILTERING create separation?")
    if "all Hub rows" in store and "shadow candidates" in store:
        a=st.mean([x for _,x in store["all Hub rows"]]); b=st.mean([x for _,x in store["shadow candidates"]])
        print(f"    all Hub rows      EOD {a:+.3f}%")
        print(f"    shadow candidates EOD {b:+.3f}%")
        print(f"    difference            {b-a:+.3f}pp")
asyncio.run(main())
