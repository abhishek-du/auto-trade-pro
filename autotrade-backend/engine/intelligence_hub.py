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
    from utils.sector_cache import get_sector as _cache_get_sector

    clean = symbol.replace(".NS", "").replace(".BO", "")

    # 1. SECTOR_DEFINITIONS (sector_data.py explicit lists)
    for sector_key, definition in SECTOR_DEFINITIONS.items():
        stocks = definition.get("stocks", [])
        if symbol in stocks or clean in stocks or f"{clean}.NS" in stocks:
            return sector_key

    # 2. Persistent JSON cache + live yfinance fallback (covers all 9,600+ NSE symbols)
    return _cache_get_sector(clean)


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
    # Varsity Ch 16.5: market regime — momentum works only in uptrend.
    # Derived from total_macro_bias: BULL(≥2) | BEAR(≤-2) | NEUTRAL.
    nifty_regime:          str = "NEUTRAL"


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
    # Per-symbol option analytics (keyed by bare symbol, e.g. "RELIANCE").
    # Populated for any underlying with OptionContractSnapshot/IVHistory data —
    # indices always, single stocks when ENABLE_FNO. Empty → fall back to index.
    symbol_pcr:     dict = field(default_factory=dict)   # bare → PCR
    symbol_iv_rank: dict = field(default_factory=dict)   # bare → 0..100
    symbol_skew:    dict = field(default_factory=dict)   # bare → PE_IV - CE_IV (atm), pts
    symbol_bias:    dict = field(default_factory=dict)   # bare → -1/0/+1

    def score_for(self, bare: str) -> tuple[float, dict]:
        """Symbol-aware options score (≈[-20,+20]) + detail dict.

        Uses this symbol's own PCR/skew when available, else the index-wide
        nifty bias as a light market-level nudge (legacy behaviour).
        """
        if bare in self.symbol_bias:
            base = self.symbol_bias[bare] * 15
            skew = max(-5.0, min(5.0, self.symbol_skew.get(bare, 0.0) * 100))  # IV pts → score
            score = max(-20.0, min(20.0, base + skew))
            return score, {
                "source":  "symbol",
                "pcr":     round(self.symbol_pcr.get(bare, 0.0), 2),
                "iv_rank": round(self.symbol_iv_rank.get(bare, 0.0), 1),
                "skew":    round(self.symbol_skew.get(bare, 0.0), 4),
                "bias":    self.symbol_bias[bare],
            }
        return float(self.nifty_bias * 15), {"source": "index", "nifty_bias": self.nifty_bias}


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
class EventContext:
    """Upcoming market calendar events that affect agent risk decisions."""
    # Symbols with earnings results due within 5 trading days → pre-earnings caution
    earnings_in_5d:   dict = field(default_factory=dict)   # symbol → days_until
    # True when RBI MPC / Union Budget is within 7 calendar days
    macro_event_7d:   bool = False
    macro_event_name: str  = ""
    # True when current week has F&O expiry (Thursday)
    fo_expiry_this_week: bool = False
    # Sectors with active IPOs this week (liquidity drain risk)
    ipo_drain_sectors: list = field(default_factory=list)


