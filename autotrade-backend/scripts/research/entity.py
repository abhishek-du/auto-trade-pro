"""Entity-resolution audit: causal_event symbol strings -> tradable instrument.

Every stage is applied to the SAME raw population so the funnel is cumulative
and auditable. Nothing here uses market outcomes, so no stage can be tuned to
flatter the result.
"""
import asyncio, json, re, warnings
from collections import defaultdict, Counter
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal

SUFFIX = re.compile(r"\s*(LIMITED|LTD|LTD\.|LIMITED\.|PVT|PRIVATE|INDIA|\(I\))\s*$")
NONALNUM = re.compile(r"[^A-Z0-9]")

def norm_name(x: str) -> str:
    """Company-name normalisation: upper, drop THE-, drop legal suffixes, squeeze."""
    s = (x or "").upper().strip()
    s = re.sub(r"^THE\s+", "", s)
    for _ in range(3):
        s2 = SUFFIX.sub("", s).strip()
        if s2 == s: break
        s = s2
    return re.sub(r"\s+", " ", s).strip()

def norm_sym(x: str) -> str:
    """Ticker normalisation: strip exchange suffix, then all punctuation/space."""
    s = (x or "").upper().strip()
    s = re.sub(r"\.(NS|BO|NSE|BSE)$", "", s)
    return NONALNUM.sub("", s)

async def m():
    async with AsyncSessionLocal() as s:
        ev = (await s.execute(text("""SELECT bullish_stocks, bearish_stocks, event_title,
              importance, created_at FROM causal_events WHERE created_at >= DATE '2026-07-16'"""))).fetchall()
        kite = (await s.execute(text("""SELECT tradingsymbol, name, instrument_token, segment
              FROM kite_instruments WHERE instrument_type='EQ' AND segment IN ('NSE','BSE')"""))).fetchall()
        cand = {r[0] for r in (await s.execute(text(
              "SELECT DISTINCT symbol FROM candles WHERE timeframe='1m'"))).fetchall()}
        isin = {r[0].upper() for r in (await s.execute(text(
              "SELECT symbol FROM symbol_isin_map"))).fetchall()}

    # ── build the raw population, keeping provenance for the bias analysis ──
    raw = {}                       # raw string -> dict(count, types, importances)
    def add(v, title, imp):
        if isinstance(v, str):
            try: v = json.loads(v)
            except Exception: v = [x.strip() for x in v.split(",") if x.strip()]
        if not isinstance(v, list): return
        for t in v:
            t = str(t).strip()
            if not t: continue
            d = raw.setdefault(t.upper(), {"n":0, "types":Counter(), "imp":[]})
            d["n"] += 1; d["types"][title] += 1
            if imp is not None:
                try: d["imp"].append(float(imp))
                except Exception: pass
    for b1, b2, title, imp, _ in ev:
        add(b1, title, imp); add(b2, title, imp)

    N = len(raw)
    print(f"causal_events since 2026-07-16 : {len(ev)}")
    print(f"distinct raw symbol strings    : {N}\n")

    # ── resolution indexes ──
    cand_exact = {c for c in cand}                       # 'RELIANCE.NS'
    cand_base  = defaultdict(set)                        # 'RELIANCE' -> {'RELIANCE.NS', ...}
    for c in cand: cand_base[norm_sym(c)].add(c)
    kite_ts    = defaultdict(set)                        # normalised tradingsymbol
    kite_nm    = defaultdict(set)                        # normalised company name
    for ts, nm, tok, seg in kite:
        kite_ts[norm_sym(ts)].add(ts)
        if nm: kite_nm[norm_name(nm)].add(ts)

    stages = {k: set() for k in
              ("exact","normalized","kite_ts","kite_name","isin","tradable")}

    for r in raw:
        if r in cand_exact or f"{r}.NS" in cand_exact or f"{r}.BO" in cand_exact:
            stages["exact"].add(r)
        ns = norm_sym(r)
        if ns and ns in cand_base:
            stages["normalized"].add(r)
        if ns and ns in kite_ts:
            stages["kite_ts"].add(r)
        nn = norm_name(r)
        if nn and nn in kite_nm:
            stages["kite_name"].add(r)
        if ns in isin:
            stages["isin"].add(r)

    # tradable = resolves to a Kite EQ instrument by ticker OR name, AND that
    # instrument has 1m candles. This is the only stage that gates on data.
    for r in raw:
        tickers = set()
        ns, nn = norm_sym(r), norm_name(r)
        tickers |= kite_ts.get(ns, set())
        tickers |= kite_nm.get(nn, set())
        if any(f"{t}.NS" in cand_exact or f"{t}.BO" in cand_exact for t in tickers):
            stages["tradable"].add(r)

    cum = {
        "Raw causal-event symbol strings": set(raw),
        "Exact candle-symbol match":        stages["exact"],
        "+ normalized ticker match":        stages["exact"] | stages["normalized"],
        "+ Kite tradingsymbol match":       stages["exact"] | stages["normalized"] | stages["kite_ts"],
        "+ Kite company-NAME match":        stages["exact"] | stages["normalized"] | stages["kite_ts"] | stages["kite_name"],
        "+ ISIN map":                       stages["exact"] | stages["normalized"] | stages["kite_ts"] | stages["kite_name"] | stages["isin"],
        "TRADABLE (instrument + 1m data)":  stages["tradable"],
    }
    print(f"{'STAGE':<38}{'symbols':>9}{'%':>8}{'mentions':>11}{'% mentions':>12}")
    tot_mentions = sum(d["n"] for d in raw.values())
    for label, st in cum.items():
        men = sum(raw[x]["n"] for x in st)
        print(f"  {label:<36}{len(st):>9}{len(st)/N*100:>7.1f}%{men:>11}{men/tot_mentions*100:>11.1f}%")
    print(f"\n  (mentions = event-symbol pairs, i.e. how many actual events each stage keeps)")

    print(f"\n  incremental gain of each resolver, over exact match:")
    base = stages["exact"]
    for k in ("normalized","kite_ts","kite_name","isin"):
        print(f"    {k:<12} adds {len(stages[k]-base):>5} new symbols")

    # ── selection-bias check ──
    good = stages["tradable"]; bad = set(raw) - good
    print(f"\n=== SELECTION BIAS: mapped ({len(good)}) vs unmapped ({len(bad)}) ===")
    def prof(pop, label):
        tc = Counter()
        imps = []
        looks_ticker = 0
        for x in pop:
            tc.update(raw[x]["types"]); imps.extend(raw[x]["imp"])
            if " " not in x and len(x) <= 12: looks_ticker += 1
        import statistics as st_
        print(f"  {label}: n={len(pop)}  looks-like-ticker={looks_ticker/max(len(pop),1)*100:.0f}%"
              f"  median importance={st_.median(imps) if imps else float('nan'):.0f}")
        print(f"     top event types: {[f'{a}:{b}' for a,b in tc.most_common(6)]}")
    prof(good, "MAPPED  ")
    prof(bad,  "UNMAPPED")

    print(f"\n  unmapped samples (first 18):")
    for x in sorted(bad)[:18]:
        print(f"    {x[:56]:<58} mentions={raw[x]['n']}")
asyncio.run(m())
