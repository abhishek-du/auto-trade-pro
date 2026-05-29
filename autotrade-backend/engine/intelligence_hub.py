"""Master Intelligence Hub — unifies every data source into one decision layer.

Reads from existing caches/tables (FII/DII, VIX, breadth, sectors, news,
earnings, options, fundamentals, portfolio doctor) and produces a single
ranked score per NSE symbol each cycle. Writes nothing new at the data layer —
it calls existing engine/crawler functions.

Public API
----------
build_master_context(portfolio, session)  -> MasterContext
score_symbol(symbol, df, ctx, session)     -> ScoredStock
score_universe(symbols, ctx, session)      -> list[ScoredStock]
persist_scores(scored, bar_time, session)
update_portfolio_doctor_cache(data)        / get_portfolio_doctor_flags()
LAST_MACRO_CONTEXT / LAST_NEWS_CONTEXT / LAST_EARNINGS_CONTEXT  (module caches)
"""
from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger

# ── Module-level caches (read by macro agent + decision engine + chat) ────────

LAST_MACRO_CONTEXT = None      # type: MacroContext | None
LAST_NEWS_CONTEXT = None       # type: NewsContext | None
LAST_EARNINGS_CONTEXT = None   # type: EarningsContext | None
LAST_BUILT_AT = None           # type: str | None
PORTFOLIO_DOCTOR_CACHE: dict = {}


def update_portfolio_doctor_cache(data: dict) -> None:
    PORTFOLIO_DOCTOR_CACHE.update(data)


def get_portfolio_doctor_flags() -> dict:
    return dict(PORTFOLIO_DOCTOR_CACHE)


_MOOD_BIAS = {
    "STRONGLY_BULLISH": 2, "BULLISH": 1, "NEUTRAL": 0,
    "BEARISH": -1, "STRONGLY_BEARISH": -2,
}


# ── Sector resolver ───────────────────────────────────────────────────────────

def _get_sector_for_symbol(symbol: str) -> str:
    from crawler.sector_data import SECTOR_DEFINITIONS
    clean = symbol.replace(".NS", "").replace(".BO", "")
    for sector_key, definition in SECTOR_DEFINITIONS.items():
        stocks = definition.get("stocks", [])
        if symbol in stocks or clean in stocks or f"{clean}.NS" in stocks:
            return sector_key
    FALLBACK = {
        "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
        "AXISBANK": "Banking", "KOTAKBANK": "Banking", "INDUSINDBK": "Banking",
        "BAJFINANCE": "Banking",
        "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
        "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy", "NTPC": "Energy",
        "POWERGRID": "Energy",
        "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma", "DIVISLAB": "Pharma",
        "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG", "DABUR": "FMCG",
        "MARUTI": "Auto", "TATAMOTORS": "Auto", "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto",
        "TATASTEEL": "Metals", "HINDALCO": "Metals", "JSWSTEEL": "Metals",
        "LT": "Infra", "ULTRACEMCO": "Infra",
        "BHARTIARTL": "Telecom",
    }
    return FALLBACK.get(clean, "GENERAL")


# ── Context dataclasses ───────────────────────────────────────────────────────

@dataclass
class MacroContext:
    fii_net_1d: float
    fii_net_3d: float
    fii_net_5d: float
    dii_net_3d: float
    fii_bias:   int
    dii_bias:   int
    india_vix:  float
    vix_label:  str
    vix_bias:   int
    advance_decline_ratio: float
    nse_market_mood:       str
    breadth_bias:          int
    total_macro_bias:      int


@dataclass
class SectorContext:
    sector_moods:     dict = field(default_factory=dict)
    sector_biases:    dict = field(default_factory=dict)
    rotating_into:    list = field(default_factory=list)
    rotating_out_of:  list = field(default_factory=list)
    strongest_sector: str = "UNKNOWN"
    weakest_sector:   str = "UNKNOWN"


@dataclass
class NewsContext:
    scores_by_symbol:    dict = field(default_factory=dict)
    headlines_by_symbol: dict = field(default_factory=dict)
    market_wide_score:   float = 0.0


@dataclass
class EarningsContext:
    tones_by_symbol:  dict = field(default_factory=dict)
    recent_summaries: dict = field(default_factory=dict)


@dataclass
class OptionsContext:
    nifty_pcr:      float = 1.0
    nifty_max_pain: float = 0.0
    nifty_bias:     int = 0
    bank_nifty_pcr: float = 1.0


