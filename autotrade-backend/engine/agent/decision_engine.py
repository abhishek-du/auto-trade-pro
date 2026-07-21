"""Decision Engine — fuses candidate + context into a structured decision.

Reference: trading_agent/decision.py (extended with bear-case check, M12).

Pipeline order:
  1. fetch_hub_candidate()  — regime restriction + conflict detection (hard skips)
  2. DecisionEngine.fuse()  — multiplicative confidence + threshold check + position sizing
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from utils.config import settings
from utils.logger import logger


# Strategies that require MIS (intraday) product per NSE/SEBI rules:
# - Short-selling is only allowed intraday; delivery short is illegal on NSE/BSE
# - MIS positions must be squared off before 3:20 PM IST (Zerodha auto-squareoff)
_MIS_STRATEGIES = {"MEAN_REVERSION_SHORT"}


@dataclass
class AgentDecisionOutput:
    symbol:             str
    action:             str
    confidence:         int
    regime:             str
    strategy:           str
    entry:              float
    stop:               float
    target:             float
    qty:                int
    risk_pct:           float
    risk_reward:        float
    product:            str   = "CNC"   # CNC=delivery positional | MIS=intraday | NRML=F&O
    reasons:            list  = field(default_factory=list)
    macro_bias:         int   = 0
    fund_score:         int   = 0
    fund_grade:         str   = "WATCHLIST"
    ts:                 str   = ""
    master_score:       float | None = None   # raw hub score before confidence calc
    confidence_factors: dict  | None = None   # breakdown for audit log
    # ── F&O fields (EQUITY for cash trades; populated for FUTURE/CE/PE) ────────
    instrument_type:    str   = "EQUITY"        # EQUITY | FUTURE | CE | PE
    underlying_symbol:  str   | None = None     # e.g. "NIFTY" for a NIFTY option
    tradingsymbol:      str   | None = None      # broker NFO symbol, e.g. NIFTY26JAN24500CE
    strike_price:       float | None = None
    option_type:        str   | None = None     # CE | PE
    expiry_date:        str   | None = None     # ISO date string
    lot_size:           int   = 1
    contract_multiplier: float = 1.0
    exchange:           str   = "NSE"           # NSE | NFO

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts or datetime.utcnow().isoformat()
        return d


async def _candidate_context(symbol: str, candidate, decision) -> str:
    """Shared, model-readable summary of a candidate + its 7-factor breakdown.
    Used by all reasoning levels. When Level-4 reflection is on, appends the most
    relevant past lessons so the model learns from the agent's own history."""
    sub = getattr(candidate, "hub_subscores", {}) or {}
    cf  = getattr(decision, "confidence_factors", {}) or {}
    base = (
        f"Symbol {symbol} | Side {decision.action} | Strategy {candidate.strategy}\n"
        f"Regime {decision.regime} | MasterScore {decision.master_score}\n"
        f"Entry {candidate.entry} Stop {candidate.stop} Target {candidate.target} "
        f"R:R {candidate.risk_reward}\n"
        f"7-factor: technical={sub.get('technical')} news={sub.get('news')} "
        f"sector={sub.get('sector')} macro={sub.get('macro')} earnings={sub.get('earnings')} "
        f"fundamental={sub.get('fundamental')} options={sub.get('options')}\n"
        f"Modifiers: news_factor={cf.get('news_factor')} earnings_tone={cf.get('earnings_tone')} "
        f"fii_bias={cf.get('fii_bias')} regime_factor={cf.get('regime_factor')}\n"
        f"Arithmetic confidence {decision.confidence}%\n"
    )
    
    try:
        from crawler.india_price_feed import PRICE_CACHE
        nifty = PRICE_CACHE.get("^NSEI", {})
        banknifty = PRICE_CACHE.get("^NSEBANK", {})
        base += f"\nLIVE MACRO: NIFTY 50: {nifty.get('change_pct', 0.0)}% | BANKNIFTY: {banknifty.get('change_pct', 0.0)}%\n"
    except Exception:
        pass
    
    # Inject deep fundamentals (Screener.in equivalent)
    try:
        from db.database import AsyncSessionLocal
        from db.models import FundamentalData
        from sqlalchemy import select
        async with AsyncSessionLocal() as s:
            f_row = (await s.execute(select(FundamentalData).where(
                FundamentalData.symbol.in_([symbol, symbol.replace(".NS", "")])
            ).limit(1))).scalar_one_or_none()
            if f_row:
                base += (f"\nDEEP FUNDAMENTALS (Screener):\n"
                         f"PE: {f_row.pe_ratio} | PB: {f_row.pb_ratio} | ROE: {f_row.roe}% | ROCE: {f_row.roce}%\n"
                         f"D/E: {f_row.debt_to_equity} | Mkt Cap: {f_row.market_cap_cr} Cr | Div: {f_row.dividend_yield}%\n"
                         f"FII: {f_row.fii_holding}% | Promoter: {f_row.promoter_holding}%\n"
                         f"Rev Growth 3y: {f_row.revenue_growth_3yr}% | Profit Growth 3y: {f_row.profit_growth_3yr}%\n")
    except Exception:
        pass
        
    # Technical / chart read (candlestick patterns, indicator states, support/
    # resistance, ML direction) so the model reasons over the CHART, not just the
    # numeric factors. Attached upstream by the trade loops; absent → skipped.
    brief = getattr(candidate, "chart_brief", None)
    if brief:
        base += "\nTechnical / chart read:\n" + str(brief)

    # Phase 3 (canonical event -> decision-context binding): when a canonical
    # CausalEvent already exists for this candidate, the LLM's job changes
    # from "discover what happened" to "given what already happened, is it
    # tradeable." event_id is rendered for traceability (so this exact prompt
    # can be tied back to the CausalEvent row in logs), not so the model can
    # verify the database — it can't, and isn't asked to. The gate
    # (_verify_canonical_event, engine/decision_router.py) is what actually
    # enforces consistency after the verdict comes back; this block is what
    # gives the model the real facts up front so it has less reason to
    # contradict them in the first place.
    evidence = getattr(candidate, "evidence", None)
    if evidence is not None:
        event_id = getattr(candidate, "event_id", None)
        base += (
            f"\n=== CANONICAL_EVENT (already established — you are not discovering this) ===\n"
            f"event_id: {event_id}\n"
            f"event_category: {evidence.event_category}\n"
            f"materiality: {evidence.materiality}\n"
            f"direction: {evidence.direction}\n"
            f"source_type: {evidence.source_type}\n"
            f"published_at: {evidence.published_at}\n"
            f"title: {evidence.title}\n"
            f"summary: {evidence.summary}\n"
            f"classifier_confidence: {evidence.confidence:.2f}\n"
            f"===============================================================\n"
            f"The event above is canonical fact, not a lead to investigate further. "
            f"You may NOT search for or substitute a different news event, a different "
            f"cause, or a different affected company. Your job is only to determine:\n"
            f"  1. Does {symbol} actually correspond to the company this event concerns?\n"
            f"  2. Is the event's stated materiality/direction enough to act on right now?\n"
            f"  3. Does current market context (price action, sector, macro, technicals) "
            f"support or argue against acting on it?\n"
            f"  4. What are the risks to this specific trade?\n"
            f"  5. What confidence do you have in EXECUTING on this canonical event now — "
            f"not in whether the event itself is real (it is; that's already verified)?\n"
        )
    try:
        from engine.agent.reflection import get_relevant_lessons
        lessons = await get_relevant_lessons(candidate.strategy, decision.regime, decision.action)
        if lessons:
            base += "\nPast lessons from similar trades:\n" + \
                    "\n".join(f"- {l}" for l in lessons)
    except Exception:
        pass
    
    try:
        import os, json
        rules_file = os.path.join(os.path.dirname(__file__), "agent_rules.json")
        if os.path.exists(rules_file):
            with open(rules_file, "r") as f:
                rules_list = json.load(f)
                if rules_list:
                    base += "\nCRITICAL Global Agent Rules (MUST OBEY):\n" + \
                            "\n".join(f"- {r['rule']}" for r in rules_list)
    except Exception:
        pass
    return base


def _parse_first_json(resp: str) -> dict | None:
    """Extract the FIRST JSON object from an LLM response, tolerating trailing
    text or extra objects (raw_decode stops after the first complete value)."""
    if not resp:
        return None
    import json as _json
    i = resp.find("{")
    if i < 0:
        return None
    try:
        obj, _end = _json.JSONDecoder().raw_decode(resp[i:])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def llm_reason_candidate(symbol: str, candidate, decision) -> dict | None:
    """Level-1 LLM reasoning: ask the model to reason over a qualified candidate
    (bull case / bear case / biggest risk) and return a structured verdict.

    Returns {verdict, confidence, bull, bear, key_risk} or None on any failure
    (the caller then falls back to the arithmetic decision — fail-open).
    """
    try:
        from utils.llm import call_llm_chat

        sys_prompt = (
            "You are a senior discretionary Indian-equity (NSE) swing trader. Weigh "
            "ALL the evidence given like a human expert would — the 7 fundamental/"
            "sentiment factors (technical, news, sector, macro, earnings, fundamental, "
            "options), the technical/chart read (candlestick patterns, indicator "
            "states, support/resistance, ML next-day forecast), and any past lessons. "
            "Reason briefly about the bull case, the bear case, and the single biggest "
            "risk, then decide TAKE or SKIP. Be skeptical: SKIP when the chart and the "
            "factors disagree, the edge is weak, the regime is unsupportive, or the "
            "risk/reward is poor — do not take a trade just because the score is high. "
            'Respond with ONLY compact JSON: '
            '{"verdict":"TAKE"|"SKIP","confidence":<0-100 int>,'
            '"bull":"<=20 words","bear":"<=20 words","key_risk":"<=12 words"}'
        )
        user_prompt = await _candidate_context(symbol, candidate, decision)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ]
        resp = await call_llm_chat(messages, max_tokens=300, temperature=0.2)
        return _parse_first_json(resp)
    except Exception as exc:
        logger.debug(f"[agent/llm_reason] {symbol} reasoning failed: {exc}")
        return None