@dataclass
class MFFlowContext:
    """Sector-level institutional flow signal derived from MF NAV trends."""
    # sector → bias: +1 = MF inflow (bullish), -1 = MF outflow (bearish), 0 = neutral
    sector_bias: dict = field(default_factory=dict)
    # sector → 5-day NAV change % for reasoning display
    sector_nav_change: dict = field(default_factory=dict)


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
    events:    EventContext    = field(default_factory=EventContext)
    mf_flows:  MFFlowContext   = field(default_factory=MFFlowContext)
    # symbol → fundamental_score (0–100), pre-loaded once from FundamentalData
    # so scoring 500 symbols needs ZERO live fundamental API calls.
    fundamentals_by_symbol: dict = field(default_factory=dict)
    # symbol → (profit_growth_3yr, revenue_growth_3yr) — used as earnings proxy
    # for symbols with no transcript in EarningsCallSummary.
    growth_by_symbol: dict = field(default_factory=dict)

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

    # Staleness guard: if FII data is >5 days old, treat as neutral — stale
    # data should not permanently block new entries.
    import datetime as _dt
    _fii_stale = rows and ((_dt.date.today() - rows[0].date).days > 5)
    if _fii_stale:
        logger.debug(f"[hub] FII data stale ({rows[0].date}), using neutral bias")
        fii_bias = 0
    elif fii_net_3d > 2000:  fii_bias = 2
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

    # Varsity Ch 16.5: infer market regime from Nifty 50 200-EMA
    # If Nifty 50 is below its 200-EMA, the market is in a downtrend (BEAR regime),
    # which suppresses momentum swing buys to protect capital.
    try:
        from sqlalchemy import text as _text
        _rows = (await session.execute(_text("""
            SELECT close FROM candles
            WHERE symbol = 'NIFTYBEES.NS' AND timeframe = '1d'
            ORDER BY timestamp DESC LIMIT 220
        """))).scalars().all()
        if len(_rows) >= 200:
            _closes = pd.Series(list(reversed(_rows)), dtype=float)
            ema200     = _closes.ewm(span=200, adjust=False).mean().iloc[-1]
            last_close = _closes.iloc[-1]
            nifty_regime = "BEAR" if last_close < ema200 else "BULL"
        else:
            nifty_regime = "BULL" if total >= 2 else ("BEAR" if total <= -2 else "NEUTRAL")
    except Exception as exc:
        logger.warning(f"[hub] Nifty EMA calc failed: {exc}")
        nifty_regime = "BULL" if total >= 2 else ("BEAR" if total <= -2 else "NEUTRAL")


    return MacroContext(
        fii_net_1d=round(fii_net_1d, 1), fii_net_3d=round(fii_net_3d, 1),
        fii_net_5d=round(fii_net_5d, 1), dii_net_3d=round(dii_net_3d, 1),
        fii_bias=fii_bias, dii_bias=dii_bias,
        india_vix=round(vix, 2), vix_label=vix_label, vix_bias=vix_bias,
        advance_decline_ratio=round(adr, 2), nse_market_mood=mood,
        breadth_bias=breadth_bias, total_macro_bias=total,
        nifty_regime=nifty_regime,
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

    # 7-day window: yfinance NSE articles are typically 2-5 days old;
    # same-day RSS items are always within range and still dominate the average.
    cutoff = datetime.utcnow() - timedelta(days=7)
    items = (await session.execute(
        select(NewsItem).where(NewsItem.published_at >= cutoff)
        .order_by(desc(NewsItem.published_at)).limit(2000)
    )).scalars().all()

    # tickers_affected stores full NSE symbols today (e.g. "INFY.NS") because
    # _build_india_name_map keys the lookup that way. The branches below stay
    # defensive against three other shapes we'd want to leave untouched:
    #   - bare equity tickers ("INFY")                              → append ".NS"
    #   - exchange-suffixed symbols ("INFY.NS", "INFY.BO")          → use as-is
    #   - non-equity tickers (indices "^NSEI", forex "USDINR=X", etc.) → use as-is
    _NON_EQUITY_SYMBOLS = {
        "USDINR", "EURINR", "GBPINR", "JPYINR",
        "USD", "EUR", "GBP", "JPY", "AUD", "CHF", "CAD",
        "NIFTY50", "SENSEX", "BANKNIFTY", "INDIAVIX",
    }
    raw_scores: dict[str, list] = {}
    headlines: dict[str, list] = {}
    for item in items:
        for ticker in (item.tickers_affected or []):
            t = str(ticker).strip().upper()
            if not t:
                continue
            if t.endswith(".NS") or t.endswith(".BO"):
                sym = t
            elif t.startswith("^") or "=" in t or t in _NON_EQUITY_SYMBOLS:
                sym = t
            else:
                sym = f"{t}.NS"
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


async def enrich_news_context_with_tavily(
    ctx_news: "NewsContext",
    hub_universe: list[str],
) -> "NewsContext":
    """Inject Tavily news scores for hub symbols that have no RSS/DB coverage.

    Called once per hub cycle AFTER build_news_context() completes.
    Mutates and returns a new NewsContext with the enriched data.
    Budget-safe: capped at 10 Tavily calls per invocation (10 credits).
    """
    try:
        from engine.tavily_enricher import enrich_missing_news
        from utils.config import settings

        if not getattr(settings, "tavily_available", False):
            return ctx_news

        enriched = await enrich_missing_news(
            symbol_list=hub_universe,
            existing_scores=ctx_news.scores_by_symbol,
            max_symbols=20,
        )
        if not enriched:
            return ctx_news

        new_scores = dict(ctx_news.scores_by_symbol)
        new_headlines = dict(ctx_news.headlines_by_symbol)
        for sym, (score, hl) in enriched.items():
            new_scores[sym] = round(score, 4)
            new_headlines[sym] = hl[:3]

        logger.info(
            f"[hub/tavily] news enriched {len(enriched)} symbols "
            f"previously at news_score=0"
        )
        return NewsContext(
            scores_by_symbol=new_scores,
            headlines_by_symbol=new_headlines,
            market_wide_score=ctx_news.market_wide_score,
        )
    except Exception as exc:
        logger.warning(f"[hub/tavily] news enrichment failed: {exc}")
        return ctx_news


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

    octx = OptionsContext(
        nifty_pcr=round(n_pcr, 2), nifty_max_pain=n_mp,
        nifty_bias=_bias_from_pcr(n_pcr), bank_nifty_pcr=round(b_pcr, 2),
    )

    # Per-symbol analytics from OptionContractSnapshot + IVHistory (Phase 2).
    # Best-effort: any failure leaves the maps empty → index-wide fallback.
    try:
        await _populate_symbol_options(octx, _bias_from_pcr, session)
    except Exception as exc:
        logger.debug(f"[hub/options] per-symbol enrichment skipped: {exc}")

    return octx


async def _populate_symbol_options(octx: "OptionsContext", bias_fn, session: AsyncSession) -> None:
    """Fill per-symbol PCR / IV-rank / skew / bias from the F&O analytics tables."""
    from db.models import OptionContractSnapshot, IVHistory
    from sqlalchemy import func as _f

    # Underlyings with a recent per-strike snapshot (last 2 days).
    cutoff = datetime.utcnow() - timedelta(days=2)
    unders = (await session.execute(
        select(OptionContractSnapshot.underlying)
        .where(OptionContractSnapshot.snapshot_at >= cutoff)
        .distinct()
    )).scalars().all()
    if not unders:
        return

    for under in unders:
        bare = under.replace(".NS", "").upper()
        # Latest snapshot batch for this underlying = max snapshot_at.
        last_at = (await session.execute(
            select(_f.max(OptionContractSnapshot.snapshot_at))
            .where(OptionContractSnapshot.underlying == under)
        )).scalar()
        if last_at is None:
            continue
        rows = (await session.execute(
            select(OptionContractSnapshot).where(
                OptionContractSnapshot.underlying == under,
                OptionContractSnapshot.snapshot_at == last_at,
            )
        )).scalars().all()
        if not rows:
            continue

        call_oi = sum(r.oi for r in rows if r.option_type == "CE")
        put_oi  = sum(r.oi for r in rows if r.option_type == "PE")
        pcr = round(put_oi / call_oi, 3) if call_oi > 0 else 0.0

        # ATM skew = PE_IV - CE_IV at the strike nearest spot.
        spot = rows[0].spot or 0.0
        atm = min({r.strike for r in rows}, key=lambda k: abs(k - spot), default=0.0)
        ce_iv = next((r.iv for r in rows if r.strike == atm and r.option_type == "CE" and r.iv), None)
        pe_iv = next((r.iv for r in rows if r.strike == atm and r.option_type == "PE" and r.iv), None)
        skew = round((pe_iv - ce_iv), 4) if (ce_iv and pe_iv) else 0.0
        atm_iv = round(((ce_iv or 0) + (pe_iv or 0)) / (int(bool(ce_iv)) + int(bool(pe_iv)) or 1), 4)

        # IV rank over trailing IVHistory (need ≥5 points to be meaningful).
        hist = (await session.execute(
            select(IVHistory.atm_iv).where(IVHistory.underlying == under)
        )).scalars().all()
        iv_rank = 50.0
        if len(hist) >= 5 and atm_iv > 0:
            lo, hi = min(hist), max(hist)
            iv_rank = round(100 * (atm_iv - lo) / (hi - lo), 1) if hi > lo else 50.0

        octx.symbol_pcr[bare]     = pcr
        octx.symbol_iv_rank[bare] = iv_rank
        octx.symbol_skew[bare]    = skew
        octx.symbol_bias[bare]    = bias_fn(pcr)


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


async def build_event_context(session: AsyncSession) -> EventContext:
    """Read upcoming market_events for next 7 days and build caution signals."""
    from datetime import date as _date, timedelta as _td
    from db.models import MarketEvent
    from sqlalchemy import and_

    today   = _date.today()
    in_7d   = today + _td(days=7)
    in_5d   = today + _td(days=5)

    try:
        rows = (await session.execute(
            select(MarketEvent).where(
                and_(MarketEvent.event_date >= today, MarketEvent.event_date <= in_7d)
            ).order_by(MarketEvent.event_date)
        )).scalars().all()
    except Exception as exc:
        logger.debug(f"[hub/events] query failed: {exc}")
        return EventContext()

    earnings_in_5d: dict[str, int] = {}
    macro_event_7d = False
    macro_event_name = ""
    fo_expiry_this_week = False
    ipo_drain_sectors: list[str] = []

    _MACRO_EVENT_TYPES = {"RBI_MPC", "BUDGET", "ECONOMIC_REVIEW", "FED_MEETING"}
    _SECTOR_MAP = {
        "bank": "Banking", "banking": "Banking",
        "tech": "IT", "software": "IT", "infosys": "IT",
        "pharma": "Pharma", "health": "Pharma",
        "fmcg": "FMCG", "consumer": "FMCG",
        "auto": "Auto", "automobile": "Auto",
        "energy": "Energy", "oil": "Energy", "power": "Energy",
        "metal": "Metals", "steel": "Metals",
        "infra": "Infra", "realty": "Infra",
    }

    for ev in rows:
        etype = (ev.event_type or "").upper()
        sym   = (ev.symbol or "").upper().replace(".NS", "")
        days_until = (ev.event_date - today).days

        if etype == "EARNINGS" and sym and days_until <= 5:
            ns_sym = f"{sym}.NS"
            earnings_in_5d[ns_sym] = min(earnings_in_5d.get(ns_sym, 99), days_until)

        elif etype in _MACRO_EVENT_TYPES and not macro_event_7d:
            macro_event_7d   = True
            macro_event_name = ev.title or etype

        elif etype in ("FO_EXPIRY", "DERIVATIVES_EXPIRY"):
            fo_expiry_this_week = True

        elif etype == "IPO":
            meta  = ev.event_metadata or {}
            name  = (ev.title or "").lower() + " " + (meta.get("sector", "")).lower()
            for kw, sector in _SECTOR_MAP.items():
                if kw in name and sector not in ipo_drain_sectors:
                    ipo_drain_sectors.append(sector)

    logger.debug(
        f"[hub/events] earnings_5d={len(earnings_in_5d)} macro={macro_event_7d} "
        f"fo_expiry={fo_expiry_this_week} ipo_sectors={ipo_drain_sectors}"
    )
    return EventContext(
        earnings_in_5d=earnings_in_5d,
        macro_event_7d=macro_event_7d,
        macro_event_name=macro_event_name,
        fo_expiry_this_week=fo_expiry_this_week,
        ipo_drain_sectors=ipo_drain_sectors,
    )


async def build_mf_flow_context(session: AsyncSession) -> MFFlowContext:
    """Compute sector-level MF flow signal from recent NAV changes.

    Maps MF scheme categories to equity sectors and returns a bias score
    per sector based on 5-day NAV momentum. Rising NAVs = institutional
    retail money flowing in = bullish for that sector.
    """
    from db.models import MutualFundNAV
    from sqlalchemy import func as _func

    _CAT_SECTOR: dict[str, str] = {
        "banking":        "Banking",   "bank":           "Banking",
        "financial":      "Banking",   "psu bank":       "Banking",
        "technology":     "IT",        "tech":           "IT",
        "information":    "IT",
        "pharma":         "Pharma",    "healthcare":     "Pharma",
        "fmcg":           "FMCG",      "consumption":    "FMCG",
        "auto":           "Auto",      "automobile":     "Auto",
        "infrastructure": "Infra",     "realty":         "Infra",
        "energy":         "Energy",    "power":          "Energy",
        "metal":          "Metals",    "commodities":    "Metals",
    }

    try:
        # Get latest NAV and its 5-day change% per scheme, grouped by category
        rows = (await session.execute(
            select(
                MutualFundNAV.category,
                _func.avg(MutualFundNAV.change_pct).label("avg_change_pct"),
                _func.count().label("n"),
            )
            .where(MutualFundNAV.change_pct != 0.0)
            .group_by(MutualFundNAV.category)
        )).all()
    except Exception as exc:
        logger.debug(f"[hub/mf_flows] query failed: {exc}")
        return MFFlowContext()

    sector_changes: dict[str, list[float]] = {}
    for cat, avg_pct, n in rows:
        cat_lower = (cat or "").lower()
        for kw, sector in _CAT_SECTOR.items():
            if kw in cat_lower:
                sector_changes.setdefault(sector, []).append(float(avg_pct or 0.0))
                break

    sector_bias: dict[str, int] = {}
    sector_nav_change: dict[str, float] = {}
    for sector, changes in sector_changes.items():
        avg = sum(changes) / len(changes)
        sector_nav_change[sector] = round(avg, 3)
        sector_bias[sector] = 1 if avg > 0.3 else (-1 if avg < -0.3 else 0)

    logger.debug(f"[hub/mf_flows] sector_bias={sector_bias}")
    return MFFlowContext(sector_bias=sector_bias, sector_nav_change=sector_nav_change)


async def build_master_context(
    agent_portfolio,
    session: AsyncSession,
    hub_universe: list[str] | None = None,
) -> MasterContext:
    # NOTE: a single AsyncSession cannot serve concurrent queries, so these
    # DB-backed builders run sequentially (each is a fast indexed lookup).
    macro     = await build_macro_context(session)
    news      = await build_news_context(session)
    earnings  = await build_earnings_context(session)
    options   = await build_options_context(session)
    portfolio = await build_portfolio_context(agent_portfolio, session)
    events    = await build_event_context(session)
    mf_flows  = await build_mf_flow_context(session)
    sectors   = build_sector_context()  # sync, cache-only

    # Tavily: inject real-time news for small-cap hub symbols with no RSS/DB
    # coverage. Capped at 10 calls (10 credits) so the monthly budget is safe.
    if hub_universe:
        news = await enrich_news_context_with_tavily(news, hub_universe)

    # Pre-load cached fundamental scores once (DB only — no live yfinance calls).
    # FundamentalData is keyed on the bare ticker (e.g. "RELIANCE").
    from db.models import FundamentalData
    fund_rows = (await session.execute(
        select(
            FundamentalData.symbol,
            FundamentalData.fundamental_score,
            FundamentalData.profit_growth_3yr,
            FundamentalData.revenue_growth_3yr,
        )
    )).all()
    fundamentals_by_symbol = {
        r.symbol.replace(".NS", ""): float(r.fundamental_score)
        for r in fund_rows if r.fundamental_score is not None
    }
    growth_by_symbol = {
        r.symbol.replace(".NS", ""): (
            float(r.profit_growth_3yr)  if r.profit_growth_3yr  is not None else None,
            float(r.revenue_growth_3yr) if r.revenue_growth_3yr is not None else None,
        )
        for r in fund_rows
        if r.profit_growth_3yr is not None or r.revenue_growth_3yr is not None
    }

    now = datetime.utcnow().isoformat()
    ctx = MasterContext(
        built_at=now, bar_time=now, macro=macro, sectors=sectors,
        news=news, earnings=earnings, options=options, portfolio=portfolio,
        events=events, mf_flows=mf_flows,
        fundamentals_by_symbol=fundamentals_by_symbol,
        growth_by_symbol=growth_by_symbol,
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


def _intraday_overlay(df_1m: "pd.DataFrame | None") -> tuple[float, dict]:
    """Compute an intraday score adjustment (−25 to +25) from 1m candles.

    The core technical_score this feeds into is computed from DAILY (1d)
    candles, which only get a new data point when today's session closes —
    so on its own it can't see a reversal happening intraday until tomorrow.
    This overlay is the only part of the score that reacts same-day.

    Resamples 1m bars to 5m and 15m, then evaluates:
      • 5m  RSI(9): oversold/overbought bias              (±5)
      • 15m RSI(9): confirms / dampens 5m reading         (±3)
      • 5m  momentum: close vs 12-bar-ago (≈1 h)          (±2)
      • Reversal: today's net move so far vs the most recent ~15-20 min move
        (±15) — fires specifically when they point in OPPOSITE directions
        (day has been up but the last few bars are rolling over, or vice
        versa), which is the actual definition of an intraday reversal, not
        just "RSI is extreme". This is the term that lets the score react to
        a reversal today instead of waiting for tomorrow's daily candle.

    Returns (adjustment, detail_dict).  Returns (0.0, {}) if fewer than 15
    1m bars are available (outside market hours / not enough data yet).
    """
    import math

    if df_1m is None or len(df_1m) < 15:
        return 0.0, {}

    def _resample(df: "pd.DataFrame", minutes: int) -> "pd.DataFrame":
        import pandas as _pd
        df2 = df.copy()
        df2.index = _pd.to_datetime(df2.index)
        rule = f"{minutes}min"
        agg = df2.resample(rule, closed="left", label="left").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        ).dropna(subset=["close"])
        return agg

    def _rsi(series: "pd.Series", period: int = 9) -> float:
        """Wilder RSI with shorter period (9) suited for intraday bars."""
        if len(series) < period + 1:
            return float("nan")
        delta = series.diff().dropna()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        avg_g = float(gain.iloc[:period].mean())
        avg_l = float(loss.iloc[:period].mean())
        for i in range(period, len(delta)):
            avg_g = (avg_g * (period - 1) + float(gain.iloc[i])) / period
            avg_l = (avg_l * (period - 1) + float(loss.iloc[i])) / period
        if avg_l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + avg_g / avg_l)

    try:
        df5  = _resample(df_1m, 5)
        df15 = _resample(df_1m, 15)
    except Exception:
        return 0.0, {}

    adj  = 0.0
    info: dict = {}

    # ── 5m RSI(9): needs 10 5m bars = 50 1m bars (available from ~10:05 IST) ──
    rsi_5 = _rsi(df5["close"]) if len(df5) >= 10 else float("nan")
    if not math.isnan(rsi_5):
        info["rsi_5m"] = round(float(rsi_5), 1)
        if rsi_5 <= 25:
            adj += 5.0
        elif rsi_5 <= 35:
            adj += 3.0
        elif rsi_5 <= 45:
            adj += 1.0
        elif rsi_5 >= 75:
            adj -= 5.0
        elif rsi_5 >= 65:
            adj -= 3.0
        elif rsi_5 >= 55:
            adj -= 1.0

    # ── 15m RSI(9): needs 10 15m bars = 150 1m bars (available from ~11:45 IST) ─
    rsi_15 = _rsi(df15["close"]) if len(df15) >= 10 else float("nan")
    if not math.isnan(rsi_15):
        info["rsi_15m"] = round(float(rsi_15), 1)
        if rsi_15 <= 30:
            adj += 3.0
        elif rsi_15 <= 40:
            adj += 1.5
        elif rsi_15 >= 70:
            adj -= 3.0
        elif rsi_15 >= 60:
            adj -= 1.5

    # ── 5m momentum: close vs 12 bars ago (~1 h) (±2 points) ─────────────
    if len(df5) >= 13:
        mom_pct = float((df5["close"].iloc[-1] / df5["close"].iloc[-13] - 1.0) * 100)
        info["mom_1h_pct"] = round(mom_pct, 2)
        if mom_pct > 1.0:
            adj += 2.0
        elif mom_pct > 0.3:
            adj += 1.0
        elif mom_pct < -1.0:
            adj -= 2.0
        elif mom_pct < -0.3:
            adj -= 1.0

    # ── Reversal: today's move-so-far vs the last ~15-20 min (±15 points) ──
    # day_move: open (first 1m bar today) → now. recent_move: ~4 bars of the
    # 5m series ago (≈15-20 min) → now. A reversal is specifically when these
    # DISAGREE in sign and the recent move is big enough to matter — e.g. the
    # stock was up all day but the last 15 min are rolling over hard. Scaled
    # by how strong the recent move is, capped at ±15.
    if len(df_1m) >= 2 and len(df5) >= 4:
        day_open = float(df_1m["open"].iloc[0])
        cur      = float(df_1m["close"].iloc[-1])
        day_move_pct = (cur - day_open) / day_open * 100 if day_open else 0.0
        recent_move_pct = float((df5["close"].iloc[-1] / df5["close"].iloc[-4] - 1.0) * 100)
        info["day_move_pct"] = round(day_move_pct, 2)
        info["recent_move_pct"] = round(recent_move_pct, 2)
        rev_score = 0.0
        if day_move_pct > 0.5 and recent_move_pct < -0.5:
            # day's been up, but rolling over now → bearish reversal
            rev_score = max(-15.0, recent_move_pct * 6.0)
        elif day_move_pct < -0.5 and recent_move_pct > 0.5:
            # day's been down, but bouncing now → bullish reversal
            rev_score = min(15.0, recent_move_pct * 6.0)
        if rev_score != 0.0:
            info["reversal_score"] = round(rev_score, 1)
            adj += rev_score

    adj = max(-25.0, min(25.0, adj))
    info["intraday_adj"] = round(adj, 1)
    return round(adj, 1), info


