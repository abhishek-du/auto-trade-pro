"""Reaction curves. Stages S6-S8 (price validation) then the measurements.

Direction convention: every return is signed BY THE EVENT'S OWN DIRECTION, so a
SHORT event that falls produces a positive number. This measures "did the market
move the way the event said", not "did the price go up".
"""
import asyncio, json, warnings, datetime as dt, statistics as st
from collections import defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal

SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
EV=[dict(e, t_event=dt.datetime.fromisoformat(e["t_event"])) for e in json.load(open(f"{SP}/elig.json"))]
HORIZONS=[1,3,5,10,15,30,60]
BENCH="NIFTYBEES.NS"

async def load_bars(pairs):
    """pairs: set of (symbol, date). Returns {(sym,date): [(ts,o,h,l,c,v)...]}"""
    out=defaultdict(list); pl=sorted(pairs)
    async with AsyncSessionLocal() as s:
        for i in range(0,len(pl),150):
            chunk=pl[i:i+150]
            conds=" OR ".join(f"(symbol=:s{j} AND timestamp>=:a{j} AND timestamp<:b{j})" for j in range(len(chunk)))
            params={}
            for j,(sym,d) in enumerate(chunk):
                params[f"s{j}"]=sym
                params[f"a{j}"]=dt.datetime.combine(d,dt.time(0,0))
                params[f"b{j}"]=dt.datetime.combine(d,dt.time(23,59))
            rows=(await s.execute(text(
                f"SELECT symbol,timestamp,open,high,low,close,volume FROM candles "
                f"WHERE timeframe='1m' AND ({conds}) ORDER BY symbol,timestamp"), params)).fetchall()
            for sym,ts,o,h,l,c,v in rows:
                out[(sym,ts.date())].append((ts,float(o),float(h),float(l),float(c),float(v or 0)))
    return out

async def main():
    pairs={(e["symbol"], e["t_event"].date()) for e in EV}
    dates={d for _,d in pairs}
    print(f"loading 1m bars for {len(pairs)} symbol-days across {len(dates)} sessions...")
    bars=await load_bars(pairs)
    bench=await load_bars({(BENCH,d) for d in dates})
    print(f"  loaded {sum(len(v) for v in bars.values())} bars; benchmark days available: "
          f"{len([d for d in dates if bench.get((BENCH,d))])}/{len(dates)}\n")

    N0=len(EV); rows=[]; drops=defaultdict(list)
    for e in EV:
        key=(e["symbol"], e["t_event"].date()); bl=bars.get(key) or []
        if not bl: drops["S6 no 1m bars that session"].append(e); continue
        t0=e["t_event"]
        pre=[b for b in bl if b[0]<=t0]
        if not pre: drops["S6 no bar at/before T_event"].append(e); continue
        p0=pre[-1][4]
        if p0<=0: drops["S6 non-positive pre-event price"].append(e); continue
        post=[b for b in bl if b[0]>t0]
        if len(post)<5: drops["S7 <5 post-event bars"].append(e); continue
        bb=bench.get((BENCH, key[1])) or []
        bpre=[b for b in bb if b[0]<=t0]
        has_b = bool(bpre) and bpre[-1][4]>0
        if not has_b: drops["S8 no benchmark at T_event"].append(e)
        b0 = bpre[-1][4] if has_b else None
        sgn = 1.0 if e["side"]=="LONG" else -1.0

        r=dict(e); r["p0"]=p0; r["nbars"]=len(post)
        for h in HORIZONS:
            seg=[b for b in post if (b[0]-t0).total_seconds()<=h*60]
            if len(seg)==0: r[f"r{h}"]=None; r[f"b{h}"]=None; continue
            r[f"r{h}"]=sgn*(seg[-1][4]-p0)/p0*100
            if has_b:
                bseg=[b for b in bb if t0<b[0]<=t0+dt.timedelta(minutes=h)]
                r[f"b{h}"]=(sgn*(bseg[-1][4]-b0)/b0*100) if bseg else None
            else: r[f"b{h}"]=None
        r["rEOD"]=sgn*(post[-1][4]-p0)/p0*100
        if has_b:
            bpost=[b for b in bb if b[0]>t0]
            r["bEOD"]=sgn*(bpost[-1][4]-b0)/b0*100 if bpost else None
        else: r["bEOD"]=None
        # MFE / MAE in the event's own direction, over the rest of the session
        if sgn>0:
            fav=[( (b[2]-p0)/p0*100, b[0]) for b in post]
            adv=[( (b[3]-p0)/p0*100, b[0]) for b in post]
        else:
            fav=[( (p0-b[3])/p0*100, b[0]) for b in post]
            adv=[( (p0-b[2])/p0*100, b[0]) for b in post]
        mfe,tm=max(fav); mae,ta=min(adv)
        r["mfe"]=mfe; r["mae"]=mae
        r["t_mfe"]=(tm-t0).total_seconds()/60; r["t_mae"]=(ta-t0).total_seconds()/60
        rows.append(r)

    print("PRICE-VALIDATION STAGES")
    running=N0
    for k in ("S6 no 1m bars that session","S6 no bar at/before T_event","S6 non-positive pre-event price",
              "S7 <5 post-event bars"):
        if drops[k]:
            running-=len(drops[k])
            ex=[(d["symbol"],str(d["t_event"])[:16]) for d in drops[k][:4]]
            print(f"  {k:<34} dropped {len(drops[k]):>5}  -> {running:>5}   e.g. {ex}")
    nb=len(drops["S8 no benchmark at T_event"])
    print(f"  {'S8 no benchmark (kept, flagged)':<34} flagged {nb:>5}  -> {len(rows):>5}"
          f"   e.g. {[(d['symbol'],str(d['t_event'])[:16]) for d in drops['S8 no benchmark at T_event'][:3]]}")
    print(f"\nFINAL REACTION OBSERVATIONS: {len(rows)}  ({len(rows)/19241*100:.1f}% of the 19,241 raw mentions)")
    print(f"  instruments {len({r['symbol'] for r in rows})} · sessions {len({r['t_event'].date() for r in rows})}"
          f" · LONG {sum(1 for r in rows if r['side']=='LONG')} · SHORT {sum(1 for r in rows if r['side']=='SHORT')}")
    json.dump([{k:(v.isoformat() if isinstance(v,dt.datetime) else v) for k,v in r.items()} for r in rows],
              open(f"{SP}/react.json","w"))

asyncio.run(main())
