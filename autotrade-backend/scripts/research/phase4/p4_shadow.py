"""PHASE 4 — offline reconstruction of the Hub origination funnel.

SHADOW SAFETY (Part 15): this module imports NOTHING from the execution stack.
No decision_router, no tactical_executor, no trade_simulator, no paper_trading,
no zerodha_executor. It opens read-only SELECTs and writes one JSONL file.
An AST guard in p4_safety.py proves it, and is run before this script.

Faithfulness: every filter below cites the production line it reproduces, from
tasks/india_tasks.py at commit HEAD. Divergences are listed in DIVERGENCES.
"""
import asyncio, json, sys, datetime as dt
from collections import defaultdict
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
from sqlalchemy import text
from db.database import AsyncSessionLocal

# ── production constants, read from the same settings the loop reads ─────────
CONF_THRESHOLD   = 50.0    # settings.PAPER_CONFIDENCE_THRESHOLD          :639
MAX_NEW          = 5       # settings.MAX_NEW_ENTRIES_PER_CYCLE           :885
CAP              = min(max(MAX_NEW*3, 12), 24)                          # :888
MAX_STOCK_W      = 10.0    # PortfolioPolicy default (row absent)         :738
MAX_SECTOR_W     = 25.0    #                                             :739
NEWS_OVERRIDE_ON = True    # settings.NEWS_REGIME_OVERRIDE_ENABLED        :752
NEWS_OVERRIDE_MIN= 75.0    # settings.NEWS_OVERRIDE_MIN_NEWS_SCORE        :753
ACTIONABLE       = ("BUY","STRONG_BUY","SELL","STRONG_SELL")             # :640
SCORE_WINDOW_MIN = 45      # _cutoff = max(now-45min, session_open)       :658
PRICE_MAX_AGE_MIN= 90      # candle fallback freshness gate               :~800

DIVERGENCES = [
 "entry price: production tries PRICE_CACHE, then Kite REST LTP, then a <=90min "
 "candle. Neither cache nor a live REST call is reproducible after the fact, so "
 "this uses the third fallback (the 1m close at or before the cycle timestamp, "
 "rejected if >90min stale). Production would resolve a price at least as often, "
 "so BLOCKED_BY_PRICE here is an UPPER bound.",
 "position/sector weights: rebuilt from paper_trades open at the cycle instant, "
 "using size_usd/500000. Production reads get_position_weights() live.",
 "research veto and portfolio-brain stance run after the cap and involve live "
 "web calls; they are NOT reproduced. Candidates surviving the cap are reported "
 "as SHADOW_ELIGIBLE, which is therefore also an upper bound.",
]

SESSION_OPEN_U = dt.time(3,45); SESSION_CLOSE_U = dt.time(10,0)
ENTRY_CUTOFF_U = dt.time(9,50)   # 15:20 IST — the loop's own is_entry_window :531