@dataclass
class PortfolioContext:
    equity:              float = 0.0
    cash:                float = 0.0
    cash_pct:            float = 0.0
    open_position_count: int = 0
    open_symbols:        list = field(default_factory=list)
    sector_exposure:     dict = field(default_factory=dict)
    health_score:        int = 70
    health_grade:        str = "B"
    overweight_sectors:  list = field(default_factory=list)
    concentration_flags: list = field(default_factory=list)


@dataclass
class MasterContext:
    built_at:  str
    bar_time:  str
    macro:     MacroContext
    sectors:   SectorContext
    news:      NewsContext
    earnings:  EarningsContext
    options:   OptionsContext
    portfolio: PortfolioContext

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class ScoredStock:
    symbol:         str
    master_score:   float
    signal:         str
    regime:         str
    is_blocked:     bool
    blocked_reason: str | None
    reasoning:      dict
    features:       object = None
    fund_grade:     str = "WATCHLIST"


# ── Context builders ──────────────────────────────────────────────────────────

async def build_macro_context(session: AsyncSession) -> MacroContext:
    from db.models import FIIDIIFlow
    from crawler.live_prices import PRICE_CACHE

    rows = (await session.execute(
        select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(5)
    )).scalars().all()

    fii_net_1d = rows[0].fii_net_buy if rows else 0.0
    fii_net_3d = sum(f.fii_net_buy for f in rows[:3]) if rows else 0.0
    fii_net_5d = sum(f.fii_net_buy for f in rows[:5]) if rows else 0.0
    dii_net_3d = sum(f.dii_net_buy for f in rows[:3]) if rows else 0.0

    if   fii_net_3d > 2000:  fii_bias = 2
    elif fii_net_3d > 500:   fii_bias = 1
    elif fii_net_3d < -2000: fii_bias = -2
    elif fii_net_3d < -500:  fii_bias = -1
    else:                    fii_bias = 0
    dii_bias = 1 if dii_net_3d > 500 else (-1 if dii_net_3d < -500 else 0)

    vix = float(PRICE_CACHE.get("^INDIAVIX", {}).get("price", 15.0) or 15.0)
    if   vix < 13: vix_label, vix_bias = "LOW", 1
    elif vix < 20: vix_label, vix_bias = "MODERATE", 0
    elif vix < 25: vix_label, vix_bias = "HIGH", -1
    else:          vix_label, vix_bias = "EXTREME", -2

    # Breadth cache is nested — prefer NSE section, fall back to watchlist
    mood, adr = "NEUTRAL", 1.0
    try:
        from crawler.market_breadth import get_breadth_cache
        bc = get_breadth_cache()
        nse = bc.get("nse") or {}
        wl  = bc.get("watchlist") or {}
        mood = nse.get("market_mood") or wl.get("market_mood") or "NEUTRAL"
        adr  = float(nse.get("ad_ratio") or wl.get("ad_ratio") or 1.0)
    except Exception as exc:
        logger.debug(f"[hub] breadth cache read failed: {exc}")

    breadth_bias = _MOOD_BIAS.get(mood, 0)
    total = max(-5, min(5, fii_bias + dii_bias + vix_bias + breadth_bias))

    return MacroContext(
        fii_net_1d=round(fii_net_1d, 1), fii_net_3d=round(fii_net_3d, 1),
        fii_net_5d=round(fii_net_5d, 1), dii_net_3d=round(dii_net_3d, 1),
        fii_bias=fii_bias, dii_bias=dii_bias,
        india_vix=round(vix, 2), vix_label=vix_label, vix_bias=vix_bias,
        advance_decline_ratio=round(adr, 2), nse_market_mood=mood,
        breadth_bias=breadth_bias, total_macro_bias=total,
    )


def build_sector_context() -> SectorContext:
    from crawler.sector_data import get_sector_cache
    cache = get_sector_cache()

    moods, biases = {}, {}
    for sector_key, data in cache.items():
        mood = (data or {}).get("mood", "NEUTRAL")
        moods[sector_key]  = mood
        biases[sector_key] = _MOOD_BIAS.get(mood, 0)

    rotating_into   = [k for k, v in biases.items() if v >= 1]
    rotating_out_of = [k for k, v in biases.items() if v <= -1]
    strongest = max(biases, key=biases.get) if biases else "UNKNOWN"
    weakest   = min(biases, key=biases.get) if biases else "UNKNOWN"

    return SectorContext(
        sector_moods=moods, sector_biases=biases,
        rotating_into=rotating_into, rotating_out_of=rotating_out_of,
        strongest_sector=strongest, weakest_sector=weakest,
    )