async def llm_debate_candidate(symbol: str, candidate, decision) -> dict | None:
    """Level-2 multi-agent debate: a Bull, a Bear and a Risk analyst argue
    independently (in parallel), then a Judge synthesises the verdict.

    Returns {verdict, confidence, bull, bear, key_risk, judge} (plus raw analyst
    notes) or None on failure (caller falls back to arithmetic — fail-open).
    """
    try:
        import asyncio as _aio
        from utils.llm import call_llm_chat

        context = await _candidate_context(symbol, candidate, decision)

        async def _analyst(role: str) -> str:
            msgs = [
                {"role": "system", "content": role},
                {"role": "user",   "content": context},
            ]
            return (await call_llm_chat(msgs, max_tokens=160, temperature=0.3)) or ""

        bull_role = ("You are a BULL-side NSE equity analyst. In <=40 words, make the "
                     "strongest concrete case FOR taking this long trade — name the catalyst/edge.")
        bear_role = ("You are a BEAR-side NSE equity analyst. In <=40 words, make the "
                     "strongest case AGAINST it — name the most likely way this trade loses.")
        risk_role = ("You are a RISK manager. In <=40 words, judge the risk/reward, regime "
                     "fit and position risk; state plainly whether the risk is acceptable.")

        bull, bear, risk = await _aio.gather(
            _analyst(bull_role), _analyst(bear_role), _analyst(risk_role)
        )
        if not (bull or bear or risk):
            return None

        judge_sys = (
            "You are the head portfolio manager. Weigh the BULL, BEAR and RISK views "
            "and decide TAKE or SKIP. Be skeptical — SKIP when the bear/risk case "
            "outweighs a thin bull case or the factors conflict. "
            'Respond with ONLY compact JSON: '
            '{"verdict":"TAKE"|"SKIP","confidence":<0-100 int>,"bull":"<=20 words",'
            '"bear":"<=20 words","key_risk":"<=12 words","judge":"<=25 words rationale"}'
        )
        judge_user = f"{context}\n\nBULL: {bull}\nBEAR: {bear}\nRISK: {risk}"
        resp = await call_llm_chat(
            [{"role": "system", "content": judge_sys},
             {"role": "user",   "content": judge_user}],
            max_tokens=320, temperature=0.2,
        )
        data = _parse_first_json(resp)
        if not data:
            return None
        data["_panel"] = {"bull": bull[:200], "bear": bear[:200], "risk": risk[:200]}
        return data
    except Exception as exc:
        logger.debug(f"[agent/llm_debate] {symbol} debate failed: {exc}")
        return None


# ── Level-3 agentic tools: the LLM pulls fresh data before deciding ──────────
async def _tool_fundamentals(symbol: str) -> str:
    try:
        from engine.fundamental_analyzer import fetch_fundamentals_yfinance, fetch_fundamentals_screener, calculate_fundamental_score
        import asyncio
        
        bare = symbol.replace(".NS", "")
        # Live fetch in parallel (skipping database entirely)
        yf_task = asyncio.to_thread(fetch_fundamentals_yfinance, symbol)
        sc_task = fetch_fundamentals_screener(bare)
        yf_data, sc_data = await asyncio.gather(yf_task, sc_task)
        
        merged = {**yf_data, **sc_data}
        score = calculate_fundamental_score(merged)
        
        return (f"fundamentals (LIVE API): PE={merged.get('pe_ratio')} ROE={merged.get('roe')}% ROCE={merged.get('roce')}% D/E={merged.get('debt_to_equity')} "
                f"rev_growth_3y={merged.get('revenue_growth_3yr')}% profit_growth_3y={merged.get('profit_growth_3yr')}% promoter={merged.get('promoter_holding')}% score={score}")
    except Exception as exc:
        return f"fundamentals: error ({exc})"


async def _tool_news(symbol: str) -> str:
    try:
        import urllib.request
        import xml.etree.ElementTree as ET
        bare = symbol.replace(".NS", "")
        url = f"https://news.google.com/rss/search?q={bare}+stock+OR+share+india&hl=en-IN&gl=IN&ceid=IN:en".replace(" ", "%20")
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_data = response.read()
            
        root = ET.fromstring(xml_data)
        headlines = []
        for item in root.findall('./channel/item')[:3]:
            title = item.find('title').text
            pub_date = item.find('pubDate').text
            headlines.append(f"[{pub_date}] {title}")
            
        if not headlines:
            return "news (LIVE): No recent news found on Google News."
            
        return "news (LIVE): " + " | ".join(headlines)
    except Exception as exc:
        return f"news: error ({exc})"


async def _tool_options(symbol: str) -> str:
    try:
        from db.database import AsyncSessionLocal
        from sqlalchemy import text as _t
        bare = symbol.replace(".NS", "")
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(_t(
                "SELECT option_type, sum(oi) FROM option_contract_snapshots "
                "WHERE underlying IN (:a,:b) AND snapshot_at = "
                "(SELECT max(snapshot_at) FROM option_contract_snapshots WHERE underlying IN (:a,:b)) "
                "GROUP BY option_type"), {"a": bare, "b": symbol})).fetchall()
        if not rows:
            return "options: no chain data"
        oi = {str(k): float(v or 0) for k, v in rows}
        ce, pe = oi.get("CE", 0) or oi.get("call", 0), oi.get("PE", 0) or oi.get("put", 0)
        pcr = round(pe / ce, 2) if ce else None
        return f"options: CE_OI={ce:.0f} PE_OI={pe:.0f} PCR={pcr}"
    except Exception as exc:
        return f"options: error ({exc})"


async def _tool_price_action(symbol: str) -> str:
    try:
        from crawler.zerodha_market import get_kite_historical, get_live_prices
        from datetime import datetime, timedelta
        
        now = datetime.now()
        start = now - timedelta(days=35)
        # 1. Fetch live historical daily candles from Zerodha directly
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            candles = await get_kite_historical(symbol, start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), "day", session=session)
        
        if not candles:
            return "price_action: live historical data unavailable"
            
        cl = [float(c["close"]) for c in reversed(candles)] # recent first
        
        # 2. Merge LIVE LTP for today
        live_data = await get_live_prices([symbol])
        if live_data and symbol in live_data and live_data[symbol].get('last_price'):
            cl.insert(0, live_data[symbol]['last_price'])
            
        if len(cl) < 6:
            return "price_action: insufficient history"
        last = cl[0]
        ret5 = round((last / cl[5] - 1) * 100, 2)
        hi20, lo20 = max(cl[:20]), min(cl[:20])
        pos = round((last - lo20) / (hi20 - lo20) * 100, 0) if hi20 > lo20 else 50
        return f"price_action: LIVE LTP={last} 5d_return={ret5}% pos_in_20d_range={pos}%"
    except Exception as exc:
        return f"price_action: error ({exc})"


async def _tool_market_depth(symbol: str) -> str:
    try:
        from crawler.zerodha_market import get_live_prices
        resp = await get_live_prices([symbol])
        data = resp.get(symbol)
        if not data:
            return "market_depth: live data unavailable"
        
        buy_qty = sum([b.get("quantity", 0) for b in data.get("buy_depth", [])])
        sell_qty = sum([s.get("quantity", 0) for s in data.get("sell_depth", [])])
        return (f"market_depth: Live LTP={data.get('last_price')} | Volume={data.get('volume')} | "
                f"Total Buy Pending: {buy_qty} | Total Sell Pending: {sell_qty}")
    except Exception as exc:
        return f"market_depth: error ({exc})"


# Resolve a symbol's sector via utils.sector_cache.get_sector() — a
# persistent, disk-backed cache (data/sector_cache.json, ~1,500+ NSE symbols
# already resolved, weekly rebuild + live yfinance fallback on miss) that
# engine/intelligence_hub.py already relies on for the exact same purpose.
# _tool_sector_analysis previously built its own reverse-lookup off the
# ~45-stock SECTOR_DEFINITIONS["stocks"] lists directly — confirmed live
# that those lists didn't even cover TVS Motor, while get_sector() already
# had it cached ("Consumer"). get_sector() returns one of the same 10
# canonical keys SECTOR_CACHE/SECTOR_DEFINITIONS use, or "GENERAL" on a
# genuine miss.
def _symbol_to_sector_key(symbol: str) -> str | None:
    from utils.sector_cache import get_sector
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    sector_key = get_sector(bare)
    return sector_key if sector_key != "GENERAL" else None


async def _tool_sector_analysis(symbol: str) -> str:
    try:
        from crawler.sector_data import SECTOR_CACHE, get_sector_cache
        sector_key = _symbol_to_sector_key(symbol)
        if not sector_key:
            return (
                f"sector_analysis: no sector classification available for {symbol} "
                f"(genuine data gap, not a coverage limit — checked against a "
                f"~1,500+ symbol cache with live yfinance fallback)."
            )
        cache = SECTOR_CACHE or get_sector_cache()
        data = cache.get(sector_key)
        if not data:
            return f"sector_analysis: Sector is {sector_key}. No live index data."
        idx_chg = data.get("index_change_pct")
        mood = data.get("mood", "NEUTRAL")
        if idx_chg is not None:
            return (
                f"sector_analysis: Sector is {data.get('name', sector_key)}. "
                f"Index {data.get('index_symbol')} is at {idx_chg}% today. Mood: {mood}."
            )
        return (
            f"sector_analysis: Sector is {data.get('name', sector_key)}. Mood: {mood} "
            f"(avg peer move {data.get('avg_change_pct')}%). No live index quote."
        )
    except Exception as exc:
        return f"sector_analysis: error ({exc})"

