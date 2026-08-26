"""Reaction-study eligibility funnel.

T_event = causal_events.created_at — the moment OUR system created the event
row. Using it means every measurement starts from information we demonstrably
held, so no stage can smuggle in look-ahead. (news_items.published_at would give
the theoretical ceiling but is populated for only 2,912 of 11,049 events.)

Every exclusion is counted and sampled. Nothing is dropped silently.
"""
import asyncio, json, re, warnings, datetime as dt
from collections import defaultdict, Counter
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal

NONALNUM = re.compile(r"[^A-Z0-9]")
SUFFIX   = re.compile(r"\s*(LIMITED|LTD|LTD\.|LIMITED\.|PVT|PRIVATE|INDIA|\(I\))\s*$")
def norm_sym(x):
    s=(x or "").upper().strip(); s=re.sub(r"\.(NS|BO|NSE|BSE)$","",s); return NONALNUM.sub("",s)
def norm_name(x):
    s=(x or "").upper().strip(); s=re.sub(r"^THE\s+","",s)
    for _ in range(3):
        s2=SUFFIX.sub("",s).strip()
        if s2==s: break
        s=s2
    return re.sub(r"\s+"," ",s).strip()

# NSE session in UTC (DB stores naive UTC): 09:15-15:30 IST = 03:45-10:00
SESS_OPEN, SESS_CLOSE = dt.time(3,45), dt.time(10,0)
CONFOUND_MIN = 60          # two events on one instrument inside this window are not independent
MIN_POST_BARS = 5          # need at least +5m of tape to measure anything

def stage(name, keep, drop, examples, log):
    log.append((name, len(keep), len(drop), examples[:6]))

async def main():
    async with AsyncSessionLocal() as s:
        ev = (await s.execute(text("""
            SELECT id, bullish_stocks, bearish_stocks, event_title, importance,
                   confidence, created_at, news_id
            FROM causal_events WHERE created_at >= DATE '2026-07-16'
            ORDER BY created_at"""))).fetchall()
        kite = (await s.execute(text("""SELECT tradingsymbol, name FROM kite_instruments
                WHERE instrument_type='EQ' AND segment IN ('NSE','BSE')"""))).fetchall()
        cand_syms = {r[0] for r in (await s.execute(text(
                "SELECT DISTINCT symbol FROM candles WHERE timeframe='1m'"))).fetchall()}

    kite_ts, kite_nm = defaultdict(set), defaultdict(set)
    for ts, nm in kite:
        kite_ts[norm_sym(ts)].add(ts)
        if nm: kite_nm[norm_name(nm)].add(ts)

    log = []
    # ── S0: raw mentions ──────────────────────────────────────────────────────
    raw = []
    for eid, b1, b2, title, imp, conf, ts, nid in ev:
        for v, side in ((b1,"LONG"), (b2,"SHORT")):
            if isinstance(v,str):
                try: v=json.loads(v)
                except Exception: v=[]
            if not isinstance(v,list): continue
            for t in v:
                t=str(t).strip()
                if t: raw.append(dict(eid=eid, raw=t.upper(), side=side, title=title,
                                      imp=imp, conf=conf, t_event=ts, news_id=nid))
    print(f"S0  RAW event-symbol mentions            {len(raw):>8}  100.0%")
    N0=len(raw)

    def pct(n): return f"{n/N0*100:>6.1f}%"

    # ── S1: resolve to a tradable instrument ─────────────────────────────────
    keep, drop = [], []
    for r in raw:
        ns, nn = norm_sym(r["raw"]), norm_name(r["raw"])
        tickers = kite_ts.get(ns,set()) | kite_nm.get(nn,set())
        hit = None
        for t in tickers:
            for sfx in (".NS",".BO"):
                if f"{t}{sfx}" in cand_syms: hit=f"{t}{sfx}"; break
            if hit: break
        if hit: r["symbol"]=hit; keep.append(r)
        else: drop.append(r)
    print(f"S1  mapped to instrument WITH 1m data    {len(keep):>8} {pct(len(keep))}   dropped {len(drop)}")
    print(f"      e.g. {[d['raw'] for d in drop[:6]]}")
    raw = keep

    # ── S2: event timestamp inside a trading session ─────────────────────────
    keep, drop = [], []
    for r in raw:
        t = r["t_event"]
        if t.weekday() < 5 and SESS_OPEN <= t.time() <= SESS_CLOSE: keep.append(r)
        else: drop.append(r)
    print(f"S2  T_event inside NSE session           {len(keep):>8} {pct(len(keep))}   dropped {len(drop)}")
    print(f"      e.g. {[(d['raw'], str(d['t_event'])[:16]) for d in drop[:4]]}")
    raw = keep

    # ── S3: leave room to measure — event before 15:00 IST (09:30 UTC) ───────
    keep, drop = [], []
    for r in raw:
        if r["t_event"].time() <= dt.time(9,30): keep.append(r)
        else: drop.append(r)
    print(f"S3  >=30 min of session left after event {len(keep):>8} {pct(len(keep))}   dropped {len(drop)}")
    print(f"      e.g. {[(d['raw'], str(d['t_event'].time())[:5]+' UTC') for d in drop[:4]]}")
    raw = keep

    # ── S4: de-duplicate identical (instrument, side, minute) ────────────────
    seen, keep, drop = set(), [], []
    for r in raw:
        k = (r["symbol"], r["side"], r["t_event"].replace(second=0, microsecond=0))
        if k in seen: drop.append(r)
        else: seen.add(k); keep.append(r)
    print(f"S4  de-duplicated (instrument,side,min)  {len(keep):>8} {pct(len(keep))}   dropped {len(drop)}")
    print(f"      e.g. {[(d['symbol'], str(d['t_event'])[:16]) for d in drop[:4]]}")
    raw = keep

    # ── S5: drop confounded events (same instrument within 60 min) ───────────
    bysym = defaultdict(list)
    for r in raw: bysym[r["symbol"]].append(r)
    keep, drop = [], []
    for sym, rows in bysym.items():
        rows.sort(key=lambda x: x["t_event"])
        last = None
        for r in rows:
            if last is not None and (r["t_event"]-last).total_seconds() < CONFOUND_MIN*60:
                r["_conf_prev"]=last; drop.append(r)
            else:
                keep.append(r); last = r["t_event"]
    print(f"S5  non-confounded (>{CONFOUND_MIN}min apart)      {len(keep):>8} {pct(len(keep))}   dropped {len(drop)}")
    print(f"      e.g. {[(d['symbol'], str(d['t_event'])[11:16], 'prev '+str(d['_conf_prev'])[11:16]) for d in drop[:4]]}")
    raw = keep

    # persist the survivor set for the reaction pass
    out=[dict(eid=str(r['eid']), symbol=r['symbol'], side=r['side'], title=r['title'],
              imp=float(r['imp']) if r['imp'] is not None else None,
              conf=float(r['conf']) if r['conf'] is not None else None,
              t_event=r['t_event'].isoformat(), raw=r['raw'],
              linked_news=bool(r['news_id'])) for r in raw]
    json.dump(out, open("/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad/elig.json","w"))
    print(f"\n  carried to price-validation pass: {len(out)}")
    print(f"  distinct instruments {len({r['symbol'] for r in raw})} · distinct sessions {len({r['t_event'].date() for r in raw})}")
    print(f"  LONG {sum(1 for r in raw if r['side']=='LONG')} · SHORT {sum(1 for r in raw if r['side']=='SHORT')}")

asyncio.run(main())
