"""Ground-truth event set from NSE corporate announcements.

Source of truth: news_items WHERE source='NSE-Announcements'.
  · published_at comes from NSE's own `an_dt` field (crawler/news_crawler.py:435
    -> _parse_nse_announcement_dt), i.e. the EXCHANGE's publication timestamp,
    parsed from IST and stored as naive UTC. It is not our crawl time and not
    our DB insert time.
  · category comes from NSE's own classification, not from our LLM.
  · causal_events is not consulted anywhere in this file.

Direction is assigned by DETERMINISTIC RULES over NSE's category plus keyword
tests on the announcement text. No model is asked what it thinks. Categories
that do not carry a direction are labelled NEUTRAL and are NOT forced into a
side — they become the natural control for "an announcement happened".
"""
import asyncio, json, re, warnings, datetime as dt
from collections import defaultdict, Counter
warnings.filterwarnings("ignore")
from sqlalchemy import text
from db.database import AsyncSessionLocal

SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"

# ── deterministic taxonomy ───────────────────────────────────────────────────
POSITIVE_CATS = {
    "Bagging/Receiving of orders/contracts": ("ORDER_WIN", 2),
    "Awarding of order(s)/contract(s)":      ("ORDER_WIN", 2),
    "Acquisition":                           ("ACQUISITION", 1),
    "Amalgamation/Merger":                   ("ACQUISITION", 1),
    "Buyback":                               ("BUYBACK", 1),
    "Product launch":                        ("PRODUCT_LAUNCH", 3),
    "Agreements":                            ("MAJOR_PARTNERSHIP", 3),
    "Memorandum of Understanding/Agreements":("MAJOR_PARTNERSHIP", 3),
    "Scheme of Arrangement":                 ("CORPORATE_RESTRUCTURE", 2),
    "Demerger":                              ("CORPORATE_RESTRUCTURE", 2),
}
NEGATIVE_CATS = {
    "Resignation of Director/KMP/SMP":                 ("MANAGEMENT_RESIGNATION", 3),
    "Resignation":                                     ("MANAGEMENT_RESIGNATION", 3),
    "Resignation of Statutory Auditor":                ("AUDITOR_RESIGNATION", 1),
    "Reasons for Delayed/Non-submission of Financial": ("DELAYED_FILING", 2),
    "Granting/withdrawal/surrender/cancellation/suspe": ("REGULATORY_ACTION", 2),
}
NEUTRAL_CATS = {
    "Outcome of Board Meeting":                 ("ROUTINE_BOARD_MEETING", 4),
    "Press Release":                            ("ROUTINE_DISCLOSURE", 4),
    "Press Release (Revised)":                  ("ROUTINE_DISCLOSURE", 4),
    "Dividend":                                 ("DIVIDEND", 4),
    "Reply to Clarification- Financial results": ("ROUTINE_COMPLIANCE", 4),
    "Clarification - Financial Results":        ("ROUTINE_COMPLIANCE", 4),
    "Monthly Business Updates":                 ("ROUTINE_DISCLOSURE", 4),
    "Preferential issue":                       ("CAPITAL_RAISE", 3),
    "Rights Issue":                             ("CAPITAL_RAISE", 3),
}
# Credit-rating direction comes from the text, never from the bare category.
UPGRADE_RE   = re.compile(r"\b(upgrad|revised upward|improve|positive outlook|reaffirm.*stable)", re.I)
DOWNGRADE_RE = re.compile(r"\b(downgrad|revised downward|negative outlook|default|watch with negative)", re.I)

def classify(cat, text_blob):
    """-> (direction, subcategory, materiality_tier) or None to exclude."""
    cat = (cat or "").strip()
    if cat.startswith("Credit Rating"):
        if DOWNGRADE_RE.search(text_blob): return ("SHORT", "RATING_DOWNGRADE", 2)
        if UPGRADE_RE.search(text_blob):   return ("LONG",  "RATING_UPGRADE", 2)
        return ("NEUTRAL", "RATING_UNCLEAR", 4)
    if cat in POSITIVE_CATS:
        sub, tier = POSITIVE_CATS[cat]; return ("LONG", sub, tier)
    if cat in NEGATIVE_CATS:
        sub, tier = NEGATIVE_CATS[cat]; return ("SHORT", sub, tier)
    if cat in NEUTRAL_CATS:
        sub, tier = NEUTRAL_CATS[cat]; return ("NEUTRAL", sub, tier)
    return None