async def _tool_macro_environment(symbol: str) -> str:
    try:
        from db.database import AsyncSessionLocal
        from db.models import FIIDIIFlow
        from sqlalchemy import select
        async with AsyncSessionLocal() as s:
            fii = (await s.execute(select(FIIDIIFlow).order_by(FIIDIIFlow.date.desc()).limit(1))).scalar_one_or_none()
        if fii:
            return f"macro_environment: Date={fii.date} | FII Net: {fii.fii_net_buy} Cr | DII Net: {fii.dii_net_buy} Cr"
        return "macro_environment: No FII/DII data available."
    except Exception as exc:
        return f"macro_environment: error ({exc})"

async def _tool_earnings_report(symbol: str) -> str:
    try:
        # Reusing the live API fundamental fetcher to guarantee fresh data
        fund_data = await _tool_fundamentals(symbol)
        return fund_data.replace("fundamentals", "earnings_report")
    except Exception as exc:
        return f"earnings_report: error ({exc})"

async def _tool_predict_next_candle(symbol: str) -> str:
    # Uses short-term momentum to give a rough mathematical probability for the next candle
    try:
        from crawler.zerodha_market import get_live_prices
        data = await get_live_prices([symbol])
        if not data or not data.get(symbol):
            return "predict_next_candle: Need live LTP for ML prediction."
        chg = data[symbol].get("change_pct", 0.0)
        prob_bull = min(max(50 + (chg * 10), 10), 90) # Simple momentum-based probability mapping
        return f"predict_next_candle: Current Momentum = {chg}%. Probability of NEXT CANDLE being GREEN is ~{prob_bull:.1f}%. Probability of RED is ~{100-prob_bull:.1f}%."
    except Exception as exc:
        return f"predict_next_candle: error ({exc})"

async def _tool_screener_deep(symbol: str) -> str:
    return await _tool_fundamentals(symbol)

async def _tool_expert_research(symbol: str) -> str:
    # Fetches recent news labeled as 'Brokerage' or research reports
    try:
        from db.database import AsyncSessionLocal
        from db.models import NewsItem
        from sqlalchemy import select
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(select(NewsItem).where(NewsItem.headline.ilike(f"%{symbol.replace('.NS','')} %")).order_by(NewsItem.published_at.desc()).limit(2))).scalars().all()
        if not rows:
            return "expert_research: No major brokerage or expert upgrades/downgrades found recently."
        return "expert_research: " + " | ".join([f"[{r.published_at.strftime('%d-%b')}] {r.headline} (Sentiment: {r.sentiment})" for r in rows])
    except Exception as exc:
        return f"expert_research: error ({exc})"


async def _tool_intraday_candles(symbol: str) -> str:
    try:
        from crawler.zerodha_market import get_kite_historical
        from datetime import datetime, timedelta
        now = datetime.now()
        start = now - timedelta(days=2)
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            candles = await get_kite_historical(symbol, start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), "15minute", session=session)
        if not candles:
            return "intraday_candles: live data unavailable"
            
        last_5 = candles[-5:]
        res = "intraday_candles (last five 15-min bars): "
        for c in last_5:
            res += f"[{c['timestamp'].strftime('%H:%M')}] O:{c['open']} H:{c['high']} L:{c['low']} C:{c['close']} V:{c['volume']} | "
        return res
    except Exception as exc:
        return f"intraday_candles: error ({exc})"


def _fmt_stmt_point(d: dict | None) -> str:
    """income_statement/cash_flow points look like {"value", "period", "change"}."""
    if not d or d.get("value") is None:
        return "n/a"
    s = f"{d['value']}"
    if d.get("period"):
        s += f"@{d['period']}"
    if d.get("change"):
        s += f"({d['change']})"
    return s


def _fmt_ownership_trend(d: dict | None) -> str:
    if not d or d.get("latest_pct") is None:
        return "n/a"
    chg = d.get("change_qoq")
    if chg is None:
        return f"{d['latest_pct']}%"
    arrow = "↑" if chg > 0 else ("↓" if chg < 0 else "→")
    return f"{d['latest_pct']}% ({arrow}{abs(chg)}pp QoQ)"


async def _tool_company_intelligence(symbol: str) -> str:
    """Level-3 agentic tool (Phase U3): normalized Upstox-primary company
    intelligence — financial statements, corporate actions, ownership trend,
    competitors — via engine/company_intelligence.py's single aggregator
    rather than exposing eight separate Upstox endpoints as eight separate
    tools (that would let the model pit "Upstox says X" against "yfinance
    says Y" itself, recreating the reconciliation problem the aggregator
    exists to resolve upstream). Always reports completeness/failed_sections
    so the model can tell "no cash-flow data exists for this company" apart
    from "the enrichment layer happened to be down right now"."""
    try:
        from engine.company_intelligence import get_company_intelligence
        ci = await get_company_intelligence(symbol)
        meta = ci["metadata"]
        lines = [f"company_intelligence (status={meta['status']}, completeness={meta['completeness']})"]

        identity = ci.get("identity")
        if identity:
            if identity.get("sector"):
                lines.append(f"sector={identity['sector']}")
            desc = identity.get("business_description")
            if desc:
                lines.append(f"business: {desc[:220]}{'...' if len(desc) > 220 else ''}")

        fs = ci.get("financial_statements")
        if fs:
            lines.append(
                f"financials ({fs['units']}): revenue={_fmt_stmt_point(fs.get('revenue'))} "
                f"op_profit={_fmt_stmt_point(fs.get('operating_profit'))} "
                f"net_profit={_fmt_stmt_point(fs.get('net_profit'))} "
                f"op_cash_flow={_fmt_stmt_point(fs.get('operating_cash_flow'))} "
                f"total_assets={fs.get('total_assets')} total_liabilities={fs.get('total_liabilities')}"
            )
        else:
            lines.append("financials: UNAVAILABLE (no fallback source exists for statement-level data)")

        val, qual = ci.get("valuation"), ci.get("quality")
        if val or qual:
            parts = []
            if val:
                parts.append(f"PE={val.get('pe')} PB={val.get('pb')} MktCap={val.get('market_cap_cr')}Cr")
            if qual:
                parts.append(
                    f"ROE={qual.get('roe')}% ROCE={qual.get('roce')}% "
                    f"D/E={qual.get('debt_to_equity')} CurrentRatio={qual.get('current_ratio')}"
                )
            lines.append("ratios: " + " | ".join(parts))

        own = ci.get("ownership")
        if own:
            lines.append(
                f"ownership: promoter={_fmt_ownership_trend(own.get('promoter'))} "
                f"fii={_fmt_ownership_trend(own.get('fii'))} dii={_fmt_ownership_trend(own.get('dii'))} "
                f"public={_fmt_ownership_trend(own.get('public'))}"
            )

        events = ci.get("corporate_events")
        if events:
            ev_strs = [f"{e.get('name')} (ex-date {e.get('expiry_date')}, amt {e.get('amount')})" for e in events[:3]]
            lines.append("corporate_events: " + "; ".join(ev_strs))

        comp = ci.get("competitors")
        if comp:
            lines.append(f"competitors: {len(comp)} peers identified in {comp[0].get('sector')} sector")

        if meta["failed_sections"]:
            lines.append(f"NOTE: unavailable this call = {meta['failed_sections']}")

        return " | ".join(lines)
    except Exception as exc:
        return f"company_intelligence: error ({exc})"


_LLM_TOOLS = {
    "fundamentals":         _tool_fundamentals,
    "news":                 _tool_news,
    "options":              _tool_options,
    "price_action":         _tool_price_action,
    "market_depth":         _tool_market_depth,
    "intraday_candles":     _tool_intraday_candles,
    "sector":               _tool_sector_analysis,
    "macro":                _tool_macro_environment,
    "earnings":             _tool_earnings_report,
    "predict_candle":       _tool_predict_next_candle,
    "screener_deep":        _tool_screener_deep,
    "expert_research":      _tool_expert_research,
    "company_intelligence": _tool_company_intelligence,
}


_NEWS_AUTHORITY_TOOLS = ("news", "expert_research")


# Deterministic provenance backstop for _check_grounding(). A first attempt at
# this check used an LLM judge alone and was demonstrably too lenient in
# testing: a verdict citing "Options data (PCR 0.75, max-pain 1350)" and
# "Macro shows net FII inflow" was scored grounded=True even though `options`
# and `macro` were never called that session — there is categorically no
# source for those numbers, but the judge model didn't catch it. Whether a
# claim TYPE requires a specific tool is a fact about this codebase, not a
# judgment call, so it belongs in a fixed table, not another model's opinion —
# same reasoning engine/event_classifier.py::validate_evidence_consistency()
# already applies to its own (narrower) case.
_PROVENANCE_ONLY_TERMS: dict[str, tuple[str, ...]] = {
    "options": ("pcr", "max pain", "max-pain", "open interest", "call oi", "put oi", "option chain"),
    "macro":   ("fii inflow", "fii outflow", "dii inflow", "dii outflow", "net fii", "net dii",
                "fii buying", "fii selling", "dii buying", "dii selling"),
    "earnings": ("earnings beat", "earnings miss", "guidance raised", "guidance cut", "eps beat"),
}
# Claim types no tool in this system provides AT ALL, regardless of what was
# called this session — analyst ratings/price targets aren't sourced anywhere.
_NO_SOURCE_TERMS = (
    "analyst upgrade", "analyst downgrade", "rating upgrade", "rating downgrade",
    "brokerage upgrade", "brokerage downgrade", "target price raised", "target price cut",
)