async def build_news_context(session: AsyncSession) -> NewsContext:
    from db.models import NewsItem

    cutoff = datetime.utcnow() - timedelta(hours=24)
    items = (await session.execute(
        select(NewsItem).where(NewsItem.published_at >= cutoff)
        .order_by(desc(NewsItem.published_at)).limit(500)
    )).scalars().all()

    raw_scores: dict[str, list] = {}
    headlines: dict[str, list] = {}
    for item in items:
        for ticker in (item.tickers_affected or []):
            sym = ticker if str(ticker).endswith(".NS") else f"{ticker}.NS"
            if item.score is not None:
                raw_scores.setdefault(sym, []).append(item.score)
            headlines.setdefault(sym, []).append(item.headline or "")

    avg = {s: sum(v) / len(v) for s, v in raw_scores.items() if v}
    market_wide = sum(avg.values()) / len(avg) if avg else 0.0

    return NewsContext(
        scores_by_symbol={k: round(v, 4) for k, v in avg.items()},
        headlines_by_symbol={k: v[:3] for k, v in headlines.items()},
        market_wide_score=round(market_wide, 4),
    )


async def build_earnings_context(session: AsyncSession) -> EarningsContext:
    from db.models import EarningsCallSummary

    cutoff = datetime.utcnow() - timedelta(days=90)
    rows = (await session.execute(
        select(EarningsCallSummary).where(EarningsCallSummary.created_at >= cutoff)
        .order_by(desc(EarningsCallSummary.created_at))
    )).scalars().all()

    tones, recent = {}, {}
    for s in rows:
        sym = s.symbol if s.symbol.endswith(".NS") else f"{s.symbol}.NS"
        if sym not in tones:  # most-recent only
            tones[sym] = s.management_tone or "NEUTRAL"
            recent[sym] = {
                "quarter":          s.quarter,
                "tone":             s.management_tone,
                "revenue_guidance": s.revenue_guidance,
                "margin_guidance":  s.margin_guidance,
                "key_risks":        (s.key_risks or [])[:2],
            }
    return EarningsContext(tones_by_symbol=tones, recent_summaries=recent)


async def build_options_context(session: AsyncSession) -> OptionsContext:
    from db.models import OptionsChainSnapshot

    def _bias_from_pcr(pcr: float) -> int:
        # High PCR (>1.3) = heavy puts = contrarian-bullish/fear; low (0.7-1.3 band) = neutral.
        # pcr <= 0 means no/garbage snapshot → neutral, not a signal.
        if pcr <= 0:    return 0
        if pcr >= 1.3:  return 1
        if pcr <= 0.7:  return -1
        return 0

    async def _latest(sym: str):
        return (await session.execute(
            select(OptionsChainSnapshot)
            .where(OptionsChainSnapshot.symbol == sym)
            .order_by(desc(OptionsChainSnapshot.snapshot_at)).limit(1)
        )).scalar_one_or_none()

    nifty = await _latest("NIFTY")
    bank  = await _latest("BANKNIFTY")

    n_pcr = float(nifty.pcr) if nifty else 1.0
    n_mp  = float(nifty.max_pain) if nifty else 0.0
    b_pcr = float(bank.pcr) if bank else 1.0

    return OptionsContext(
        nifty_pcr=round(n_pcr, 2), nifty_max_pain=n_mp,
        nifty_bias=_bias_from_pcr(n_pcr), bank_nifty_pcr=round(b_pcr, 2),
    )


async def build_portfolio_context(agent_portfolio, session: AsyncSession) -> PortfolioContext:
    from db.models import PortfolioDiagnosis

    health_score, health_grade = 70, "B"
    overweight, conc_flags = [], []

    diag = (await session.execute(
        select(PortfolioDiagnosis).order_by(desc(PortfolioDiagnosis.created_at)).limit(1)
    )).scalar_one_or_none()
    if diag:
        health_score = diag.overall_score
        health_grade = diag.overall_grade
        for f in (diag.findings or []):
            sev = f.get("severity", "")
            mod = f.get("module", "")
            if sev == "CRITICAL" and mod == "CONCENTRATION":
                conc_flags.extend(f.get("stocks", []))
            if mod == "SECTOR_TIMING" and "WARNING" in sev:
                sec = (f.get("metric") or {}).get("sector", "")
                if sec:
                    overweight.append(sec)

    equity = max(agent_portfolio.equity, 1.0)
    sector_exposure: dict = {}
    for sym, pos in agent_portfolio.open_positions.items():
        sector = _get_sector_for_symbol(sym)
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + (
            pos["entry"] * pos["qty"] / equity * 100
        )

    # Merge doctor cache flags if present
    dc = get_portfolio_doctor_flags()
    if dc.get("concentration_flags"):
        conc_flags = list(set(conc_flags) | set(dc["concentration_flags"]))
    if dc.get("overweight_sectors"):
        overweight = list(set(overweight) | set(dc["overweight_sectors"]))

    return PortfolioContext(
        equity=agent_portfolio.equity, cash=agent_portfolio.cash,
        cash_pct=round(agent_portfolio.cash / equity * 100, 1),
        open_position_count=len(agent_portfolio.open_positions),
        open_symbols=list(agent_portfolio.open_positions.keys()),
        sector_exposure={k: round(v, 1) for k, v in sector_exposure.items()},
        health_score=health_score, health_grade=health_grade,
        overweight_sectors=overweight, concentration_flags=conc_flags,
    )