IST = dt.timedelta(hours=5, minutes=30)
def ist_time(u): return (u + IST).time()

async def main():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT id, headline, category, company, tickers_affected, published_at, crawled_at, url
            FROM news_items WHERE source='NSE-Announcements' ORDER BY published_at"""))).fetchall()
        cand = {r[0] for r in (await s.execute(text(
            "SELECT DISTINCT symbol FROM candles WHERE timeframe='1m'"))).fetchall()}
        cand1d = {r[0] for r in (await s.execute(text(
            "SELECT DISTINCT symbol FROM candles WHERE timeframe='1d'"))).fetchall()}

    print(f"S0  NSE announcements in DB                {len(rows):>6}  100.0%")
    N0 = len(rows)
    def pct(n): return f"{n/N0*100:>6.1f}%"

    out, drops = [], Counter(); ex = defaultdict(list)
    for nid, head, cat, comp, tick, pub, crawl, url in rows:
        if pub is None:
            drops["S1 no publication timestamp"] += 1; continue
        blob = f"{head or ''} {comp or ''}"
        cl = classify(cat, blob)
        if cl is None:
            drops["S2 category outside controlled taxonomy"] += 1
            if len(ex["S2"]) < 6: ex["S2"].append(str(cat))
            continue
        side, sub, tier = cl
        # symbol: tickers_affected is a JSON list written as '<SYM>.NS'
        t = tick
        if isinstance(t, str):
            try: t = json.loads(t)
            except Exception: t = [t]
        syms = [str(x).strip().upper() for x in t] if isinstance(t, list) else []
        sym = next((x for x in syms if x in cand), None)
        sym1d = next((x for x in syms if x in cand1d), None)
        if sym is None and sym1d is None:
            drops["S3 symbol not resolvable to candles"] += 1
            if len(ex["S3"]) < 6: ex["S3"].append(f"{comp}|{syms[:2]}")
            continue
        it = ist_time(pub)
        if   dt.time(9,15) <= it <= dt.time(15,30): window = "INTRADAY"
        elif it < dt.time(9,15):                    window = "PREMARKET"
        else:                                       window = "POSTMARKET"
        out.append(dict(nid=str(nid), sym=sym, sym1d=sym1d, cat=cat, sub=sub, side=side,
                        tier=tier, pub=pub.isoformat(), window=window,
                        company=comp, head=(head or "")[:200], url=url,
                        detect_lag_min=(crawl-pub).total_seconds()/60 if crawl else None))
    running = N0
    for k in ("S1 no publication timestamp","S2 category outside controlled taxonomy",
              "S3 symbol not resolvable to candles"):
        if drops[k]:
            running -= drops[k]
            tag = k.split()[0]
            print(f"{k:<44} dropped {drops[k]:>5} -> {running:>5}   e.g. {ex[tag][:4]}")
    print(f"\nGROUND-TRUTH EVENTS: {len(out)}  ({len(out)/N0*100:.1f}% of announcements)")
    c = Counter((o['window'], o['side']) for o in out)
    print(f"\n  {'window':<12}{'LONG':>7}{'SHORT':>7}{'NEUTRAL':>9}")
    for w in ("INTRADAY","PREMARKET","POSTMARKET"):
        print(f"  {w:<12}{c[(w,'LONG')]:>7}{c[(w,'SHORT')]:>7}{c[(w,'NEUTRAL')]:>9}")
    print(f"\n  by materiality tier: {dict(Counter(o['tier'] for o in out))}")
    print(f"  by subcategory:")
    for k,v in Counter(o['sub'] for o in out).most_common():
        print(f"     {k:<26}{v}")
    json.dump(out, open(f"{SP}/gt_events.json","w"))
    lags=[o['detect_lag_min'] for o in out if o['detect_lag_min'] is not None]
    if lags:
        import statistics as st
        lags_s=sorted(lags)
        print(f"\n  OUR detection lag vs NSE publication (min): median {st.median(lags):.1f}"
              f"  p90 {lags_s[int(len(lags_s)*0.9)]:.1f}  max {max(lags):.0f}")
asyncio.run(main())