def _deterministic_provenance_check(claim_text: str, used_tools: list[str]) -> list[str]:
    text = claim_text.lower()
    violations = []
    for tool, terms in _PROVENANCE_ONLY_TERMS.items():
        if tool in used_tools:
            continue
        for term in terms:
            if term in text:
                violations.append(f'"{term}" cited but the `{tool}` tool was never called this session')
    for term in _NO_SOURCE_TERMS:
        if term in text:
            violations.append(f'"{term}" cited but no tool in this system provides analyst ratings/price targets')
    return violations


# Second deterministic backstop, added after the LLM semantic layer missed
# TWO separate invented catalysts in back-to-back live tests ("5G partnership",
# "regulatory approval for a renewable project", "earnings surprise (8% beat)"
# — none present in any tool output, all scored grounded=True by the judge
# model anyway). _deterministic_provenance_check only catches a claim TYPE
# tied to an uncalled tool; it says nothing about a freely-invented event
# narrative dressed up in confident language. A fixed vocabulary of discrete-
# event words is a second, narrower net: if the verdict names one of these
# event TYPES, that specific word must actually appear somewhere the model
# was legitimately given (candidate_context or a tool output this session) —
# not proof of truth, but a real, mechanical trip-wire for exactly the
# fabrication pattern observed twice so far.
_EVENT_CLAIM_TERMS = (
    "partnership", "joint venture", "merger", "acquisition", "acquire",
    "stake sale", "contract win", "order win", "regulatory approval",
    "approval from", "license", "licence", "patent", "litigation", "lawsuit",
    "penalty", "recall", "strike", "buyback", "delisting", "restructuring",
    "earnings surprise", "profit surge", "record profit",
)


def _deterministic_entity_overlap_check(claim_text: str, evidence_pool: str) -> list[str]:
    text, pool = claim_text.lower(), evidence_pool.lower()
    return [
        f'"{term}" cited but does not appear in any tool output or candidate context this session'
        for term in _EVENT_CLAIM_TERMS
        if term in text and term not in pool
    ]


# Third deterministic backstop — catches a DISTORTED figure, not just an
# invented one. Neither check above would flag a model citing a real
# category (cash flow, revenue) with a number that doesn't match what was
# actually retrieved: a verdict claiming "1.2 L cr" cash flow when the real
# tool output said 192113 crore (~1.92 L cr, ~37% off) uses a real word
# ("cash flow") that IS in the evidence, so layer 2 passes it; and it's not
# tied to an uncalled tool, so layer 1 passes it too. Only comparing the
# actual number catches this.
_LAKH_CRORE_RE   = re.compile(r"(\d+(?:\.\d+)?)\s*(?:l\.?|lakh)\s*cr", re.IGNORECASE)
_CRORE_RE        = re.compile(r"(\d+(?:\.\d+)?)\s*cr(?:ore)?s?\b", re.IGNORECASE)
_LARGE_NUMBER_RE = re.compile(r"(?<![\d.])(\d{3,}(?:\.\d+)?)(?![\d])")   # bare numbers >= 100
_KV_RE           = re.compile(r"([a-zA-Z_]+)\s*=\s*(\d+(?:\.\d+)?)")     # e.g. "op_cash_flow=192113.0"

