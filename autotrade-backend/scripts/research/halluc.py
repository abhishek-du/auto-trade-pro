"""Are the unmapped ticker-like strings REAL instruments we failed to resolve,
or strings that do not exist on any Indian exchange?"""
import asyncio, json, re, warnings
from collections import Counter, defaultdict
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal
NONALNUM = re.compile(r"[^A-Z0-9]")
SUFFIX = re.compile(r"\s*(LIMITED|LTD|LTD\.|LIMITED\.|PVT|PRIVATE|INDIA|\(I\))\s*$")
def norm_sym(x):
    s=(x or "").upper().strip(); s=re.sub(r"\.(NS|BO|NSE|BSE)$","",s); return NONALNUM.sub("",s)
def norm_name(x):
    s=(x or "").upper().strip(); s=re.sub(r"^THE\s+","",s)
    for _ in range(3):
        s2=SUFFIX.sub("",s).strip()
        if s2==s: break
        s=s2
    return re.sub(r"\s+"," ",s).strip()
async def m():
    async with AsyncSessionLocal() as s:
        ev=(await s.execute(text("""SELECT bullish_stocks,bearish_stocks FROM causal_events
             WHERE created_at >= DATE '2026-07-16'"""))).fetchall()
        kite=(await s.execute(text("""SELECT tradingsymbol,name,instrument_type,segment
             FROM kite_instruments"""))).fetchall()
        cand={r[0] for r in (await s.execute(text(
             "SELECT DISTINCT symbol FROM candles WHERE timeframe='1m'"))).fetchall()}
    raw=Counter()
    for b1,b2 in ev:
        for v in (b1,b2):
            if isinstance(v,str):
                try: v=json.loads(v)
                except Exception: v=[]
            if isinstance(v,list):
                for t in v:
                    t=str(t).strip().upper()
                    if t: raw[t]+=1
    # every instrument Kite knows, ANY type/segment
    all_ts=defaultdict(set); all_nm=defaultdict(set); eq_ts=set()
    for ts,nm,it,seg in kite:
        all_ts[norm_sym(ts)].add((ts,it,seg))
        if nm: all_nm[norm_name(nm)].add((ts,it,seg))
        if it=="EQ" and seg in ("NSE","BSE"): eq_ts.add(norm_sym(ts))
    cand_base={norm_sym(c) for c in cand}
    buckets=Counter(); examples=defaultdict(list)
    for r,n in raw.items():
        ns,nn=norm_sym(r),norm_name(r)
        resolved = (ns in eq_ts) or (nn in all_nm)
        if resolved:
            if ns in cand_base or any(norm_sym(t) in cand_base for t,_,_ in all_nm.get(nn,set())):
                b="RESOLVED + has 1m data"
            else:
                b="RESOLVED but no 1m candles"
        else:
            if ns in all_ts:
                b="exists on Kite but NOT an NSE/BSE equity"
            elif " " in r or len(r)>14:
                b="company-name string we could not resolve"
            else:
                b="ticker-like string that exists NOWHERE on Kite"
        buckets[b]+=n
        if len(examples[b])<10: examples[b].append(f"{r}({n})")
    tot=sum(buckets.values())
    print(f"event-symbol mentions since 2026-07-16: {tot}\n")
    print(f"{'BUCKET':<48}{'mentions':>10}{'%':>8}")
    for b,c in buckets.most_common():
        print(f"  {b:<46}{c:>10}{c/tot*100:>7.1f}%")
    print()
    for b,_ in buckets.most_common():
        print(f"  {b}:\n     {', '.join(examples[b])}")
asyncio.run(m())