async def build_master_context(agent_portfolio, session: AsyncSession) -> MasterContext:
    # NOTE: a single AsyncSession cannot serve concurrent queries, so these
    # DB-backed builders run sequentially (each is a fast indexed lookup).
    macro     = await build_macro_context(session)
    news      = await build_news_context(session)
    earnings  = await build_earnings_context(session)
    options   = await build_options_context(session)
    portfolio = await build_portfolio_context(agent_portfolio, session)
    sectors   = build_sector_context()  # sync, cache-only

    now = datetime.utcnow().isoformat()
    ctx = MasterContext(
        built_at=now, bar_time=now, macro=macro, sectors=sectors,
        news=news, earnings=earnings, options=options, portfolio=portfolio,
    )

    # Publish to module caches for macro agent / decision engine / chat
    global LAST_MACRO_CONTEXT, LAST_NEWS_CONTEXT, LAST_EARNINGS_CONTEXT, LAST_BUILT_AT
    LAST_MACRO_CONTEXT = macro
    LAST_NEWS_CONTEXT = news
    LAST_EARNINGS_CONTEXT = earnings
    LAST_BUILT_AT = now
    return ctx


# ── Scorer ────────────────────────────────────────────────────────────────────

_EARNINGS_SCORE = {"OPTIMISTIC": 30, "NEUTRAL": 0, "CAUTIOUS": -20, "NEGATIVE": -40}


async def score_symbol(symbol: str, df: pd.DataFrame, ctx: MasterContext, session: AsyncSession) -> ScoredStock:
    from engine.indicators import compute_indicators
    from engine.agent.analyzer import MarketAnalyzerAgent
    from engine.agent.fundamentals import FundamentalsAgent

    analyzer = MarketAnalyzerAgent()

    # 1. Technical (35%)
    signals = compute_indicators(df)
    technical_score = float(signals.composite_score or 0.0)
    try:
        features = analyzer.compute_features(df)
        regime = features.regime
    except Exception:
        features, regime = None, "UNKNOWN"

    # 2. News (15%)
    raw_news = ctx.news.scores_by_symbol.get(symbol, 0.0)
    news_score = max(-100, min(100, raw_news * 100))

    # 3. Sector (15%)
    sector = _get_sector_for_symbol(symbol)
    sector_bias = ctx.sectors.sector_biases.get(sector, 0)
    sector_score = max(-50, min(50, sector_bias * 25))

    # 4. Macro (10%)
    macro_score = max(-50, min(50, ctx.macro.total_macro_bias * 12))

    # 5. Earnings (10%)
    tone = ctx.earnings.tones_by_symbol.get(symbol, "NEUTRAL")
    earnings_score = _EARNINGS_SCORE.get(tone, 0)

    # 6. Fundamental (10%)
    try:
        fund_score, fund_grade = await FundamentalsAgent().get_cached_grade(symbol)
    except Exception:
        fund_score, fund_grade = 50, "WATCHLIST"
    fundamental_score = (fund_score - 50) * 1.0

    # 7. Options (5%) — index-wide bias applied lightly to every name
    options_score = ctx.options.nifty_bias * 15

    master_score = (
        technical_score   * 0.35 +
        news_score        * 0.15 +
        sector_score      * 0.15 +
        macro_score       * 0.10 +
        earnings_score    * 0.10 +
        fundamental_score * 0.10 +
        options_score     * 0.05
    )

    # Blocking + penalties
    is_blocked, blocked_reason = False, None
    sector_mood = ctx.sectors.sector_moods.get(sector, "NEUTRAL")

    if symbol in ctx.portfolio.concentration_flags:
        is_blocked, blocked_reason = True, "PORTFOLIO_CONCENTRATION_FLAG"
    elif symbol in ctx.portfolio.open_symbols:
        is_blocked, blocked_reason = True, "ALREADY_OPEN_POSITION"
    elif tone == "NEGATIVE":
        is_blocked, blocked_reason = True, "EARNINGS_NEGATIVE"
    elif sector_mood == "STRONGLY_BEARISH" and master_score > 0:
        is_blocked, blocked_reason = True, "SECTOR_STRONGLY_BEARISH"

    if sector in ctx.portfolio.overweight_sectors:
        master_score *= 0.7
        if not blocked_reason:
            blocked_reason = f"SECTOR_OVERWEIGHT:{sector}"

    # Doctor-flagged persistent losers / tax harvest
    dc = get_portfolio_doctor_flags()
    if symbol in dc.get("losers_to_exit", []):
        is_blocked, blocked_reason = True, "DOCTOR_FLAGGED_PERSISTENT_LOSER"

    if master_score >= 60:    signal = "STRONG_BUY"
    elif master_score >= 25:  signal = "BUY"
    elif master_score >= -25: signal = "NEUTRAL"
    elif master_score >= -60: signal = "SELL"
    else:                     signal = "STRONG_SELL"

    if symbol in dc.get("tax_harvest_symbols", []) and signal in ("SELL", "STRONG_SELL"):
        master_score -= 15

    reasoning = {
        "technical": round(technical_score, 1), "news": round(news_score, 1),
        "sector": round(sector_score, 1), "macro": round(macro_score, 1),
        "earnings": round(earnings_score, 1), "fundamental": round(fundamental_score, 1),
        "options": round(options_score, 1), "master": round(master_score, 1),
        "regime": regime, "sector_name": sector, "news_tone": tone,
        "sector_mood": sector_mood, "fund_grade": fund_grade,
        "is_blocked": is_blocked, "blocked_reason": blocked_reason,
        "headlines": ctx.news.headlines_by_symbol.get(symbol, []),
    }

    return ScoredStock(
        symbol=symbol, master_score=round(master_score, 2), signal=signal,
        regime=regime, is_blocked=is_blocked, blocked_reason=blocked_reason,
        reasoning=reasoning, features=features, fund_grade=fund_grade,
    )