# A keyword that might appear in the model's prose near a claimed figure,
# mapped to the specific evidence label(s) it should be checked against.
# Built from the exact field names engine/company_intelligence.py's
# financial_statements section / _tool_company_intelligence's formatting
# emit — see _fmt_stmt_point call sites there. Kept narrow and specific
# rather than trying to be exhaustive; an unrecognized category falls back
# to the broad (weaker) magnitude-only comparison below.
_FIGURE_CATEGORY_LABELS: dict[str, tuple[str, ...]] = {
    "cash flow":         ("op_cash_flow", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow"),
    "operating profit":  ("op_profit", "operating_profit"),
    "net profit":        ("net_profit",),
    "profit":            ("net_profit", "operating_profit"),
    "revenue":           ("revenue",),
    "total assets":      ("total_assets",),
    "assets":            ("total_assets",),
    "total liabilities": ("total_liabilities",),
    "liabilities":       ("total_liabilities",),
    "debt":              ("total_liabilities",),
    "market cap":        ("market_cap_cr",),
}


def _extract_crore_claim_matches(text: str) -> list[tuple[float, int]]:
    """Currency figures the CLAIM explicitly labels as crore-denominated
    ('X cr' / 'X crore' / 'X lakh cr', where 1 lakh crore = 100,000 crore),
    paired with their position in `text` so the caller can look at nearby
    words to infer which financial-statement category a figure refers to.
    Deliberately narrow — only a figure the model itself frames as a rupee
    amount is checked; this must not fire on RSI/R:R/percentages/prices."""
    matches: list[tuple[float, int]] = []
    consumed: list[tuple[int, int]] = []
    for m in _LAKH_CRORE_RE.finditer(text):
        try:
            matches.append((float(m.group(1)) * 100_000, m.start()))
            consumed.append(m.span())
        except ValueError:
            pass
    for m in _CRORE_RE.finditer(text):
        if any(s <= m.start() <= e for s, e in consumed):
            continue   # don't double-count the trailing "cr" of an already-matched "X lakh cr"
        try:
            matches.append((float(m.group(1)), m.start()))
        except ValueError:
            pass
    return matches


def _extract_reference_numbers(text: str) -> list[float]:
    """Any number >= 100 appearing anywhere in the evidence pool — used only
    as the broad fallback comparison set when no specific category can be
    inferred. Evidence-side numbers don't need a currency label to be
    trustworthy; they came from an actual tool output, not the model."""
    out = []
    for m in _LARGE_NUMBER_RE.finditer(text):
        try:
            out.append(float(m.group(1)))
        except ValueError:
            pass
    return out


def _extract_labeled_numbers(text: str) -> dict[str, float]:
    """key=value pairs from tool-output text, e.g. 'op_cash_flow=192113.0'."""
    out: dict[str, float] = {}
    for m in _KV_RE.finditer(text):
        try:
            out[m.group(1).lower()] = float(m.group(2))
        except ValueError:
            pass
    return out


def _nearby_category(claim_text: str, match_start: int, window: int = 40) -> tuple[str, ...] | None:
    """Picks the category keyword whose LAST occurrence in the window is
    closest to the number — not just the first one found in dict order.
    A window can legitimately contain more than one category keyword (e.g.
    "...cash flow (1.2 L cr), revenue growth of 1086181.0 crore..." — both
    "cash flow" and "revenue" appear before the second number), and the
    nearer one is what the figure actually refers to."""
    context = claim_text[max(0, match_start - window):match_start].lower()
    best_labels, best_pos = None, -1
    for keyword, labels in _FIGURE_CATEGORY_LABELS.items():
        idx = context.rfind(keyword)
        if idx > best_pos:
            best_pos, best_labels = idx, labels
    return best_labels


def _deterministic_numeric_consistency_check(claim_text: str, evidence_pool: str, tolerance: float = 0.25) -> list[str]:
    """Flags a claimed crore-figure with no matching retrieved number within
    `tolerance` relative distance.

    Category-aware where possible: if the words near the claimed figure name
    a recognized category ("cash flow", "revenue", ...), the figure is
    checked ONLY against that category's labeled evidence value(s) — not
    against every number in the pool. This matters in practice: a first,
    magnitude-only version of this check was tested against a real fabricated
    "1.2 L cr cash flow" claim (real op_cash_flow=192113, ~37% off) and MISSED
    it, because 120,000 happened to land within tolerance of the unrelated
    real op_profit=123162 figure — a coincidental collision between two
    genuinely different line items of similar scale, which is common in a
    single company's financial statements. Category-scoping avoids that.
    Falls back to the broad pool (weaker, magnitude-only) when no category is
    recognized or that category isn't present in the labeled evidence.

    A generous default tolerance (25%) is deliberate: this is a trip-wire for
    a clear mismatch, not an exact-match requirement that would flag ordinary
    rounding.
    """
    claim_matches = _extract_crore_claim_matches(claim_text)
    if not claim_matches:
        return []
    labeled = _extract_labeled_numbers(evidence_pool)
    pool_values = [v for v in _extract_reference_numbers(evidence_pool) if v > 0]
    if not pool_values:
        return []   # nothing to compare against — not this check's job to flag "no data"

    violations = []
    for cv, pos in claim_matches:
        if cv <= 0:
            continue
        category_labels = _nearby_category(claim_text, pos)
        candidates = (
            [labeled[lbl] for lbl in category_labels if lbl in labeled and labeled[lbl] > 0]
            if category_labels else []
        )
        compare_pool = candidates or pool_values
        best_rel_diff = min(abs(cv - pv) / pv for pv in compare_pool)
        if best_rel_diff > tolerance:
            scope = "for its stated category" if candidates else "anywhere in evidence"
            violations.append(
                f"figure ~{cv:.0f} crore cited but no retrieved figure {scope} is within "
                f"{int(tolerance * 100)}% of it (closest differs by {best_rel_diff * 100:.0f}%)"
            )
    return violations


async def _check_grounding(
    symbol: str, verdict_step: dict, tool_outputs: list[str], used_tools: list[str], candidate_context: str = "",
) -> dict:
    """Does the verdict's bull/bear/thesis/thought text cite specific factual
    claims (named catalysts, partnerships, flows, figures) that never
    appeared anywhere this candidate was legitimately given — either a tool
    output gathered THIS session, or the original candidate_context (entry/
    stop/target/strategy/hub_subscores/chart_brief/canonical event) it started
    from? Only the latter is "given"; a fact of neither type is fabricated.

    Three layers, not one — the first two are the reliable backbone, the third
    is best-effort:
      1. Provenance check (_deterministic_provenance_check) — catches claim
         TYPES that require a specific tool (options PCR/max-pain, FII/DII
         flows) when that tool was never called, or types (analyst ratings)
         no tool provides at all.
      2. Event-vocabulary overlap check (_deterministic_entity_overlap_check)
         — catches a fixed vocabulary of discrete-event words (partnership,
         acquisition, regulatory approval, earnings surprise, ...) cited
         without appearing anywhere in the tool outputs or candidate context.
         Added after the LLM layer below missed exactly this twice in back-
         to-back live tests ("5G partnership", "regulatory approval for a
         renewable project", "earnings surprise (8% beat)" — none present
         anywhere, all scored grounded=True by the judge model regardless).
      3. Numeric consistency check (_deterministic_numeric_consistency_check)
         — catches a DISTORTED figure for a real category, not just an
         invented one (e.g. claiming "1.2 L cr" cash flow when the tool
         output said 192113 crore, ~1.92 L cr, ~37% off) — a failure mode
         layers 1-2 can't catch since the word "cash flow" IS in evidence.
      4. LLM semantic check — catches whatever free-text fabrication neither
         fixed vocabulary anticipates. Demonstrated in testing to be lenient
         on its own — kept as a defense-in-depth pass, NOT the primary
         defense; layers 1-3 are what's actually reliable here.

    Fail-open on the LLM layer's OWN failure (error / bad JSON) — an inability
    to verify grounding is not the same as detected ungroundedness. The
    deterministic layers' findings are never discarded by an LLM-layer
    failure. Returns {"grounded": bool, "unsupported_claims": list[str]}.
    """
    claim_text = " ".join(str(verdict_step.get(k) or "") for k in ("bull", "bear", "thesis", "thought"))
    evidence_pool = f"{candidate_context}\n" + "\n".join(tool_outputs)
    deterministic_claims = (
        _deterministic_provenance_check(claim_text, used_tools)
        + _deterministic_entity_overlap_check(claim_text, evidence_pool)
        + _deterministic_numeric_consistency_check(claim_text, evidence_pool)
    )

    llm_claims: list[str] = []
    check_failed = False
    try:
        from utils.llm import call_llm_chat
        transcript = "\n".join(tool_outputs) if tool_outputs else "(no tool outputs gathered this session)"
        prompt = (
            f"Tools actually called this session: {used_tools}\n"
            f"Original candidate context given to the model before investigation "
            f"(entry/stop/target/strategy/scores/chart/canonical-event — these are "
            f"GIVEN facts, never unsupported even if not repeated in a tool output):\n"
            f"{candidate_context or '(none provided)'}\n\n"
            f"Tool outputs actually retrieved this session for {symbol}:\n{transcript}\n\n"
            f"Trade verdict text to check:\n{claim_text}\n\n"
            "List any SPECIFIC factual claims in the verdict text (named catalysts, "
            "partnerships, deals, analyst actions, capital flows, or figures) that are "
            "NOT stated or directly implied by EITHER the original candidate context OR "
            "the tool outputs above. Do NOT flag the candidate's own given entry/stop/"
            "target/strategy/scores — those are legitimate inputs, not claims to verify. "
            "If a claim's TYPE would normally come from a tool that was never called this "
            "session, it is automatically unsupported. General reasoning, opinion, or "
            "synthesis of the given numbers is fine — only flag concrete factual "
            "assertions that have no support above.\n"
            'Reply with ONLY a JSON object: {"unsupported_claims": ["..."], "grounded": true|false}'
        )
        resp = await call_llm_chat(
            [
                {"role": "system", "content": "You are a strict fact-checker. Output only the requested JSON, nothing else."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1024, temperature=0.0,
        )
        parsed = _parse_first_json(resp) or {}
        llm_claims = [c for c in (parsed.get("unsupported_claims") or []) if c]
    except Exception as exc:
        check_failed = True
        logger.debug(f"[agent/grounding_check] {symbol} LLM layer failed (fail-open on this layer only): {exc}")

    all_claims = deterministic_claims + llm_claims
    result = {"grounded": not all_claims, "unsupported_claims": all_claims}
    if check_failed:
        result["llm_layer_failed"] = True
    return result


async def llm_tooluse_candidate(symbol: str, candidate, decision) -> dict | None:
    """Level-3 agentic reasoning: give the LLM tools (news / options / fundamentals
    / price_action / market_depth) and let it INVESTIGATE before deciding.

    Phase 3 (canonical event -> decision-context binding): when candidate.evidence
    is already set, a canonical CausalEvent exists for this candidate (see
    news_discovery_engine.py::process_ticker() / _build_evidence()). In that case
    `news` and `expert_research` — the two tools that let the model fetch its own,
    unclassified news (live Google RSS, or a raw NewsItem-table query) — are removed
    from the tool menu entirely, not merely discouraged by prompt wording. Structural
    removal, not a prompt instruction: a model that tries anyway gets a BLOCKED
    observation, not a second source of "truth" to reason from. This closes the gap
    Phase 1/2's gate could only catch after the fact (BLOCKED_EVIDENCE_DRIFT) — the
    model no longer has a standing tool that could originate a contradictory thesis.

    For candidates with no canonical event (candidate.evidence is None — e.g. the
    TECHNICAL equity-scan callers in agent_loop.py/india_tasks.py, which never set
    .evidence), all tools remain available — this restriction only applies once a
    canonical event already exists to be reasoned over.
    """
    try:
        from utils.llm import call_llm_chat

        has_canonical_event = getattr(candidate, "evidence", None) is not None
        available_tools = {k: v for k, v in _LLM_TOOLS.items()
                            if not (has_canonical_event and k in _NEWS_AUTHORITY_TOOLS)}

        tool_table_rows = [
            "| `fundamentals` | Swing only: Need debt, promoter holding, growth. IGNORE for Intraday. |",
            "| `company_intelligence` | Swing/event only: revenue/profit/cash-flow scale, ownership trend (FII/DII/promoter direction, not just level), pending corporate actions (dividends/splits — ex-dates that move price independent of the thesis). Use when checking whether a news event's materiality is actually big relative to this company's financials. IGNORE for Intraday. |",
            "| `options` | Index or heavily traded stocks – check OI and PCR. |",
            "| `price_action` | Current LTP, daily trend, recent returns. |",
            "| `market_depth` | Order book – bid/ask imbalance, pending volumes (Critical for Intraday). |",
            "| `intraday_candles` | Short‑term 15‑minute bars for entry timing and intraday setups. |",
            "| `sector` | Sector performance relative to broader market. |",
            "| `macro` | FII/DII flows, overall market direction. |",
            "| `predict_candle` | Momentum‑based next‑candle probability (Crucial for Intraday). |",
        ]
        if not has_canonical_event:
            tool_table_rows.insert(1, "| `news` | Immediate catalysts, intraday spikes, sentiment. |")
        tool_table = "\n".join(tool_table_rows)

        # The "core tools" list the model is forced to use before it may decide —
        # swap `news` out for `options` when a canonical event already exists, so
        # the requirement never demands a tool that no longer exists.
        # company_intelligence joined this list (Phase U3) as the primary
        # structured company-intelligence source (financial statements,
        # ownership trend, corporate actions) the news-only architecture is
        # meant to lean on — not a de-prioritized optional alongside
        # earnings/macro/screener_deep.
        core_tools = ["fundamentals", "company_intelligence", "sector", "price_action", "market_depth", "intraday_candles"]
        core_tools.append("options" if has_canonical_event else "news")

        news_authority_notice = (
            "\n## Canonical Event Notice\n"
            "A canonical event already exists for this candidate (see CANONICAL_EVENT "
            "in the context below). The `news`/`expert_research` tools are NOT available "
            "to you for this candidate — you must reason over the given canonical event, "
            "not search for or substitute a different one.\n"
        ) if has_canonical_event else ""

        sys_prompt = f"""You are a senior NSE swing and intraday algorithmic trading analyst. Your task is to evaluate a candidate trade and output a final decision: TAKE or SKIP.

## Your Workflow (ReAct)

You must follow this cycle until you have enough evidence to decide:
1. **THINK** – Analyse what you already know and identify what additional information you need.
2. **ACT** – Call ONE tool to get that information.
3. **OBSERVE** – Review the tool output.
4. **REPEAT** – Continue until you have called ALL available tools to debate the facts before making your decision.
5. **DECIDE** – Output your final verdict with confidence and rationale.
{news_authority_notice}
## Available Tools and When to Use

| Tool | When to Use |
|------|-------------|
{tool_table}

**Important**: You MUST call ALL available tools before arriving at a decision. You are required to debate on data and facts using live data from every tool (including screener, fundamentals, etc.).

## Output Format

### During investigation (THINK + ACT):
```json
{{
  "thought": "<your reasoning about what you know and what you need next>",
  "action": "tool",
  "tool": "<tool_name>"
}}
```

### When ready to decide (ONLY after using all tools):
You MUST simulate a multi-agent debate inside the `thought` field before taking the final call.
```json
{{
  "thought": "SWING_AGENT: <argues if it's a good swing trade using tool proofs> | INTRADAY_AGENT: <argues if it's a good intraday trade using tool proofs> | FINAL_JUDGE: <takes all discussions/proofs and makes the final call>",
  "action": "decide",
  "verdict": "TAKE" or "SKIP",
  "confidence": <integer 0-100>,
  "bull": "<strongest bullish argument, max 20 words>",
  "bear": "<strongest bearish argument, max 20 words>",
  "key_risk": "<single biggest risk, max 12 words>",
  "thesis": "<if a CANONICAL_EVENT was given: state how it justifies this trade, in your own words, WITHOUT contradicting its category/materiality/direction. If no canonical event was given, your general investment thesis.>",
  "market_confirmation": "POSITIVE" or "NEGATIVE" or "NEUTRAL"
}}
```

## Concrete Decision Criteria

- **INTRADAY TRADING RULES:**
  - **IGNORE** PE ratio, PB, and long-term earnings completely.
  - Focus heavily on live `market_depth`, `intraday_candles`, and `predict_candle`.
  - **Embrace Short Selling:** If a stock has been going up for many days but is now showing weakness, bleeding sector momentum, or aggressive sellers in market depth, DO NOT HESITATE to TAKE a short (SELL) setup to profit from the fall.

- **SWING TRADING RULES (Multi-day):**
  - **AVOID EXTENDED RUNNERS:** If a stock has been going up continuously for many days (overbought), DO NOT BUY IT. It is prone to sudden profit-booking (falling from the top).
  - **Catch Fresh Moves:** Only TAKE trades on FRESH breakouts from a consolidation zone that are *just starting* to go up, supported by volume and short-term catalysts.
  - Ignore high PE if the fresh volume breakout and news catalyst are very strong.
  - Risk-to-reward ratio MUST be ≥ 2:1.
  - Look for sector momentum backing the technical breakout.

- **SKIP when:**
  - Intraday: Thin market depth (illiquid) or conflicting candle momentum.
  - Swing: Extended runner (overbought), no catalyst, negative earnings surprise, bad sector momentum, or **if recent daily price history/chart data is completely unavailable to verify the trend**.

## Critical Rules (Must Follow)
1. **Never** output more than one JSON per response.
2. **Never** call a tool without a `thought` explaining *why* you need it.
3. **Never** decide without sufficient investigation. You MUST use at least {len(core_tools)} core tools ({", ".join(core_tools)}) before you are allowed to output a verdict.
4. **Always** include your reasoning in `thought`.
5. **Embrace Intraday Shorts:** Do not hesitate to approve SELL side (shorting) setups if momentum is bleeding.

Now, based on the user's context below, follow your workflow and produce your final JSON output.
"""
        ctx0 = await _candidate_context(symbol, candidate, decision)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": ctx0},
        ]
        used: list[str] = []
        tool_outputs: list[str] = []
        grounding_retries = 0
        for _ in range(15):  # max 15 LLM rounds (≤14 tool calls + a decide)
            resp = await call_llm_chat(messages, max_tokens=32768, temperature=0.2)
            step = _parse_first_json(resp)
            if not step:
                return None
            if step.get("action") == "tool":
                tool = step.get("tool")
                messages.append({"role": "assistant", "content": resp})
                if tool in available_tools:
                    result = await available_tools[tool](symbol)
                    used.append(tool)
                    tool_outputs.append(f"[{tool}] {result}")
                    messages.append({"role": "user", "content": f"TOOL[{tool}] → {result}\nContinue or decide."})
                elif tool in _NEWS_AUTHORITY_TOOLS:
                    messages.append({"role": "user", "content": (
                        f"TOOL[{tool}] → BLOCKED: not available for this candidate — a canonical "
                        f"event already exists (see CANONICAL_EVENT above). Independent news search "
                        f"is not permitted; reason over the given event facts instead. "
                        f"Available tools: {list(available_tools)}"
                    )})
                else:
                    messages.append({"role": "user", "content": f"TOOL[{tool}] → unknown tool. Available tools: {list(available_tools)}"})
                continue
            if step.get("action") == "decide" or "verdict" in step:
                # Force the agent to actually call every CORE tool by name. The
                # previous check (len(set(used)) < 5) only counted how many
                # DISTINCT tools were used — any 5 satisfied it, so a model
                # could skip fundamentals/company_intelligence entirely as
                # long as it called 5 of something else, despite the prompt
                # claiming those specific tools were required. This checks
                # identity, not count.
                missing_core = [t for t in core_tools if t not in used]
                if missing_core:
                    messages.append({"role": "assistant", "content": resp})
                    messages.append({"role": "user", "content": f"You have not met the minimum tool requirement. You must still call: {', '.join(missing_core)}. Continue investigating."})
                    continue

                # Hallucination-vs-tool-output grounding check: does bull/bear/
                # thesis/thought cite a specific fact never returned by any tool
                # this session? Give the model ONE chance to self-correct; a
                # second ungrounded verdict rejects the candidate outright
                # (fail-closed) rather than silently accepting a known-fabricated
                # thesis — matching Flow B's existing fail-closed reasoning gate.
                grounding = await _check_grounding(symbol, step, tool_outputs, used, ctx0)
                if not grounding["grounded"]:
                    if grounding_retries >= 1:
                        logger.info(
                            f"[agent/llm_tooluse] {symbol} rejected — ungrounded claims "
                            f"persisted after retry: {grounding['unsupported_claims']}"
                        )
                        return None
                    grounding_retries += 1
                    messages.append({"role": "assistant", "content": resp})
                    messages.append({"role": "user", "content": (
                        "GROUNDING CHECK FAILED: your verdict asserts claims not supported "
                        f"by any tool output gathered this session: {grounding['unsupported_claims']}. "
                        "Revise your verdict to rely only on facts actually returned above "
                        "(call an additional tool first if you need to verify a claim)."
                    )})
                    continue

                step["tools_used"] = used
                step["grounding"] = grounding
                return step
        return None  # ran out of rounds without deciding
    except Exception as exc:
        logger.debug(f"[agent/llm_tooluse] {symbol} tool-use failed: {exc}")
        return None


async def apply_reasoning_gate(symbol: str, candidate, decision):
    """Level-1 reasoning gate (opt-in via AGENT_LLM_REASONING_ENABLED).

    On a candidate that has already cleared the arithmetic threshold, let the LLM
    confirm/veto and blend confidence. Returns (decision_or_None, reject_reason).
    If the gate itself is disabled (AGENT_LLM_REASONING_ENABLED=false), the
    arithmetic decision passes through unchanged. If the gate is enabled but the
    LLM call fails/times out, this now fails CLOSED: the candidate is rejected
    rather than allowed through un-reviewed.
    """
    if not getattr(settings, "AGENT_LLM_REASONING_ENABLED", False):
        return decision, None

    # Dispatch by level: tool-use (L3) > debate (L2) > single-pass reasoning (L1).
    if getattr(settings, "AGENT_LLM_TOOLUSE_ENABLED", False):
        mode, data = "tooluse", await llm_tooluse_candidate(symbol, candidate, decision)
    elif getattr(settings, "AGENT_LLM_DEBATE_ENABLED", False):
        mode, data = "debate", await llm_debate_candidate(symbol, candidate, decision)
    else:
        mode, data = "reason", await llm_reason_candidate(symbol, candidate, decision)
    if not data:
        candidate.reasons.append(f"llm_{mode}:unavailable→veto")
        return None, "llm_reasoning_unavailable"

    arith_conf = decision.confidence   # snapshot BEFORE any blend
    verdict  = str(data.get("verdict", "TAKE")).upper()
    llm_conf = data.get("confidence")
    key_risk = str(data.get("key_risk", ""))[:80]
    bull     = str(data.get("bull", ""))[:120]
    bear     = str(data.get("bear", ""))[:120]

    # Capture the model's raw reasoning channel (gpt-oss) for this decision and
    # persist it, so the WHY behind every trade decision is auditable/visible.
    model_reasoning = None
    try:
        from utils.llm import get_last_reasoning, log_llm_reasoning
        model_reasoning = get_last_reasoning()
        if model_reasoning:
            await log_llm_reasoning(
                source="decision", symbol=symbol,
                prompt=f"{mode} gate: {getattr(candidate,'strategy','')} "
                       f"{getattr(decision,'action','')} @ conf {arith_conf}",
                content=f"verdict={verdict} conf={llm_conf} bull={bull} bear={bear} risk={key_risk}",
                reasoning=model_reasoning, model=settings.MANTLE_MODEL,
            )
    except Exception:
        pass

    record = {
        "mode": mode, "verdict": verdict, "confidence": llm_conf,
        "bull": bull, "bear": bear, "key_risk": key_risk,
    }
    if model_reasoning:
        record["model_reasoning"] = model_reasoning[:4000]
    if data.get("judge"):
        record["judge"] = str(data["judge"])[:160]
    if data.get("_panel"):
        record["panel"] = data["_panel"]
    if data.get("tools_used"):
        record["tools_used"] = data["tools_used"]
    try:
        decision.confidence_factors["llm_reasoning"] = record
    except Exception:
        pass
    decision.reasons.append(f"llm_{mode}:{verdict} conf={llm_conf} risk={key_risk}")
    candidate.reasons.append(f"llm_{mode}:{verdict} conf={llm_conf} risk={key_risk}")

    # Shadow mode: log the verdict but DON'T act on it — the trade proceeds either
    # way, so would-be-SKIPs still get real outcomes for an unbiased A/B. `taken` is
    # True (the trade IS taken); llm_verdict still records what the gate WOULD do.
    shadow = bool(getattr(settings, "AGENT_LLM_SHADOW_MODE", False))
    if shadow:
        record["shadow"] = True
        candidate.reasons.append("llm_shadow:logged_not_enforced")
        await _log_verdict(symbol, candidate, decision, mode, arith_conf,
                           verdict, llm_conf, decision.confidence, taken=True, record=record)
        return decision, None

    if verdict == "SKIP":
        await _log_verdict(symbol, candidate, decision, mode, arith_conf,
                           verdict, llm_conf, None, taken=False, record=record)
        return None, f"llm_reasoning_skip:{key_risk or 'weak_edge'}"

    # TAKE → blend arithmetic + LLM confidence (defensive parse).
    try:
        lc = int(llm_conf)
        if 0 <= lc <= 100:
            blended = int(round((decision.confidence + lc) / 2))
            decision.confidence = blended
            decision.confidence_factors["final_confidence_blended"] = blended
    except Exception:
        pass
    await _log_verdict(symbol, candidate, decision, mode, arith_conf,
                       verdict, llm_conf, decision.confidence, taken=True, record=record)
    return decision, None


async def _log_verdict(symbol, candidate, decision, mode, arith_conf,
                       verdict, llm_conf, final_conf, taken: bool, record: dict) -> None:
    """Append one row to reasoning_verdicts (self-contained session, fail-safe) so
    EVERY gate verdict — taken or skipped, either execution path — is captured and
    later joinable to the trade outcome. Never raises into the decision flow."""
    try:
        from db.database import AsyncSessionLocal
        from db.models import ReasoningVerdict

        def _int(x):
            try: return int(x)
            except Exception: return None

        # 7-factor Hub breakdown + modifiers at decision time → per-factor outcome
        # attribution later. Floats kept as-is; tone/bias kept for grouping.
        sub = getattr(candidate, "hub_subscores", {}) or {}
        cf  = getattr(decision, "confidence_factors", {}) or {}
        factors = {
            "technical":   sub.get("technical"),   "news":        sub.get("news"),
            "sector":      sub.get("sector"),      "macro":       sub.get("macro"),
            "earnings":    sub.get("earnings"),    "fundamental": sub.get("fundamental"),
            "options":     sub.get("options"),
            "news_factor": cf.get("news_factor"),  "earnings_tone": cf.get("earnings_tone"),
            "fii_bias":    cf.get("fii_bias"),     "regime_factor": cf.get("regime_factor"),
        }

        async with AsyncSessionLocal() as s:
            s.add(ReasoningVerdict(
                symbol=symbol, mode=mode,
                side=getattr(decision, "action", None) or getattr(candidate, "side", None),
                strategy=getattr(candidate, "strategy", None),
                regime=getattr(decision, "regime", None),
                entry=getattr(candidate, "entry", None),
                arith_confidence=_int(arith_conf),
                llm_verdict=verdict, llm_confidence=_int(llm_conf),
                final_confidence=_int(final_conf), taken=taken,
                key_risk=str(record.get("key_risk", ""))[:120] or None,
                detail=record, factors=factors,
            ))
            await s.commit()
    except Exception as exc:
        logger.debug(f"[agent/verdict_log] {symbol} log failed: {exc}")


class DecisionEngine:

    def fuse(
        self,
        symbol: str,
        candidate,
        regime: str,
        macro_bias: int,
        fund_score: int,
        fund_grade: str,
        equity: float,
    ) -> tuple["AgentDecisionOutput | None", "str | None"]:
        """Return (decision, None) on success or (None, reject_reason) when filtered."""

        if candidate is None:
            return None, "no_candidate"

        from engine.agent.risk_manager import capital_utilization_size

        # Apply regime-based position size reduction flag (set by fetch_hub_candidate)
        size_factor = getattr(candidate, "size_factor", 1.0)

        # Capital-utilization sizing: deploy toward a conviction-weighted target
        # (so the ₹20L is actually used) while keeping the per-trade risk guard,
        # the 20% per-position cap, and the cash buffer. `deployed_notional` is
        # passed by the caller so the cash-buffer room is respected portfolio-wide.
        deployed_notional = getattr(candidate, "deployed_notional", 0.0)
        conviction = abs(getattr(candidate, "master_score", None) or candidate.confidence)
        live_vix: float = 15.0
        try:
            from crawler.live_prices import PRICE_CACHE
            live_vix = float(PRICE_CACHE.get("^INDIAVIX", {}).get("price", 15) or 15)
        except Exception:
            pass
        qty, _size_reason = capital_utilization_size(
            equity, conviction, candidate.entry, candidate.stop,
            deployed_notional, size_factor=size_factor, vix=live_vix,
        )
        if qty <= 0:
            return None, f"qty_zero:{_size_reason}"
        from engine.agent.risk_manager import vix_size_factor as _vix_sf
        _vsf = _vix_sf(live_vix)
        if _vsf < 1.0:
            candidate.reasons.append(f"vix_scaled:{live_vix:.1f}→sf={_vsf:.2f}")

        risk_amt = qty * abs(candidate.entry - candidate.stop)
        risk_pct = risk_amt / max(equity, 1)

        # Varsity M12 — Innerworth: always check the opposing view
        bear = self._bear_case(candidate, regime, macro_bias)
        if bear:
            candidate.reasons.append(f"bear_case:{bear}")

        # ── Conflict detection ────────────────────────────────────────────────
        # Hard skips when hub context disagrees with the BUY signal.
        # Checked BEFORE confidence calculation so we never emit a low-conf order.
        if candidate.side == "BUY":
            conflict_reason = self._check_conflicts(symbol, candidate)
            if conflict_reason:
                candidate.reasons.append(conflict_reason)
                logger.info(
                    f"[agent/decision] {symbol} CONFLICT SKIP — {conflict_reason}"
                )
                return None, conflict_reason

        # ── Multiplicative confidence ─────────────────────────────────────────
        # Replaces the old additive hub modifier.
        bare = symbol.replace(".NS", "")
        raw_master = getattr(candidate, "master_score", None)
        if raw_master is not None:
            signal_strength = abs(raw_master) / 100.0
        else:
            signal_strength = candidate.confidence / 100.0

        regime_factor    = self._regime_factor(candidate.side, regime)
        news_factor      = 1.0
        earnings_factor  = 1.0
        fii_factor       = 1.0
        news_raw         = 0.0
        earnings_tone    = "NEUTRAL"
        fii_bias_val     = 0

        try:
            from engine import intelligence_hub as hub
            if hub.LAST_NEWS_CONTEXT is not None:
                news_raw    = hub.LAST_NEWS_CONTEXT.scores_by_symbol.get(bare, 0.0)
                news_factor = max(0.5, min(1.5, 1.0 + news_raw * 0.5))

            if hub.LAST_EARNINGS_CONTEXT is not None:
                earnings_tone  = hub.LAST_EARNINGS_CONTEXT.tones_by_symbol.get(bare, "NEUTRAL")
                earnings_bonus = {"OPTIMISTIC": 5, "NEUTRAL": 0, "CAUTIOUS": -10, "NEGATIVE": -20}.get(earnings_tone, 0)
                earnings_factor = max(0.5, min(1.5, 1.0 + earnings_bonus / 100.0))

            if hub.LAST_MACRO_CONTEXT is not None:
                fii_bias_val = hub.LAST_MACRO_CONTEXT.fii_bias
                fii_factor   = max(0.6, min(1.4, 1.0 + fii_bias_val * 0.2))

        except Exception as exc:
            logger.debug(f"[agent/decision] hub factors skipped for {symbol}: {exc}")

        market_support    = regime_factor * news_factor * earnings_factor * fii_factor
        final_confidence  = max(0, min(100, int(signal_strength * market_support * 100)))

        conf_factors = {
            "signal_strength":  round(signal_strength, 4),
            "regime_factor":    round(regime_factor, 4),
            "news_raw":         round(news_raw, 4),
            "news_factor":      round(news_factor, 4),
            "earnings_tone":    earnings_tone,
            "earnings_factor":  round(earnings_factor, 4),
            "fii_bias":         fii_bias_val,
            "fii_factor":       round(fii_factor, 4),
            "market_support":   round(market_support, 4),
            "final_confidence": final_confidence,
        }

        candidate.reasons.append(
            f"conf_multi:sig={signal_strength:.2f},regime={regime_factor:.2f},"
            f"news={news_factor:.2f},earn={earnings_factor:.2f},fii={fii_factor:.2f}"
            f"→{final_confidence}"
        )

        if final_confidence < settings.AGENT_CONFIDENCE_THRESHOLD:
            reject = f"confidence<threshold:{final_confidence}<{settings.AGENT_CONFIDENCE_THRESHOLD}"
            logger.debug(f"[agent/decision] {symbol} filtered: {reject}")
            return None, reject

        # NSE rule: short selling only allowed intraday (MIS). CNC delivery
        # shorts are rejected by Zerodha / SEBI.
        product = (
            "MIS"
            if candidate.strategy in _MIS_STRATEGIES or candidate.side == "SELL"
            else getattr(settings, "AGENT_DEFAULT_PRODUCT", "CNC")
        )

        decision = AgentDecisionOutput(
            symbol=symbol,
            action=candidate.side,
            confidence=final_confidence,
            regime=regime,
            strategy=candidate.strategy,
            entry=candidate.entry,
            stop=candidate.stop,
            target=candidate.target,
            qty=qty,
            risk_pct=round(risk_pct, 4),
            risk_reward=candidate.risk_reward,
            product=product,
            reasons=candidate.reasons,
            macro_bias=macro_bias,
            fund_score=fund_score,
            fund_grade=fund_grade,
            ts=datetime.utcnow().isoformat(),
            master_score=raw_master,
            confidence_factors=conf_factors,
        )
        logger.info(
            f"[agent/decision] {symbol} → {candidate.side} | conf={final_confidence}% "
            f"(sig={signal_strength:.2f}×support={market_support:.2f}) | {candidate.strategy}"
        )
        return decision, None

    @staticmethod
    def _regime_factor(side: str, regime: str) -> float:
        """Reduce confidence for counter-trend trades."""
        if side == "BUY"  and regime == "BEAR_TRENDING": return 0.7
        if side == "SELL" and regime == "BULL_TRENDING":  return 0.7
        return 1.0

    @staticmethod
    def _check_conflicts(symbol: str, candidate) -> str:
        """Return conflict reason string if BUY signal conflicts with hub context."""
        bare = symbol.replace(".NS", "")
        try:
            from engine import intelligence_hub as hub

            news_raw      = hub.LAST_NEWS_CONTEXT.scores_by_symbol.get(bare, 0.0) if hub.LAST_NEWS_CONTEXT else 0.0
            earnings_tone = hub.LAST_EARNINGS_CONTEXT.tones_by_symbol.get(bare, "NEUTRAL") if hub.LAST_EARNINGS_CONTEXT else "NEUTRAL"
            fii_bias      = hub.LAST_MACRO_CONTEXT.fii_bias if hub.LAST_MACRO_CONTEXT else 0

            hard: list[str] = []
            if news_raw < -0.3:
                hard.append(f"news_negative({news_raw:.2f})")
            if earnings_tone == "NEGATIVE":
                hard.append("earnings_NEGATIVE")
            if fii_bias <= -2:  # only hard-block on heavy FII selling (>₹2000cr/3d)
                hard.append(f"fii_bearish({fii_bias})")

            if hard:
                return f"conflict:{','.join(hard)}"

            # Soft check: two or more moderate negatives
            soft: list[str] = []
            if news_raw < 0:
                soft.append("news_mild")
            if earnings_tone in ("CAUTIOUS", "NEGATIVE"):
                soft.append("earnings_cautious")
            if fii_bias < 0:
                soft.append("fii_mild")

            if len(soft) >= 2:
                return f"conflict_soft:{','.join(soft)}"

        except Exception as exc:
            logger.debug(f"[agent/decision] conflict check skipped for {symbol}: {exc}")

        return ""

    @staticmethod
    def _bear_case(candidate, regime: str, macro_bias: int) -> str:
        """Varsity M12: document the opposing case before committing."""
        if candidate.side == "BUY":
            if regime == "BEAR_TRENDING": return "STRONG:buying_into_bear_trend"
            if macro_bias <= -2:          return "STRONG:macro_headwind"
        else:
            if regime == "BULL_TRENDING": return "STRONG:shorting_bull_trend"
            if macro_bias >= 2:           return "STRONG:macro_tailwind"
        return ""


# ── Hub 7-Factor Override ─────────────────────────────────────────────────────

async def fetch_hub_candidate(
    symbol: str,
    features,
    session,
) -> "TradeCandidate | None":
    """Query master_intelligence_scores for a fresh 7-factor score.

    Returns a TradeCandidate built from the Hub master_score if:
      - A row exists scored within the last 2 hours
      - abs(master_score) >= AGENT_CONFIDENCE_THRESHOLD
      - Symbol is not blocked (is_blocked=False)
      - For SELL signals: EQUITY_SHORT_ENABLED must be True
      - Regime restriction passes (HIGH_VOL_RANGE blocked, BEAR+BUY needs reversal)
      - No hard conflict between master_score direction and news/earnings/fii

    Sets candidate.size_factor=0.5 for RANGE/LOW_VOL_RANGE regimes.
    """
    from db.models import MasterIntelligenceScore
    from sqlalchemy import select as _sel
    from engine.agent.strategies.base import TradeCandidate

    threshold = settings.AGENT_CONFIDENCE_THRESHOLD
    cutoff    = datetime.utcnow() - timedelta(hours=2)
    regime    = features.regime

    bare = symbol.replace(".NS", "")
    try:
        row = (await session.execute(
            _sel(MasterIntelligenceScore)
            .where(
                MasterIntelligenceScore.symbol.in_([bare, symbol]),
                MasterIntelligenceScore.scored_at >= cutoff,
                MasterIntelligenceScore.is_blocked == False,
            )
            .order_by(MasterIntelligenceScore.scored_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    except Exception as exc:
        logger.debug(f"[hub_override] DB query failed for {symbol}: {exc}")
        return None

    if row is None:
        return None

    master_score = row.master_score
    if abs(master_score) < threshold:
        logger.debug(
            f"[hub_override] {symbol} score={master_score:.1f} below threshold {threshold}"
        )
        return None

    side = "BUY" if master_score > 0 else "SELL"

    # ── Short-selling gate ────────────────────────────────────────────────────
    if side == "SELL" and not getattr(settings, "EQUITY_SHORT_ENABLED", False):
        logger.debug(f"[hub_override] {symbol} SELL skipped — EQUITY_SHORT_ENABLED=False")
        return None

    # ── Regime restriction ────────────────────────────────────────────────────
    if regime == "HIGH_VOL_RANGE":
        reason = "regime:HIGH_VOL_RANGE_blocks_all"
        logger.info(f"[hub_override] {symbol} SKIP — {reason}")
        await _log_hub_rejection(symbol, master_score, regime, reason, 0, session)
        return None

    if regime == "BEAR_TRENDING" and side == "BUY":
        # Allow BUY only when a reversal pattern is detected:
        # price closes above EMA20 after having made a new lower low
        reversal = (features.close > features.ema20 and
                    features.low < features.swing_low_20)
        if not reversal:
            reason = "regime:BEAR_TRENDING_no_reversal"
            logger.info(
                f"[hub_override] {symbol} SKIP — {reason} "
                f"(close={features.close:.2f} ema20={features.ema20:.2f})"
            )
            await _log_hub_rejection(symbol, master_score, regime, reason, 0, session)
            return None

    entry = features.close
    atr   = features.atr14
    if entry <= 0 or atr <= 0:
        return None

    if side == "BUY":
        stop   = round(entry - 2.0 * atr, 2)
        target = round(entry + 4.0 * atr, 2)
    else:
        stop   = round(entry + 1.0 * atr, 2)
        target = round(entry - 2.0 * atr, 2)

    # Position size reduction flag for range regimes; shorts always half-size
    size_factor = 0.5 if regime in ("RANGE", "LOW_VOL_RANGE") else 1.0
    if side == "SELL":
        size_factor *= 0.5
    if size_factor < 1.0:
        logger.info(
            f"[hub_override] {symbol} {regime} → size_factor=0.5 (50% position)"
        )

    # Build sub-score breakdown for reasons
    reasons = [
        f"hub_7factor:score={master_score:.1f}",
        f"technical={row.technical_score:.1f}",
        f"news={row.news_score:.1f}",
        f"sector={row.sector_score:.1f}",
        f"macro={row.macro_score:.1f}",
        f"earnings={row.earnings_score:.1f}",
        f"fundamental={row.fundamental_score:.1f}",
        f"options={row.options_score:.1f}",
        f"hub_signal:{row.signal}",
        f"regime:{regime}",
    ]
    if size_factor < 1.0:
        reasons.append(f"size_reduced:range_regime")

    # Confidence starts at raw master_score magnitude; fuse() will apply
    # the multiplicative model on top of this.
    confidence = min(int(abs(master_score)), 90)

    logger.info(
        f"[hub_override] {symbol} → {side} | score={master_score:.1f} "
        f"conf={confidence}% | signal={row.signal} | regime={regime} "
        f"| scored_at={row.scored_at.isoformat()}"
    )

    candidate = TradeCandidate(
        symbol=symbol,
        side=side,
        entry=round(entry, 2),
        stop=stop,
        target=target,
        confidence=confidence,
        reasons=reasons,
        strategy="HUB_7FACTOR",
        size_factor=size_factor,
        master_score=master_score,
        regime=row.regime or regime,  # carry real regime through to Telegram alerts
        hub_subscores={
            "technical":   row.technical_score,
            "news":        row.news_score,
            "sector":      row.sector_score,
            "macro":       row.macro_score,
            "earnings":    row.earnings_score,
            "fundamental": row.fundamental_score,
            "options":     row.options_score,
            "signal":      row.signal,
            "regime":      row.regime or regime,
            "reasoning":   row.reasoning or {},
            "scored_at":   row.scored_at.isoformat(),
        },
    )
    return candidate


async def _log_hub_rejection(
    symbol: str,
    master_score: float,
    regime: str,
    drop_reason: str,
    final_confidence: int,
    session,
) -> None:
    """Persist a rejected hub candidate to agent_decisions before dropping."""
    try:
        from db.models import AgentDecision
        db_dec = AgentDecision(
            symbol=symbol,
            action="SKIP",
            confidence=final_confidence,
            regime=regime,
            strategy="HUB_7FACTOR",
            entry=None, stop=None, target=None,
            qty=0,
            risk_pct=0.0,
            reasons=[],
            macro_bias=0,
            fund_score=0,
            skip_reason=drop_reason,
            master_score=master_score,
            confidence_factors=None,
            is_paper=settings.AGENT_PAPER_MODE,
            order_id=None,
        )
        session.add(db_dec)
        await session.commit()
    except Exception as exc:
        logger.debug(f"[hub_override] rejection log failed for {symbol}: {exc}")