async def main(dstr):
    d = dt.date.fromisoformat(dstr)
    async with AsyncSessionLocal() as db:
        scores = (await db.execute(text("""
            SELECT symbol, scored_at, master_score, signal, technical_score, news_score,
                   sector_score, macro_score, earnings_score, fundamental_score,
                   options_score, is_blocked, rank
            FROM master_intelligence_scores
            WHERE scored_at::date=:d AND master_score IS NOT NULL AND symbol LIKE '%.NS'
            ORDER BY scored_at"""), {"d": d})).fetchall()
        trades = (await db.execute(text("""
            SELECT symbol, size_usd, opened_at, closed_at FROM paper_trades
            WHERE size_usd IS NOT NULL AND opened_at <= :hi
              AND (closed_at IS NULL OR closed_at >= :lo)"""),
            {"lo": dt.datetime.combine(d, dt.time(0,0)), "hi": dt.datetime.combine(d, dt.time(23,59))})).fetchall()
        syms = sorted({r[0] for r in scores})
        bars = defaultdict(list)
        for i in range(0, len(syms), 300):
            for sym, ts, cl in (await db.execute(text("""
                SELECT symbol, timestamp, close FROM candles
                WHERE timeframe='1m' AND timestamp::date=:d AND symbol = ANY(:s)
                ORDER BY symbol, timestamp"""), {"d": d, "s": syms[i:i+300]})).fetchall():
                bars[sym].append((ts, float(cl)))
    import bisect
    times = {k:[b[0] for b in v] for k,v in bars.items()}
    def price_at(sym, ts):
        t = times.get(sym)
        if not t: return 0.0, None
        i = bisect.bisect_right(t, ts) - 1
        if i < 0: return 0.0, None
        age = (ts - t[i]).total_seconds()/60
        if age > PRICE_MAX_AGE_MIN: return 0.0, age
        return bars[sym][i][1], age
    def weights_at(ts):
        w = defaultdict(float); sec = defaultdict(float)
        for sym, sz, o, c in trades:
            if o <= ts and (c is None or c > ts):
                w[sym] += float(sz)/500000*100
        return w, sec

    # ── replay one cycle per minute across the entry window ──────────────────
    out = []
    cycles = []
    t = dt.datetime.combine(d, SESSION_OPEN_U)
    end = dt.datetime.combine(d, ENTRY_CUTOFF_U)
    per_symbol_latest = defaultdict(list)
    for r in scores: per_symbol_latest[r[0]].append(r)
    while t <= end:
        cutoff = max(t - dt.timedelta(minutes=SCORE_WINDOW_MIN),
                     dt.datetime.combine(d, SESSION_OPEN_U))
        # latest score per symbol inside the window  (:658-697)
        rows = []
        for sym, rs in per_symbol_latest.items():
            best = None
            for r in rs:
                if cutoff <= r[1] <= t and (best is None or r[1] > best[1]): best = r
            if best is not None: rows.append(best)
        hub_rows = [r for r in rows if not r[11] and r[3] in ACTIONABLE]     # :693-695
        pw, _ = weights_at(t)
        signals = []
        term = defaultdict(int)
        for r in hub_rows:
            sym, score, sig = r[0], float(r[2]), r[3]
            conf = min(100.0, abs(score))                                    # :746
            news_sub = float(r[5] or 0)
            override = NEWS_OVERRIDE_ON and news_sub >= NEWS_OVERRIDE_MIN     # :751-754
            if conf < CONF_THRESHOLD and not override:                        # :755
                term["BLOCKED_BY_CONFIDENCE"] += 1; continue
            if override:
                action = "BUY"; conf = max(conf, min(100.0, 50.0 + news_sub*0.5))
            else:
                action = "BUY" if "BUY" in sig else "SELL"                    # :763
            if action == "SELL" and conf < 50:                                # :765
                term["BLOCKED_BY_SELL_CONFIDENCE"] += 1; continue
            px, age = price_at(sym, t)
            if px <= 0:
                term["BLOCKED_BY_PRICE"] += 1; continue                       # :~805
            sym_w = pw.get(sym, 0.0)
            if sym_w >= MAX_STOCK_W:
                term["BLOCKED_BY_STOCK_WEIGHT"] += 1; continue                # :828
            adj = conf * (1.0 - sym_w/MAX_STOCK_W)                            # compute_adjusted_score
            if adj <= 0:
                term["BLOCKED_BY_ADJUSTED_SCORE"] += 1; continue              # :836
            signals.append(dict(sym=sym, score=score, sig=sig, action=action,
                                conf=adj, px=px, rank=r[12], news=news_sub,
                                override=override))
        actionable = [s for s in signals if s["action"] in ("BUY","SELL")]     # :867
        actionable.sort(key=lambda s: s["conf"], reverse=True)                 # :884
        level_pool = actionable[: min(len(actionable), CAP)]                   # :888
        cut = actionable[len(level_pool):]
        for s in cut: term["BLOCKED_BY_CANDIDATE_CAP"] += 1
        for s in level_pool:
            term["SHADOW_ELIGIBLE"] += 1
            out.append(dict(t=str(t), **s))
        cycles.append(dict(t=str(t), hub_rows=len(rows), actionable_rows=len(hub_rows),
                           sb=sum(1 for r in hub_rows if r[3]=="STRONG_BUY"),
                           b=sum(1 for r in hub_rows if r[3]=="BUY"),
                           s=sum(1 for r in hub_rows if r[3]=="SELL"),
                           ss=sum(1 for r in hub_rows if r[3]=="STRONG_SELL"),
                           blocked=sum(1 for r in rows if r[11]),
                           signals=len(signals), actionable=len(actionable),
                           level_pool=len(level_pool), terminal=dict(term)))
        t += dt.timedelta(minutes=1)
    json.dump(dict(date=dstr, cycles=cycles, candidates=out, divergences=DIVERGENCES),
              open(f"{SP_OUT}/p4_{dstr}.json","w"))
    tot = defaultdict(int)
    for c in cycles:
        for k,v in c["terminal"].items(): tot[k]+=v
    print(f"{dstr}: {len(cycles)} cycles · hub_rows/cycle avg "
          f"{sum(c['hub_rows'] for c in cycles)/max(len(cycles),1):.0f} · "
          f"shadow candidates {len(out)}")
    for k,v in sorted(tot.items(), key=lambda x:-x[1]): print(f"    {k:<28} {v:>8,}")

SP_OUT="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
for ds in ("2026-08-25","2026-08-24"):
    asyncio.run(main(ds))