async def score_universe(symbols: list, ctx: MasterContext, session: AsyncSession,
                         timeframe: str = "1h") -> list:
    """Score every symbol. Candle fetch is serialized on the shared session
    (a single AsyncSession cannot serve concurrent coroutines); scoring then
    runs in parallel since score_symbol() does not touch the DB session."""
    from crawler.price_feed import get_latest_candles

    # Phase 1: fetch candles sequentially (DB-bound, shared session)
    dfs: dict = {}
    for symbol in symbols:
        try:
            candles = await get_latest_candles(symbol, timeframe, 300, session)
            if not candles or len(candles) < 50:
                continue
            cs = sorted(candles, key=lambda c: c.timestamp)
            df = pd.DataFrame([{
                "open": float(c.open), "high": float(c.high), "low": float(c.low),
                "close": float(c.close), "volume": float(c.volume),
                "timestamp": c.timestamp,
            } for c in cs])
            df.set_index("timestamp", inplace=True)
            dfs[symbol] = df
        except Exception as exc:
            logger.debug(f"[hub] candle fetch failed for {symbol}: {exc}")

    # Phase 2: score in parallel (no session use inside score_symbol)
    sem = asyncio.Semaphore(10)

    async def score_one(symbol: str, df: pd.DataFrame):
        async with sem:
            try:
                return await score_symbol(symbol, df, ctx, session)
            except Exception as exc:
                logger.debug(f"[hub] score error on {symbol}: {exc}")
                return None

    results = await asyncio.gather(*[score_one(s, d) for s, d in dfs.items()])
    scored = [r for r in results if r is not None]
    scored.sort(key=lambda x: (not x.is_blocked, x.master_score), reverse=True)
    return scored


async def persist_scores(scored: list, bar_time: datetime, session: AsyncSession) -> None:
    from db.models import MasterIntelligenceScore

    for rank, s in enumerate(scored, start=1):
        r = s.reasoning
        session.add(MasterIntelligenceScore(
            symbol=s.symbol, bar_time=bar_time,
            technical_score=r["technical"], news_score=r["news"],
            sector_score=r["sector"], macro_score=r["macro"],
            earnings_score=r["earnings"], fundamental_score=r["fundamental"],
            options_score=r["options"], portfolio_score=0.0,
            master_score=s.master_score, rank=rank, signal=s.signal,
            regime=s.regime, reasoning=r, is_blocked=s.is_blocked,
            blocked_reason=s.blocked_reason,
        ))
    await session.commit()
    logger.info(f"[hub] persisted {len(scored)} scores for bar_time={bar_time}")