def _score_symbol_sync(
    symbol: str,
    df: "pd.DataFrame",
    ctx: "MasterContext",
    df_1m: "pd.DataFrame | None" = None,
    swing_mode: bool = False,
) -> "ScoredStock":
    """Pure CPU-bound scoring core — no DB session, no awaits.

    df_1m/swing_mode are positional (not keyword-only) so this can be dispatched
    via loop.run_in_executor(pool, _score_symbol_sync, symbol, df, ctx, df_1m, swing_mode)
    — run_in_executor only supports positional args, no kwargs.

    Split out from score_symbol() so score_universe() can dispatch it to a
    ProcessPoolExecutor for real multi-core parallelism (Ichimoku/ADX/EMA-ribbon/
    candlestick pattern detection is synchronous pandas math; asyncio.gather only
    overlaps I/O waits, so running this under asyncio alone is single-core-bound).
    Must stay a plain, module-level, picklable function — no closures.
    """
    from engine.indicators import compute_indicators
    from engine.agent.analyzer import MarketAnalyzerAgent

    analyzer = MarketAnalyzerAgent()

    # 1. Technical (35% default / 55% in swing_mode)
    signals = compute_indicators(df)
    # Varsity Ch 16 momentum defaults — overwritten in swing_mode block below
    momentum_12m_score: float = 0.0
    _ret_12m: float | None = None
    _nifty_regime: str = ctx.macro.nifty_regime
    if swing_mode:
        # Swing mode uses trend-following score (rewards RSI 45-75, BB breakouts,
        # ongoing MACD uptrend). Intraday overlay is irrelevant on daily candles.
        technical_score = float(signals.swing_composite_score or 0.0)
        _intraday_adj, _intraday_info = 0.0, {}
    else:
        technical_score = float(signals.composite_score or 0.0)
        # Intraday overlay: nudge technical score by up to ±25 based on 5m/15m RSI,
        # momentum, and same-day trend reversals. Active only when 1m candles are
        # available (market hours); zero outside hours.
        _intraday_adj, _intraday_info = _intraday_overlay(df_1m)
        technical_score = max(-100.0, min(100.0, technical_score + _intraday_adj))
    try:
        features = analyzer.compute_features(df)
        regime = features.regime
    except Exception:
        features, regime = None, "UNKNOWN"

    bare = symbol.replace(".NS", "")

    # 2. News/Sentiment (15%) — RSS/DB score; falls back to yfinance headlines scored by
    # FinBERT (keyword fallback if torch absent) for small caps with no RSS coverage.
    # Final fallback: use 3-year profit growth as a sentiment proxy for stocks with
    # zero media presence (common for PSUs, micro-caps, and defense/infra names).
    _has_news = symbol in ctx.news.scores_by_symbol
    raw_news = ctx.news.scores_by_symbol.get(symbol, 0.0)
    _news_source = "rss"
    if not _has_news:
        try:
            import yfinance as yf
            ticker_news = yf.Ticker(symbol).news or []
            if ticker_news:
                from engine.tavily_enricher import _score_headlines_finbert
                headlines_yf = [
                    (n.get("title") or n.get("content", ""))
                    for n in ticker_news[:5]
                ]
                raw_news = _score_headlines_finbert(headlines_yf)
                if raw_news != 0.0:
                    _has_news = True
                    _news_source = "yfinance"
                    logger.debug(f"[hub/yf_news] {symbol}: yfinance score={raw_news:+.2f}")
        except Exception:
            pass
    # Growth-based sentiment proxy — applied only when no news source has any data.
    # Rationale: consistently growing companies tend to attract positive media when
    # coverage finally appears; this prevents a perpetual 0% weight for small-caps.
    if not _has_news:
        _growth = ctx.growth_by_symbol.get(bare)
        pg = rg = None
        if _growth:
            pg, rg = _growth  # profit_growth_3yr, revenue_growth_3yr
        if pg is not None:
            if pg > 20 and (rg is None or rg > 10):
                raw_news, _news_source = 0.20, "growth_proxy"
            elif pg > 10:
                raw_news, _news_source = 0.10, "growth_proxy"
            elif pg < -10 or (rg is not None and rg < -5):
                raw_news, _news_source = -0.15, "growth_proxy"
            else:
                raw_news, _news_source = 0.05, "growth_proxy"
            _has_news = True
            logger.debug(f"[hub/growth_proxy] {symbol}: pg={pg:.1f}% rg={rg} → news_proxy={raw_news:+.2f}")
        elif bare in ctx.fundamentals_by_symbol:
            # Last resort: use fundamental quality score as a very weak sentiment proxy.
            # fund_score > 65 = quality company → slight positive; < 40 = weak → slight negative.
            fs = ctx.fundamentals_by_symbol[bare]
            if fs >= 65:
                raw_news, _news_source = 0.10, "fundamental_proxy"
            elif fs >= 50:
                raw_news, _news_source = 0.04, "fundamental_proxy"
            elif fs < 40:
                raw_news, _news_source = -0.08, "fundamental_proxy"
            else:
                raw_news, _news_source = 0.02, "fundamental_proxy"
            _has_news = True
            logger.debug(f"[hub/fund_proxy] {symbol}: fund_score={fs:.1f} → news_proxy={raw_news:+.2f}")
    news_score = max(-100, min(100, raw_news * 100))

    # 3. Sector (15%) — GENERAL/unmapped falls back to market breadth bias so
    # every hub symbol always carries a sector signal rather than being zeroed.
    # MF flow bias augments sector score: sustained NAV inflows to a sector MF
    # signal institutional retail money moving into that sector (+/- 10 pts).
    sector = _get_sector_for_symbol(symbol)
    if sector in ctx.sectors.sector_biases:
        sector_bias = ctx.sectors.sector_biases[sector]
    else:
        sector_bias = ctx.macro.breadth_bias  # market-wide proxy for unclassified stocks
    mf_sector_adj = ctx.mf_flows.sector_bias.get(sector, 0) * 10  # ±10 from MF flows
    sector_score = max(-50, min(50, sector_bias * 25 + mf_sector_adj))

    # ── Narrative Intelligence Boost (Eagle Eyes style) ───────────────────────
    # narrative_engine refreshes every 5 min from RSS + Telegram channels.
    # When a sector has strong thematic momentum (e.g. "India-Japan MoU → Auto"),
    # it receives a +10 to +25 bonus on top of the price-action sector score.
    try:
        from engine.narrative_engine import get_narrative_boost
        _narrative_boost = get_narrative_boost(sector)
        if _narrative_boost > 0:
            sector_score = min(50, sector_score + _narrative_boost)
            logger.debug(f"[hub] {symbol} narrative boost +{_narrative_boost} for sector={sector}")
    except Exception:
        pass  # Fail-open: narrative engine unavailable → no boost applied

    # 4. Macro (10%) — apply caution if RBI/Budget event within 7 days
    _macro_caution = -6 if ctx.events.macro_event_7d else 0
    macro_score = max(-50, min(50, ctx.macro.total_macro_bias * 12 + _macro_caution))

    # 5. Earnings (10%) — primary: EarningsCallSummary transcript NLP tone.
    # Fallback: derive tone from FundamentalData 3-year profit/revenue growth so
    # small-caps and PSUs without indexed transcripts still carry an earnings signal.
    _earnings_source = "transcript"
    tone = ctx.earnings.tones_by_symbol.get(symbol)
    if tone is None:
        _growth = ctx.growth_by_symbol.get(bare)
        pg = _growth[0] if _growth else None
        if pg is not None:
            if pg > 20:
                tone, _earnings_source = "OPTIMISTIC", "growth_proxy"
            elif pg > 0:
                tone, _earnings_source = "NEUTRAL",    "growth_proxy"
            else:
                tone, _earnings_source = "CAUTIOUS",   "growth_proxy"
            logger.debug(f"[hub/earn_proxy] {symbol}: pg={pg:.1f}% → {tone}")
        elif bare in ctx.fundamentals_by_symbol:
            # Fundamental quality score as earnings proxy when no growth data
            fs = ctx.fundamentals_by_symbol[bare]
            if fs >= 65:
                tone, _earnings_source = "OPTIMISTIC", "fundamental_proxy"
            elif fs >= 50:
                tone, _earnings_source = "NEUTRAL",    "fundamental_proxy"
            else:
                tone, _earnings_source = "CAUTIOUS",   "fundamental_proxy"
            logger.debug(f"[hub/earn_fund_proxy] {symbol}: fund_score={fs:.1f} → {tone}")
    if tone is None:
        tone = "NEUTRAL"
        _earnings_source = "default"
    earnings_score = _EARNINGS_SCORE.get(tone, 0)

    # 6. Fundamental (10%) — DB-cached score only (no live API call per symbol).
    # Neutral 50 when the symbol isn't in FundamentalData yet (weekly task fills it).
    fund_score = ctx.fundamentals_by_symbol.get(bare, 50.0)
    fund_grade = (
        "STRONG" if fund_score >= 70 else "GOOD" if fund_score >= 55
        else "WATCHLIST" if fund_score >= 40 else "WEAK"
    )
    fundamental_score = (fund_score - 50) * 1.0

    # 7. Options (5%) — symbol-aware when this name has its own F&O analytics
    # (PCR/IV-rank/skew); otherwise falls back to the index-wide nifty bias.
    options_score, _options_detail = ctx.options.score_for(bare)

    # Renormalize: factors with no real data get 0 weight so missing factors
    # don't dilute the ones that have a genuine signal.
    # Exceptions: sector always has a signal (known sector or market breadth proxy);
    # news is considered covered if Tavily, RSS, or yfinance contributed a score.
    _has_earnings = (tone != "NEUTRAL" or _earnings_source != "default")
    if swing_mode:
        # ── Varsity Ch 16.3: 12-month trailing momentum score ────────────────
        # Core of Varsity's Momentum Portfolio: rank by 1-year return.
        # df already has 300 daily candles for swing symbols.
        if len(df) >= 200:
            _ret_12m = float(df["close"].iloc[-1] / df["close"].iloc[-200] - 1) * 100
            if   _ret_12m >= 30: momentum_12m_score = 30.0
            elif _ret_12m >= 15: momentum_12m_score = 20.0
            elif _ret_12m >= 5:  momentum_12m_score = 10.0
            elif _ret_12m >= 0:  momentum_12m_score =  0.0
            elif _ret_12m >= -5: momentum_12m_score = -5.0
            else:                momentum_12m_score = -15.0

        # ── Varsity Ch 16.5: bear regime penalty ────────────────────────────
        # Momentum portfolios bleed heavily in down/choppy markets.
        # Apply a score haircut when the market regime is BEAR.
        _nifty_regime = ctx.macro.nifty_regime
        _regime_penalty = -20.0 if _nifty_regime == "BEAR" else 0.0

        # B13 note: these are RELATIVE weights — they sum to 1.12, not 1.0, and the
        # `_w = v/_total_w` line below normalises them, so the EFFECTIVE weights are
        # each value / 1.12 (e.g. technical is ~49%, not 55%). Behaviour is correct;
        # only read the post-normalisation values as the true factor weights.
        _w = {
            "technical":    0.55,
            "sector":       0.15,
            "momentum_12m": 0.05,
            "volume":       0.15,
            "macro":        0.10,
            "news":         0.12,
            "earnings":     0.0,
            "fundamental":  0.0,
            "options":      0.0,
        }
        _total_w = sum(_w.values())
        if _total_w > 0:
            _w = {k: v / _total_w for k, v in _w.items()}

        volume_surge = getattr(signals, "volume_surge", 0.0) or 0.0
        vol_score = min(100.0, max(-100.0, (volume_surge - 1.0) * 30.0)) if volume_surge > 0 else 0.0
        master_score = (
            technical_score    * _w["technical"] +
            sector_score       * _w["sector"] +
            momentum_12m_score * _w["momentum_12m"] +
            vol_score          * _w["volume"] +
            macro_score        * _w["macro"] +
            news_score         * _w["news"] +
            _regime_penalty
        )
    else:
        _w = {
            "technical":   0.65,
            "news":        0.12,
            "sector":      0.10,
            "macro":       0.10,
            "volume":      0.15,
            "earnings":    0.0,
            "fundamental": 0.0,
            "options":     0.0,
        }
        _total_w = sum(_w.values())
        if _total_w > 0:
            _w = {k: v / _total_w for k, v in _w.items()}
    
        volume_surge = getattr(signals, "volume_surge", 0.0) or 0.0
        vol_score = min(100.0, max(-100.0, (volume_surge - 1.0) * 30.0)) if volume_surge > 0 else 0.0
        master_score = (
            technical_score   * _w["technical"] +
            news_score        * _w["news"] +
            sector_score      * _w["sector"] +
            macro_score       * _w["macro"] +
            vol_score         * _w["volume"] +
            earnings_score    * _w["earnings"] +
            fundamental_score * _w["fundamental"] +
            options_score     * _w["options"]
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
    # elif swing_mode and ctx.macro.nifty_regime == "BEAR" and master_score > 0:
    #    # Disabled per user request: allow BUY trades even in BEAR regimes
    #    is_blocked, blocked_reason = True, "BEAR_REGIME_SWING_BLOCK"


    if sector in ctx.portfolio.overweight_sectors:
        master_score *= 0.7
        if not blocked_reason:
            blocked_reason = f"SECTOR_OVERWEIGHT:{sector}"

    # Doctor-flagged persistent losers / tax harvest
    dc = get_portfolio_doctor_flags()
    if symbol in dc.get("losers_to_exit", []):
        is_blocked, blocked_reason = True, "DOCTOR_FLAGGED_PERSISTENT_LOSER"

    # Pre-earnings caution: results due in ≤5 days → score penalty + flag.
    # We don't hard-block (open positions still need SELL signals), but we
    # reduce BUY conviction so fresh entries avoid earnings gap risk.
    _pre_earnings_days: int | None = ctx.events.earnings_in_5d.get(symbol)
    if _pre_earnings_days is not None:
        _penalty = 15 if _pre_earnings_days <= 2 else 8
        master_score -= _penalty
        if not blocked_reason and _pre_earnings_days <= 2:
            blocked_reason = f"EARNINGS_DUE_IN_{_pre_earnings_days}D"

    # F&O expiry week: elevated intraday volatility → modest score haircut
    if ctx.events.fo_expiry_this_week:
        master_score -= 5

    # IPO sector drain: active IPO in same sector can pull liquidity → minor penalty
    if sector in ctx.events.ipo_drain_sectors:
        master_score -= 5

    from utils.config import settings
    swing_thresh = getattr(settings, "SWING_CONFIDENCE_THRESHOLD", 40) if swing_mode else 60
    
    if master_score >= swing_thresh:    signal = "STRONG_BUY"
    elif master_score >= 25:  signal = "BUY"
    elif master_score >= -25: signal = "NEUTRAL"
    elif master_score >= -60: signal = "SELL"
    else:                     signal = "STRONG_SELL"

    if symbol in dc.get("tax_harvest_symbols", []) and signal in ("SELL", "STRONG_SELL"):
        master_score -= 15

    # ── Technical indicator snapshot for Telegram proofs ──────────────────────
    import math as _math
    def _nf(v):  # nan → None, else round to 2
        return None if (v is None or (isinstance(v, float) and _math.isnan(v))) else round(float(v), 2)

    tech_detail = {
        "rsi":              _nf(getattr(signals, "rsi", None)),
        "rsi_signal":       getattr(signals, "rsi_signal", ""),
        "macd":             _nf(getattr(signals, "macd", None)),
        "macd_signal":      _nf(getattr(signals, "macd_signal", None)),
        "macd_hist":        _nf(getattr(signals, "macd_histogram", None)),
        "macd_cross":       getattr(signals, "macd_cross", ""),
        "bb_position":      getattr(signals, "bb_position", ""),
        "ema_trend":        getattr(signals, "ema_trend", ""),
        "ema_20":           _nf(getattr(signals, "ema_20", None)),
        "ema_50":           _nf(getattr(signals, "ema_50", None)),
        "ema_200":          _nf(getattr(signals, "ema_200", None)),
        "adx":              _nf(getattr(signals, "adx", None)),
        "adx_direction":    getattr(signals, "adx_direction", ""),
        "adx_strength":     getattr(signals, "adx_trend_strength", ""),
        "stoch_k":          _nf(getattr(signals, "stoch_k", None)),
        "stoch_d":          _nf(getattr(signals, "stoch_d", None)),
        "stoch_signal":     getattr(signals, "stoch_signal", ""),
        "supertrend_dir":   getattr(signals, "supertrend_direction", ""),
        "ichimoku_signal":  getattr(signals, "ichimoku_signal", ""),
        "ichimoku_tenkan":  _nf(getattr(signals, "ichimoku_tenkan", None)),
        "ichimoku_kijun":   _nf(getattr(signals, "ichimoku_kijun", None)),
        "volume_surge":     _nf(getattr(signals, "volume_surge", None)),
        "composite_score":  _nf(signals.composite_score),
    }

    _growth_vals = ctx.growth_by_symbol.get(bare, (None, None))
    reasoning = {
        "technical": round(technical_score, 1), "news": round(news_score, 1),
        "sector": round(sector_score, 1), "macro": round(macro_score, 1),
        "earnings": round(earnings_score, 1), "fundamental": round(fundamental_score, 1),
        "options": round(options_score, 1), "master": round(master_score, 1),
        "regime": regime, "sector_name": sector, "news_tone": tone,
        "sector_mood": sector_mood, "fund_grade": fund_grade,
        "is_blocked": is_blocked, "blocked_reason": blocked_reason,
        # Varsity Ch 16: momentum portfolio additions
        "momentum_12m_score": round(momentum_12m_score, 1) if swing_mode else None,
        "momentum_12m_ret_pct": round(_ret_12m, 1) if (swing_mode and _ret_12m is not None) else None,
        "nifty_regime": ctx.macro.nifty_regime,
        "headlines": ctx.news.headlines_by_symbol.get(symbol, []),
        "active_weights": {k: round(v, 3) for k, v in _w.items()},
        "news_source":     _news_source,
        "earnings_source": _earnings_source,
        "profit_growth_3yr":  _growth_vals[0],
        "revenue_growth_3yr": _growth_vals[1],
        "tech_detail": tech_detail,
        "intraday": _intraday_info,
        "macro_detail": {
            "fii_net_3d":  round(ctx.macro.fii_net_3d, 1),
            "dii_net_3d":  round(ctx.macro.dii_net_3d, 1),
            "india_vix":   round(ctx.macro.india_vix, 2),
            "vix_label":   ctx.macro.vix_label,
            "fii_bias":    ctx.macro.fii_bias,
            "dii_bias":    ctx.macro.dii_bias,
            "breadth_bias": ctx.macro.breadth_bias,
        },
        "sector_detail": {
            "sector_name":  sector,
            "sector_bias":  round(sector_bias, 2),
            "sector_mood":  sector_mood,
        },
        "earnings_detail": {
            "tone":         tone,
            "has_data":     symbol in ctx.earnings.tones_by_symbol,
        },
        "fundamental_detail": {
            "fund_score":   round(fund_score, 1),
            "fund_grade":   fund_grade,
            "has_data":     bare in ctx.fundamentals_by_symbol,
        },
        "options_detail": _options_detail,
        "event_detail": {
            "pre_earnings_days":  _pre_earnings_days,
            "macro_event_7d":     ctx.events.macro_event_7d,
            "macro_event_name":   ctx.events.macro_event_name,
            "fo_expiry_week":     ctx.events.fo_expiry_this_week,
            "ipo_drain_sector":   sector in ctx.events.ipo_drain_sectors,
        },
        "mf_flow_detail": {
            "sector_bias":       ctx.mf_flows.sector_bias.get(sector, 0),
            "sector_nav_change": ctx.mf_flows.sector_nav_change.get(sector, 0.0),
        },
    }

    return ScoredStock(
        symbol=symbol, master_score=round(master_score, 2), signal=signal,
        regime=regime, is_blocked=is_blocked, blocked_reason=blocked_reason,
        reasoning=reasoning, features=features, fund_grade=fund_grade,
    )


async def score_symbol(
    symbol: str,
    df: "pd.DataFrame",
    ctx: "MasterContext",
    session: "AsyncSession",
    *,
    df_1m: "pd.DataFrame | None" = None,
    swing_mode: bool = False,
) -> "ScoredStock":
    """Public async API — unchanged signature (session kept for compatibility;
    the scoring core never touches it). Used directly by api/intelligence.py's
    single-symbol rescore endpoint. score_universe() bypasses this wrapper and
    calls _score_symbol_sync() through a process pool instead."""
    return _score_symbol_sync(symbol, df, ctx, df_1m=df_1m, swing_mode=swing_mode)


async def score_universe(symbols: list, ctx: MasterContext, session: AsyncSession,
                         timeframe: str = "1h") -> list:
    """Score every symbol. Candle fetch is serialized on the shared session
    (a single AsyncSession cannot serve concurrent coroutines); scoring then
    runs in parallel since score_symbol() does not touch the DB session."""
    from crawler.price_feed import get_latest_candles

    # Phase 0: fetch swing flags BEFORE candle loop so we can choose the right
    # timeframe per symbol. Swing symbols use daily candles (Zerodha Varsity Module 2:
    # swing trading is analysed on EOD/daily charts, not intraday bars).
    from db.models import HubUniverse
    from sqlalchemy import select as _sel
    swing_flags = {}
    try:
        if symbols:
            hub_rows = (await session.execute(
                _sel(HubUniverse.symbol, HubUniverse.is_swing).where(HubUniverse.symbol.in_(symbols))
            )).all()
            swing_flags = {r.symbol: r.is_swing for r in hub_rows}
    except Exception as exc:
        logger.debug(f"[hub] failed to fetch swing flags: {exc}")

    # Phase 1: fetch primary candles sequentially (DB-bound, shared session).
    # Swing symbols get 1d candles (trend context); non-swing get the requested timeframe.
    # Fallback chain: requested timeframe → 1h → 1d → skip.
    _FALLBACKS = {"5m": ["1h", "1d"], "1h": ["1d"], "15m": ["1h", "1d"], "1d": []}
    dfs: dict = {}
    for symbol in symbols:
        _is_swing = swing_flags.get(symbol, False)
        _tf = "1d" if _is_swing else timeframe
        _fallbacks = [] if _is_swing else _FALLBACKS.get(timeframe, ["1h", "1d"])
        try:
            candles = await get_latest_candles(symbol, _tf, 300, session)
            for fb in _fallbacks:
                if not candles or len(candles) < 50:
                    candles = await get_latest_candles(symbol, fb, 300, session)
                else:
                    break
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

    # Phase 1b: fetch today's 1m candles for the intraday overlay.
    # Only non-swing symbols get this — swing uses daily candles where 1m overlay is noise.
    # Uses up to 200 bars (≈3.3 h): 5m RSI(9) needs 50 bars (from ~10:05 IST),
    # 15m RSI(9) needs 150 bars (from ~11:45 IST), momentum needs 60 bars.
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _IST = _ZI("Asia/Kolkata")
    _today_open = _dt.datetime.now(_IST).replace(hour=9, minute=15, second=0, microsecond=0)
    _today_open_utc = _today_open.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    from db.models import Candle as _Candle

    dfs_1m: dict = {}
    for symbol in dfs:
        if swing_flags.get(symbol, False):
            continue  # swing symbols don't need intraday overlay
        try:
            rows = (await session.execute(
                _sel(_Candle)
                .where(
                    _Candle.symbol == symbol,
                    _Candle.timeframe == "1m",
                    _Candle.timestamp >= _today_open_utc,
                )
                .order_by(_Candle.timestamp.asc())
                .limit(200)
            )).scalars().all()
            if len(rows) >= 15:
                df1m = pd.DataFrame([{
                    "open": float(r.open), "high": float(r.high),
                    "low": float(r.low), "close": float(r.close),
                    "volume": float(r.volume), "timestamp": r.timestamp,
                } for r in rows])
                df1m.set_index("timestamp", inplace=True)
                dfs_1m[symbol] = df1m
        except Exception:
            pass

    if dfs_1m:
        logger.debug(f"[hub] intraday overlay active for {len(dfs_1m)}/{len(dfs)} symbols")

    # Phase 2: score across a process pool for real multi-core parallelism.
    # score_symbol's underlying work (Ichimoku/ADX/EMA-ribbon/candlestick pattern
    # detection) is synchronous pandas/numpy compute — asyncio.gather alone only
    # overlaps I/O waits, so a plain semaphore here ran everything on one core
    # (measured: ~0.45s/symbol × ~1700 symbols ≈ 13 min, the actual reason this
    # cycle was blowing its 15-min schedule / 18-min soft time limit).
    #
    # MUST use billiard, not stdlib multiprocessing/concurrent.futures. This
    # task runs inside a celery prefork worker, which is itself a daemonic
    # process — stdlib multiprocessing hard-refuses to let a daemonic process
    # spawn children ("daemonic processes are not allowed to have children"),
    # and that refusal is identical for fork AND spawn start methods (confirmed
    # in production: both failed 1732/1732 with this exact error). billiard is
    # celery's own multiprocessing fork that specifically removes this
    # restriction — it's already a celery dependency, used internally for
    # celery's own worker pool.
    import os
    from billiard.pool import Pool as _BilliardPool
    from utils.config import settings as _settings

    loop = asyncio.get_running_loop()
    max_workers = max(1, min(int(getattr(_settings, "HUB_SCORE_WORKERS", 2)), os.cpu_count() or 2))

    def _dispatch(pool: "_BilliardPool", symbol: str, df: pd.DataFrame) -> "asyncio.Future":
        fut = loop.create_future()

        def _ok(result):
            if not fut.done():
                loop.call_soon_threadsafe(fut.set_result, result)

        def _err(exc):
            if not fut.done():
                loop.call_soon_threadsafe(fut.set_exception, exc)

        pool.apply_async(
            _score_symbol_sync,
            (symbol, df, ctx, dfs_1m.get(symbol), swing_flags.get(symbol, False)),
            callback=_ok, error_callback=_err,
        )
        return fut

    async def score_one(pool, symbol: str, df: pd.DataFrame):
        try:
            return await _dispatch(pool, symbol, df)
        except Exception as exc:
            logger.warning(f"[hub] score error on {symbol}: {exc}")
            return None

    scored: list = []
    try:
        pool = _BilliardPool(processes=max_workers)
        try:
            results = await asyncio.gather(*[score_one(pool, s, d) for s, d in dfs.items()])
        finally:
            pool.terminate()
            pool.join()
        scored = [r for r in results if r is not None]
        if not scored and dfs:
            # Every single symbol failed — something is wrong with the pool
            # itself (not a per-symbol issue), so don't silently return an
            # empty scoreboard. Fall through to the in-process fallback below.
            raise RuntimeError("billiard pool returned 0/%d scores — pool itself is broken" % len(dfs))
    except Exception as exc:
        # Safety net: never let a broken pool silently zero out the Hub's
        # scoreboard (this happened twice in production while landing this
        # feature). Fall back to plain single-core scoring — slower, but
        # correct — rather than feeding india_trade_loop nothing.
        logger.error(f"[hub] process-pool scoring failed ({exc}) — falling back to single-core scoring")
        scored = []
        for symbol, df in dfs.items():
            try:
                scored.append(_score_symbol_sync(
                    symbol, df, ctx, dfs_1m.get(symbol), swing_flags.get(symbol, False)
                ))
            except Exception as sym_exc:
                logger.debug(f"[hub] score error on {symbol}: {sym_exc}")

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


async def run_research_gate_for_history(
    scored: list,
    max_symbols: int = 15,
) -> dict[str, dict]:
    """Run pre-trade research gate for top BUY signals.

    Returns {symbol: research_result_dict} for symbols that were researched.
    Called once per Hub cycle — results are stored in hub_daily_history.
    Capped at max_symbols to stay within Tavily free-tier budget.
    """
    from engine.pre_trade_research import run_pre_trade_research

    # Only research non-blocked BUY / STRONG_BUY signals, ranked by master_score
    candidates = [
        s for s in scored
        if not s.is_blocked and s.signal in ("BUY", "STRONG_BUY")
    ][:max_symbols]

    results: dict[str, dict] = {}
    for stock in candidates:
        try:
            r = await run_pre_trade_research(
                symbol=stock.symbol,
                action="BUY",
                score=stock.master_score,
                regime=stock.regime,
                entry=0.0,   # not known yet at scoring time
                stop=0.0,
                t1=0.0,
                fund_grade=getattr(stock, "fund_grade", "WATCHLIST"),
            )
            results[stock.symbol] = r
            logger.debug(
                f"[hub/research] {stock.symbol}: "
                f"veto={r['veto']} source={r['source']}"
            )
        except Exception as exc:
            logger.debug(f"[hub/research] {stock.symbol} failed: {exc}")
    return results


async def persist_daily_history(
    scored: list,
    ctx: "MasterContext",
    session: AsyncSession,
    research_results: dict[str, dict] | None = None,
) -> None:
    """Upsert one row per (date, symbol) into hub_daily_history.

    This is the flight-recorder: every Hub cycle's full output lands here
    so the backtest can replay EXACTLY what the live agent saw on any date,
    including web-research veto decisions.

    Uses raw SQL ON CONFLICT DO UPDATE so re-runs within the same day
    overwrite with the latest scores (last cycle of the day wins).
    """
    from sqlalchemy import text
    from datetime import date as _date

    if not scored:
        return

    bar_date = _date.today()
    rr = research_results or {}

    # Macro snapshot — captured once for this cycle, shared across all symbols
    vix         = round(ctx.macro.india_vix,   2)
    fii_net_3d  = round(ctx.macro.fii_net_3d,  2)
    dii_net_3d  = round(ctx.macro.dii_net_3d,  2)
    nse_mood    = ctx.macro.nse_market_mood
    ad_ratio    = round(ctx.macro.advance_decline_ratio, 3)

    rows_upserted = 0
    for s in scored:
        r = s.reasoning
        res = rr.get(s.symbol)

        web_veto        = res["veto"]         if res else None
        web_veto_reason = res.get("veto_reason", "") if res else None
        web_confidence  = None   # run_pre_trade_research doesn't return a numeric confidence
        research_note   = res.get("research_note", "") if res else None
        research_source = res.get("source", "")         if res else None

        # Extract sector from reasoning (populated by score_symbol)
        sector_detail = r.get("sector_detail", {})
        sector        = sector_detail.get("sector") or r.get("sector_name")

        try:
            await session.execute(text("""
                INSERT INTO hub_daily_history (
                    date, symbol,
                    technical_score, news_score, sector_score, macro_score,
                    earnings_score, fundamental_score, options_score, master_score,
                    signal, regime, sector, fund_grade, is_blocked, blocked_reason,
                    india_vix, fii_net_3d, dii_net_3d, nse_mood, ad_ratio,
                    web_veto, web_veto_reason, web_confidence,
                    research_note, research_source,
                    reasoning, scored_at
                ) VALUES (
                    :date, :symbol,
                    :technical, :news, :sector_s, :macro,
                    :earnings, :fundamental, :options, :master,
                    :signal, :regime, :sector_name, :fund_grade,
                    :is_blocked, :blocked_reason,
                    :vix, :fii, :dii, :mood, :adr,
                    :web_veto, :web_veto_reason, :web_conf,
                    :research_note, :research_source,
                    :reasoning::jsonb, NOW()
                )
                ON CONFLICT (date, symbol) DO UPDATE SET
                    technical_score   = EXCLUDED.technical_score,
                    news_score        = EXCLUDED.news_score,
                    sector_score      = EXCLUDED.sector_score,
                    macro_score       = EXCLUDED.macro_score,
                    earnings_score    = EXCLUDED.earnings_score,
                    fundamental_score = EXCLUDED.fundamental_score,
                    options_score     = EXCLUDED.options_score,
                    master_score      = EXCLUDED.master_score,
                    signal            = EXCLUDED.signal,
                    regime            = EXCLUDED.regime,
                    sector            = EXCLUDED.sector,
                    fund_grade        = EXCLUDED.fund_grade,
                    is_blocked        = EXCLUDED.is_blocked,
                    blocked_reason    = EXCLUDED.blocked_reason,
                    india_vix         = EXCLUDED.india_vix,
                    fii_net_3d        = EXCLUDED.fii_net_3d,
                    dii_net_3d        = EXCLUDED.dii_net_3d,
                    nse_mood          = EXCLUDED.nse_mood,
                    ad_ratio          = EXCLUDED.ad_ratio,
                    web_veto          = COALESCE(EXCLUDED.web_veto, hub_daily_history.web_veto),
                    web_veto_reason   = COALESCE(EXCLUDED.web_veto_reason, hub_daily_history.web_veto_reason),
                    web_confidence    = COALESCE(EXCLUDED.web_confidence, hub_daily_history.web_confidence),
                    research_note     = COALESCE(EXCLUDED.research_note, hub_daily_history.research_note),
                    research_source   = COALESCE(EXCLUDED.research_source, hub_daily_history.research_source),
                    reasoning         = EXCLUDED.reasoning,
                    scored_at         = NOW()
            """), {
                "date":    bar_date,    "symbol":     s.symbol,
                "technical":  float(r.get("technical", 0)),
                "news":       float(r.get("news", 0)),
                "sector_s":   float(r.get("sector", 0)),
                "macro":      float(r.get("macro", 0)),
                "earnings":   float(r.get("earnings", 0)),
                "fundamental": float(r.get("fundamental", 0)),
                "options":    float(r.get("options", 0)),
                "master":     float(s.master_score),
                "signal":     s.signal,       "regime":       s.regime,
                "sector_name": sector,         "fund_grade":  getattr(s, "fund_grade", None),
                "is_blocked": s.is_blocked,   "blocked_reason": s.blocked_reason,
                "vix":        vix,            "fii":          fii_net_3d,
                "dii":        dii_net_3d,     "mood":         nse_mood,
                "adr":        ad_ratio,
                "web_veto":   web_veto,       "web_veto_reason": web_veto_reason,
                "web_conf":   web_confidence,
                "research_note":   research_note,
                "research_source": research_source,
                "reasoning": __import__("json").dumps(r),
            })
            rows_upserted += 1
        except Exception as exc:
            logger.debug(f"[hub/history] upsert failed for {s.symbol}: {exc}")

    await session.commit()
    researched = sum(1 for v in rr.values() if v)
    vetoed     = sum(1 for v in rr.values() if v and v.get("veto"))
    logger.info(
        f"[hub/history] upserted {rows_upserted} rows for {bar_date} | "
        f"researched={researched} vetoed={vetoed}"
    )
