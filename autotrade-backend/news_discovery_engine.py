import asyncio
import logging
import re
from datetime import datetime
from db.database import AsyncSessionLocal
from db.models import PreMarketNewsQueue
from sqlalchemy import select
from crawler.news_crawler import (
    fetch_newsdata_india, fetch_free_rss_news, fetch_nse_corporate_announcements,
    SentimentAnalyser,
)
from engine.agent.decision_engine import llm_tooluse_candidate
from utils.llm import call_llm_chat
from tasks.india_tasks import _is_india_trading_window

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("news_engine")

# Track processed news headlines to avoid duplicates (persist in memory for the run)
_processed_headlines = set()

# Track processed NSE corporate-announcement seq_ids the same way.
_processed_seq_ids = set()

# NSE's anti-bot layer is far more aggressive on repeated /api/* hits than the
# free RSS feeds are — polling it every 15s (this loop's cadence) risks the
# IP getting blocked. Gate it behind its own, slower cadence instead.
_NSE_ANNOUNCEMENT_POLL_SEC = 60
_last_nse_announcement_fetch: datetime | None = None

# ── Pre-event anomaly scan (2026-07-23) ──────────────────────────────────────
# Phase 1 of the anomaly-detection engine (see engine/anomaly_detector.py and
# the approved plan): scans the tracked universe for abnormal price/volume
# behaviour and, for INVESTIGATE-tier symbols, tries to find a real catalyst
# BEFORE the market-wide announcement feed would surface it. An anomaly never
# originates a trade on its own -- only a genuine catalyst found by
# _investigate_anomaly_catalyst() reaches process_ticker() below.
_ANOMALY_SCAN_SEC = 60
_last_anomaly_scan: datetime | None = None
_ANOMALY_INVESTIGATION_COOLDOWN_SEC = 600   # don't re-investigate the same symbol every cycle
_last_anomaly_investigation: dict[str, datetime] = {}

# Negative-leaning keywords for corporate-announcement side inference — wider
# than the RSS headline list since announcement categories use formal terms
# ("Resignation", "Credit Rating") rather than headline verbs ("plunge").
_ANNOUNCEMENT_BEARISH_KEYWORDS = (
    "resign", "downgrade", "default", "loss", "decline", "disqualif", "suspend",
)

# Lazily built on first use — FinBERT load is lru_cached inside news_crawler,
# so re-instantiating this here is cheap after the first call.
_sentiment_analyser = None


def _get_sentiment_analyser() -> SentimentAnalyser:
    global _sentiment_analyser
    if _sentiment_analyser is None:
        _sentiment_analyser = SentimentAnalyser()
    return _sentiment_analyser

class NewsCandidate:
    def __init__(self, side, headline, summary):
        self.strategy = "NEWS_DISCOVERY"
        self.side = side
        self.reasons = [f"News Catalyst: {headline}"]
        self.entry = 0
        self.stop = 0
        self.target = 0
        self.risk_reward = 2.5
        self.hub_subscores = {"technical": 0, "news": 95, "sector": 50, "macro": 50, "earnings": 50, "fundamental": 50, "options": 0}
        # chart_brief intentionally left unset here — news summary text now
        # flows through `evidence` (a DecisionEvidence), not the chart-data
        # field. See process_ticker(), which sets .evidence after classifying.
        self.chart_brief = None
        self.evidence = None
        # Phase 3 (canonical event -> decision-context binding): the canonical
        # CausalEvent.id this candidate traces to, set alongside .evidence by
        # process_ticker(). Rendered into the LLM's context for traceability,
        # and used as the signal that flips llm_tooluse_candidate() into
        # "canonical event already exists — no independent news tool" mode.
        self.event_id = None

class NewsDecision:
    def __init__(self, action):
        self.action = action
        self.confidence = 60
        self.regime = "NEUTRAL"
        self.master_score = 75
        self.confidence_factors = {}

_CORPORATE_SUFFIX_RE = re.compile(
    r"\b(limited|ltd\.?|pvt\.?|private|inc\.?|corp(oration)?\.?|co\.?)\b", re.IGNORECASE
)


def _strip_corporate_suffixes(name: str) -> str:
    """'Bharat Coking Coal Limited' -> 'Bharat Coking Coal' -- improves the
    substring match against KiteInstrument.name, whose exact corporate-suffix
    wording ('Ltd' vs 'Limited', trailing '.', etc.) is inconsistent. Also
    strips leftover punctuation (e.g. the '.' in 'Ltd.') and collapses
    whitespace so the cleaned string is a clean substring candidate."""
    stripped = _CORPORATE_SUFFIX_RE.sub("", name)
    stripped = re.sub(r"[.,]+", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


async def _extract_ticker_from_news(headline: str, summary: str) -> str | None:
    """Identify the company a news item is about via a fast LLM call, then
    resolve it to a REAL, tradeable NSE symbol via engine.portfolio_service's
    instrument search (backed by the kite_instruments table) -- never trust
    an LLM-guessed ticker string directly.

    Root-caused 2026-07-23: the previous version asked the LLM to guess the
    '.NS' ticker directly and used it unchecked. For Bharat Coking Coal
    (commonly abbreviated "BCCL" in financial headlines, but actually listed
    as BHARATCOAL), this produced a plausible-looking but nonexistent
    'BCCL.NS' that silently failed at every downstream price source
    (Zerodha, yfinance, screener.in) one at a time -- quietly discarding an
    82%-confidence trade candidate instead of surfacing the mismatch. Any
    company with a common abbreviation, historical name, or alternate short
    form differing from its official trading symbol is vulnerable to the
    same failure; this fix is general, not specific to one stock. The LLM is
    good at "what company is this news about" (NLU); it is not a reliable
    source of truth for "what is this company's exact exchange ticker"
    (memorized/hallucinated, unverified) -- so symbol resolution is moved to
    our own instrument database, which the LLM's guess never was cross-
    checked against before.
    """
    sys_prompt = (
        "You are a financial entity extractor. Identify the Indian, NSE-listed "
        "company this news is primarily about. Reply with ONLY that company's "
        "commonly used name on a single line (not a ticker symbol, not an "
        "abbreviation you are guessing at) — no explanation, no punctuation "
        "after the name. If no clear NSE-listed Indian company is mentioned, "
        "reply with exactly: NONE"
    )
    prompt = f"Headline: {headline}\nSummary: {summary}\n\nCompany name:"
    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}]

    try:
        resp = await call_llm_chat(messages, max_tokens=20, temperature=0.0)
        # Defensive parsing (2026-07-24, Nova Pro switch): confirmed live that
        # Nova sometimes adds an explanatory sentence after the name despite
        # "ONLY" in the instruction (e.g. "Reliance Industries\n\nThe headline
        # and summary clearly refer to..."), unlike gpt-oss which reliably
        # returned just the name. Taking resp.strip() verbatim would feed that
        # whole multi-line string into instrument search as the "company
        # name", which silently fails to resolve -- exactly the class of
        # silent-drop bug this function's own docstring already root-caused
        # once (the BCCL.NS case). Take only the first non-empty line.
        company_name = next((ln.strip() for ln in (resp or "").splitlines() if ln.strip()), "")
    except Exception:
        return None
    if not company_name or company_name.upper() == "NONE":
        return None

    query = _strip_corporate_suffixes(company_name)
    if not query:
        return None

    from engine.portfolio_service import search_stocks_async

    try:
        async with AsyncSessionLocal() as session:
            matches = await search_stocks_async(query, session)
    except Exception as exc:
        logger.debug(f"[news_engine] instrument lookup failed for '{company_name}': {exc}")
        return None

    if not matches:
        logger.info(f"[news_engine] no NSE instrument match for extracted company '{company_name}' — skipping (fail-closed)")
        return None

    resolved = matches[0]["symbol"]
    logger.info(f"[news_engine] resolved company '{company_name}' -> {resolved} (instrument-validated)")
    return resolved

async def _compute_news_trade_levels(ticker: str, side: str, entry_price: float) -> dict:
    """Structural/volatility-aware SL/TP for a news-triggered trade, replacing
    the previous fixed 3%/7.5% template (flagged in the 2026-07-20
    execution-authority audit as "a template, not real intelligence").

    Reuses the same compute_indicators -> compute_trade_levels hierarchy
    already used by tasks/india_tasks.py's intraday_entry path — not a new,
    parallel risk model:
      1. Dynamic/structural (Supertrend/Bollinger/support-resistance) via
         engine.deep_analysis.build_trade_setup, when 1m/1d candles + enough
         bars are available.
      2. ATR-based (entry ± 2×ATR stop, ± 2×/4×ATR targets), when structure
         isn't available but ATR is.
      3. Static percentage fallback (∓5%/±10%/±15%) — the SAME fallback every
         other strategy in the codebase uses, not a bespoke news-only number.
    Plus a gap-adjustment layer specific to news reactions: if the live entry
    price has already moved materially away from the last known candle close
    (a news-driven gap), the stop computed against pre-gap structure/ATR may
    sit too close to the new price — widen it proportionally rather than
    leaving a stop nearly guaranteed to be clipped by post-gap noise.

    Known gap, not silently assumed handled: this does NOT yet implement a
    liquidity/order-book-depth adjustment tier (bid/ask spread, market depth)
    — that requires a live depth feed this function doesn't have access to.
    """
    import pandas as pd
    from crawler.price_feed import get_latest_candles
    from engine.indicators import compute_indicators
    from engine.risk_manager import compute_trade_levels

    action = "BUY" if side == "BUY" else "SELL"
    sig_ind = None
    last_close = None

    try:
        async with AsyncSessionLocal() as session:
            candles_1m = await get_latest_candles(ticker, "1m", 60, session)
            df = None
            if len(candles_1m) >= 20:
                df = pd.DataFrame([{
                    "open": c.open, "high": c.high, "low": c.low,
                    "close": c.close, "volume": c.volume, "timestamp": c.timestamp,
                } for c in candles_1m])
            if df is None or df.empty:
                candles_1d = await get_latest_candles(ticker, "1d", 60, session)
                if len(candles_1d) >= 20:
                    df = pd.DataFrame([{
                        "open": c.open, "high": c.high, "low": c.low,
                        "close": c.close, "volume": c.volume, "timestamp": c.timestamp,
                    } for c in candles_1d])
            if df is not None and not df.empty:
                last_close = float(df.iloc[-1]["close"])
                sig_ind = compute_indicators(df)
    except Exception as exc:
        logger.debug(f"[news_engine] {ticker}: candle fetch for SL/TP levels failed: {exc}")

    lv = compute_trade_levels(action, entry_price, sig=sig_ind)
    stop_loss, target_1 = lv["stop_loss"], lv["target_1"]

    gap_pct = abs(entry_price - last_close) / last_close if last_close and last_close > 0 else 0.0
    if gap_pct > 0.02:  # >2% gap between last known candle close and live entry
        extra_room = entry_price * min(gap_pct, 0.05)  # cap the widening at 5%
        if action == "BUY":
            stop_loss = min(stop_loss, entry_price - extra_room)
        else:
            stop_loss = max(stop_loss, entry_price + extra_room)

    return {
        "stop_loss": round(stop_loss, 2), "target_1": round(target_1, 2),
        "target_2": round(lv.get("target_2", target_1), 2),
        "atr": lv.get("atr", 0.0), "source": lv.get("source", "static"),
        "gap_pct": round(gap_pct, 4),
    }


# Market-confirmation multiplier per label -- POSITIVE (price already moving
# in the cascade's expected direction) gets full weight; NEGATIVE (price
# already moving against the thesis) is heavily discounted rather than zeroed,
# since a single 15-min read can be noise; NEUTRAL/unknown sits in between.
_MARKET_CONFIRMATION_MULTIPLIER = {"POSITIVE": 1.0, "NEUTRAL": 0.6, "NEGATIVE": 0.2}


async def _get_market_confirmation(ticker: str, side: str) -> str:
    """Does live price action already confirm this cascade candidate's
    expected direction? Compares current LTP against a ~15-30min-old candle,
    the same pattern _execute_news_trade's late-entry gate already uses.
    Returns 'POSITIVE' | 'NEUTRAL' | 'NEGATIVE'. Fails to 'NEUTRAL' (a
    discount, not a free pass) on any data error -- an unconfirmable
    candidate should never score as if it were confirmed."""
    try:
        from crawler.market_snapshot import get_market_snapshot
        from crawler.zerodha_market import get_kite_historical

        snap = await get_market_snapshot(ticker)
        if not snap or not snap.ltp or snap.ltp <= 0:
            return "NEUTRAL"
        today = datetime.now().strftime("%Y-%m-%d")
        async with AsyncSessionLocal() as sess:
            candles = await get_kite_historical(ticker, today, today, "15minute", session=sess)
        if not candles:
            return "NEUTRAL"
        ref = float(candles[-3]["close"]) if len(candles) >= 3 else float(candles[0]["open"])
        if ref <= 0:
            return "NEUTRAL"
        move = (snap.ltp - ref) / ref
        confirms = (side == "BUY" and move > 0.003) or (side == "SELL" and move < -0.003)
        against = (side == "BUY" and move < -0.003) or (side == "SELL" and move > 0.003)
        return "POSITIVE" if confirms else ("NEGATIVE" if against else "NEUTRAL")
    except Exception as exc:
        logger.debug(f"[news_engine] {ticker}: market-confirmation check failed (fail-neutral): {exc}")
        return "NEUTRAL"


def _compute_second_order_confidence(
    event_strength: float, relationship_strength: float, company_exposure: float, market_confirmation: str,
) -> tuple[float, float]:
    """Phase 2.3 formula (News-Only Target Architecture Contract §4b), finally
    wired up end-to-end (2026-07-22): second_order_confidence = event_strength
    x relationship_strength x company_exposure x market_confirmation. Deliberately
    conservative by design -- two sub-1.0 fractions multiplied against the
    primary event's own confidence means most cascades will land well below
    the SECOND_ORDER_MIN_CONFIDENCE bar, and that's intended: a 2nd-order
    inference should need a genuinely strong primary event AND a strong,
    confirmed link to auto-execute, not a shared sector story.

    Returns (final_confidence_0_100, market_confirmation_multiplier).
    """
    mult = _MARKET_CONFIRMATION_MULTIPLIER.get(market_confirmation, 0.5)
    final = event_strength * relationship_strength * company_exposure * mult
    return round(max(0.0, min(100.0, final)), 1), mult


async def _execute_news_trade(
    ticker: str, side: str, headline: str, verdict: dict, *,
    event_directness=None, confidence_source=None, evidence_ids: list[str] | None = None,
    event_id: int | None = None, evidence=None, extra_factors: dict | None = None,
    confidence_factors: dict | None = None,
) -> bool:
    """Build a TradeIntent from a TAKE verdict and route it through the central
    execution gate (engine.decision_router.execute_trade_intent), so a
    news-triggered trade obeys the same guardrails — cash buffer, sector caps,
    correlation limits, duplicate-position guard, drawdown breakers, AND the
    gate's confidence-provenance/event-directness/NO-EVENT-NO-TRADE checks —
    rather than bypassing risk management. Returns True only if a position was
    actually opened.

    event_directness/confidence_source default to DIRECT/CALCULATED (a primary
    TAKE verdict from llm_tooluse_candidate is a real evaluation). The 2nd-order
    cascade caller in process_ticker() overrides both explicitly, since its
    "confidence" is a fixed override, not an independent evaluation — the gate
    blocks that by design (BLOCKED_CONFIDENCE_INTEGRITY) until sector_graph.py
    produces a real per-candidate score.

    event_id: the canonical CausalEvent.id this trade traces back to (per
    docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md's "NO EVENT -> NO TRADE"
    invariant). The gate re-verifies this against the DB itself — it does not
    trust `evidence` (a caller-provided DecisionEvidence snapshot, used only
    for audit-log convenience) as the authority.
    """
    from crawler.market_snapshot import get_market_snapshot
    from engine.decision_router import (
        TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily, execute_trade_intent, RoutingOutcome,
    )
    from utils.config import settings

    if event_directness is None:
        event_directness = EventDirectness.DIRECT
    if confidence_source is None:
        confidence_source = ConfidenceSource.CALCULATED

    # 1. Live entry price via the same MarketSnapshot service the LLM's
    #    price_action/market_depth tools read from (Zerodha WS tick ->
    #    Zerodha REST full quote -> yfinance). This is what makes decision
    #    and execution observe the same tick instead of independently
    #    racing two different price paths.
    snap = await get_market_snapshot(ticker)
    entry_price = snap.ltp if snap else None
    if not entry_price or entry_price <= 0:
        logger.warning(f"[news_engine] {ticker}: no live price available — skipping execution")
        return False
    logger.info(f"[news_engine] {ticker}: entry price ₹{entry_price} (source={snap.source}, fetched_at={snap.fetched_at_ist})")

    # 1b. Late-entry gate (2026-07-22 post-mortem): by the time our news
    #     source surfaces a catalyst, the market has often already moved —
    #     NESTLEIND was bought at the exact top of a spike that ran 10:45-
    #     11:15 IST while our news item arrived 11:19, and TVSMOTOR at the
    #     day high after a 2-session +10% run. Entering AFTER a >2% 30-minute
    #     spike in the trade's own direction is chasing, not anticipating —
    #     skip rather than buy someone else's exit liquidity. Fail-open when
    #     candle data is unavailable (a data outage must not silently halt
    #     ALL news trading; the risk gate that matters fail-closed is the
    #     central authorize_trade_intent, not this timing filter).
    try:
        from crawler.zerodha_market import get_kite_historical
        _today = datetime.now().strftime("%Y-%m-%d")
        async with AsyncSessionLocal() as _sess:
            _candles = await get_kite_historical(ticker, _today, _today, "15minute", session=_sess)
        if _candles:
            # ~30 minutes back: third-from-last bar (last bar is the one
            # currently forming). Early in the session, fall back to day open.
            ref = float(_candles[-3]["close"]) if len(_candles) >= 3 else float(_candles[0]["open"])
            max_spike = float(getattr(settings, "NEWS_MAX_PRE_ENTRY_SPIKE_PCT", 2.0)) / 100.0
            spike = (entry_price - ref) / ref if ref > 0 else 0.0
            if (side == "BUY" and spike > max_spike) or (side == "SELL" and spike < -max_spike):
                logger.warning(
                    f"[news_engine] {ticker}: LATE-ENTRY GATE — price already moved "
                    f"{spike:+.2%} in the last ~30min (ref ₹{ref}, now ₹{entry_price}); "
                    f"skipping chase entry"
                )
                return False
    except Exception as _gate_exc:
        logger.debug(f"[news_engine] {ticker}: late-entry gate check failed (fail-open): {_gate_exc}")

    # 2. Structural/ATR-based SL/TP (Step 5, event-driven-pipeline-audit.md) —
    #    replaces the previous fixed 3%/7.5% template. See
    #    _compute_news_trade_levels() docstring for the full tier hierarchy.
    levels = await _compute_news_trade_levels(ticker, side, entry_price)
    stop_loss, take_profit = levels["stop_loss"], levels["target_1"]
    logger.info(
        f"[news_engine] {ticker} SL/TP source={levels['source']} "
        f"(atr={levels['atr']:.2f}, gap={levels['gap_pct']:.1%}) "
        f"SL=₹{stop_loss} TP=₹{take_profit}"
    )

    confidence = float(verdict.get("confidence") or 60)
    product = "MIS" if side == "SELL" else "CNC"  # NSE: equity shorts must be intraday

    # Confidence transparency (2026-07-22): if the caller didn't build an
    # explicit breakdown (the SECOND_ORDER cascade path does, with its own
    # formula factors), derive one from the DIRECT LLM verdict itself --
    # bull/bear/key_risk/thesis/tools_used/grounding, plus the model's raw
    # reasoning channel from this same call. Never leave a trade with just a
    # bare number and no record of how it was reached.
    if confidence_factors is None:
        confidence_factors = {
            "kind": "llm_tooluse",
            "confidence": confidence,
            "bull": verdict.get("bull"),
            "bear": verdict.get("bear"),
            "key_risk": verdict.get("key_risk"),
            "thesis": verdict.get("thesis"),
            "market_confirmation": verdict.get("market_confirmation"),
            "tools_used": verdict.get("tools_used", []),
            "grounding": verdict.get("grounding"),
            "model_reasoning": (verdict.get("model_reasoning") or "")[:4000],
        }

    # Phase 3: include `thesis` (the canonical-event-grounded field) alongside
    # the legacy `bull` field — the gate's thesis-vs-canonical check
    # (_verify_canonical_event -> validate_evidence_consistency) reads this
    # joined text, so a contradiction placed in either field is caught.
    reasoning_points = [f"News catalyst: {headline}", str(verdict.get("bull", ""))[:200]]
    thesis = verdict.get("thesis")
    if thesis:
        reasoning_points.append(str(thesis)[:300])
    extra = {"reasoning_points": reasoning_points}
    if extra_factors:
        extra.update(extra_factors)

    intent = TradeIntent(
        strategy="NEWS_CASCADE" if event_directness == EventDirectness.SECOND_ORDER else "NEWS_DIRECT",
        symbol=ticker, action=side, instrument_type="EQUITY",
        entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
        confidence=confidence, confidence_source=confidence_source,
        strategy_family=StrategyFamily.EVENT_DRIVEN,
        event_directness=event_directness, evidence_ids=evidence_ids or [],
        event_id=event_id, evidence=evidence,
        product=product,
        extra=extra,
        # Bug fix 2026-07-22: these were computed by _compute_news_trade_levels()
        # above but never threaded through -- see TradeIntent.target_2's
        # docstring for the "T2 silently collapses to T1" bug this closes.
        target_2=levels["target_2"], atr=levels["atr"],
        confidence_factors=confidence_factors,
    )

    async with AsyncSessionLocal() as session:
        result = await execute_trade_intent(intent, session)

    if result.outcome not in (RoutingOutcome.EXECUTED_PAPER, RoutingOutcome.EXECUTED_LIVE):
        logger.info(f"[news_engine] {ticker} not executed: {result.outcome.value} — {result.reason}")
        return False

    logger.warning(f"✅ NEWS-TRIGGERED TRADE OPENED: {ticker} {side} @ {entry_price} ({result.outcome.value})")
    if getattr(settings, "telegram_available", False):
        try:
            from integrations.telegram_service import send, fmt_entry
            await send(fmt_entry(_intent_to_signal_for_alert(ticker, side, entry_price, confidence), qty=0))
        except Exception as exc:
            logger.warning(f"[news_engine] Telegram alert failed: {exc}")
    return True


def _intent_to_signal_for_alert(ticker: str, side: str, entry_price: float, confidence: float):
    """Minimal TradingSignal for the Telegram alert formatter only — the real
    trade record (qty, SL/TP, product) already went through the gate above."""
    from engine.signal_generator import TradingSignal
    return TradingSignal(
        symbol=ticker, timeframe="news", action=side, confidence=confidence,
        entry_price=entry_price, stop_loss=entry_price, take_profit=entry_price,
        pattern_score=0.0, indicator_score=0.0, sentiment_score=95.0, final_score=confidence,
    )


# Generic financial-headline vocabulary excluded when extracting the
# "which company is this actually about" signal from a headline's leading
# words — see _leading_entity_tokens().
_GENERIC_HEADLINE_WORDS = {
    "ltd", "limited", "company", "india", "q1", "q2", "q3", "q4", "results",
    "result", "net", "profit", "loss", "revenue", "rises", "declines", "jumps",
    "falls", "surges", "soars", "plunges", "yoy", "quarter", "quarterly",
    "consolidated", "standalone", "reports", "announces", "the", "and", "of",
    "in", "to", "for", "on", "with", "crore", "cr", "stock", "shares", "share",
}


def _leading_entity_tokens(text: str) -> set[str]:
    """Rough company-identity extraction from a headline: the words before
    the first ':' (or the whole headline if there's no colon), minus generic
    financial-headline vocabulary. An Indian financial headline's company
    name is almost always in this leading segment ("TVS Motor Company Q1
    results: ...", "ABSL AMC Q1 Results: ...") — good enough to distinguish
    two different companies without needing a ticker->company-name resolver,
    which this file doesn't have."""
    head = text.split(":")[0]
    tokens = {w.strip(".,()'\"").lower() for w in head.split()}
    return {t for t in tokens if t and t not in _GENERIC_HEADLINE_WORDS and len(t) > 2}


async def _find_canonical_event(headline: str, session) -> "tuple[object, int] | None":
    """Phase 2 (docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md, "no duplicate
    LLM classification"): before classifying this headline fresh, check
    whether crawler/event_pipeline.py's independent pipeline already
    classified an equivalent headline recently. That pipeline always links
    news_id to a real NewsItem row, so its CausalEvent rows are the only ones
    we can reliably recover original headline text for (this file's own
    CausalEvent writes have news_id=None — see docstring below and
    docs/PHASE_2_CANONICAL_EVENT_INTEGRATION_REPORT.md §5 for why that gap
    isn't closed here: extending the CausalEvent schema wasn't judged
    "genuinely necessary" per the contract's Rule 1 — this dedup already
    catches the cross-pipeline duplication that matters most).

    Reuses the exact same difflib similarity approach and 0.5 threshold as
    engine/news_discovery_engine.py::DuplicateEventEngine, for consistency
    with the one clustering mechanism that already exists in this codebase.

    Two guards added after a live run matched TVS Motor's trade to a
    zeroed-out CausalEvent whose actual news item was about Aditya Birla Sun
    Life AMC — a completely different company. Template-heavy financial
    headlines ("X Q1 Results: profit rises N% YoY to ₹Y crore") cross the 0.5
    similarity threshold for two unrelated companies purely from shared
    boilerplate phrasing:
      1. Skip crawler/event_pipeline.py's own "duplicate stub" rows
         (country="DUPLICATE", confidence=0.0, importance=0 — a deliberate
         marker for "folded into another cluster's primary classification,
         not real signal"). Reusing one as if it were a genuine canonical
         event attaches an empty/zero-confidence event to a real candidate.
      2. Require the two headlines to share a distinctive leading word (the
         company name almost always leads an Indian financial headline —
         "TVS Motor Company Q1 results...", "ABSL AMC Q1 Results...").
         Deliberately NOT implemented as a ticker-vs-bullish_stocks/
         bearish_stocks check: those lists store full company names ("BANDHAN
         BANK", "Reliance Industries"), not bare tickers, so a bare-ticker
         comparison against them silently fails even for genuine same-company
         matches — tried that first, verified live that it broke real matches
         (Bandhan Bank, Reliance) before switching to this headline-text
         approach, which needs no ticker->company-name resolver at all.

    Returns (CausalEvent, news_item_headline) for the best match within the
    last 6 hours, or None if nothing matches.
    """
    import difflib
    from datetime import timedelta
    from sqlalchemy import select as _select
    from db.models import CausalEvent, NewsItem

    cutoff = datetime.utcnow() - timedelta(hours=6)
    rows = (await session.execute(
        _select(CausalEvent, NewsItem.headline)
        .join(NewsItem, CausalEvent.news_id == NewsItem.id)
        .where(CausalEvent.created_at >= cutoff)
        .order_by(CausalEvent.created_at.desc())
        .limit(100)
    )).all()

    target_entities = _leading_entity_tokens(headline)

    for causal, ni_headline in rows:
        if not ni_headline:
            continue
        if causal.country == "DUPLICATE":
            continue
        similarity = difflib.SequenceMatcher(None, headline.lower(), ni_headline.lower()).ratio()
        if similarity <= 0.5:
            continue
        if target_entities and not (target_entities & _leading_entity_tokens(ni_headline)):
            continue
        return causal, ni_headline
    return None


async def _build_evidence(ticker: str, side: str, headline: str, summary: str):
    """Classify this event (headline + summary, not headline-only) and persist
    a CausalEvent row for traceability, connecting the previously-disconnected
    event-classification pipeline (crawler/event_pipeline.py) to the actual
    trade-decision path for the first time.

    Phase 2 addition: first checks _find_canonical_event() — if
    crawler/event_pipeline.py's independent pipeline already classified an
    equivalent headline recently, that classification is reused (no second,
    independent LLM call, no second CausalEvent row for the same real event).

    Returns (DecisionEvidence, event_id) — event_id is the persisted
    CausalEvent.id (the canonical row the central gate will look up and
    verify against per docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md's
    "NO EVENT -> NO TRADE" invariant). Returns (None, None) if classification
    fails or the row couldn't be persisted — callers must treat this as "no
    event, no trade," not as a free pass (this was a real fail-open bug,
    documented in docs/COMPLETE_SYSTEM_DEEP_AUDIT_HINGLISH.md P0-2, fixed by
    the gate itself now requiring a real event_id rather than trusting a
    caller-supplied evidence snapshot)."""
    from engine.event_classifier import classify_event, DecisionEvidence
    from db.models import CausalEvent

    # Phase 2 — reuse the canonical classification if event_pipeline.py's
    # independent pipeline already produced one for this same real event,
    # instead of a second, independent LLM call that could disagree with it.
    try:
        async with AsyncSessionLocal() as dedup_session:
            found = await _find_canonical_event(headline, dedup_session)
    except Exception as exc:
        logger.debug(f"[news_engine] {ticker}: canonical-event lookup failed, proceeding to classify fresh: {exc}")
        found = None

    if found is not None:
        canonical, matched_headline = found
        bare = ticker.replace(".NS", "").replace(".BO", "").upper()
        bullish = {s.upper() for s in (canonical.bullish_stocks or [])}
        bearish = {s.upper() for s in (canonical.bearish_stocks or [])}
        direction = "BULLISH" if bare in bullish else ("BEARISH" if bare in bearish else ("BULLISH" if side == "BUY" else "BEARISH"))
        evidence = DecisionEvidence(
            source_type="CANONICAL_REUSE", source_id=str(canonical.id), title=matched_headline,
            summary=summary or "", event_category=canonical.event_title,
            materiality=canonical.country, direction=direction, confidence=canonical.confidence,
        )
        logger.info(
            f"[news_engine] {ticker}: reusing canonical CausalEvent id={canonical.id} "
            f"(matched headline: '{matched_headline[:60]}...') — skipping a second classify_event() call"
        )
        return evidence, canonical.id

    classification = await classify_event(headline, summary)
    if classification is None:
        logger.warning(f"[news_engine] {ticker}: event classification failed — no event, no trade")
        return None, None

    evidence = DecisionEvidence.from_classification(
        classification, source_type="NSE_ANNOUNCEMENT_OR_RSS", source_id=None,
        title=headline, summary=summary or "",
    )

    event_id = None
    try:
        async with AsyncSessionLocal() as session:
            causal = CausalEvent(
                news_id=None,  # this pipeline doesn't have a NewsItem row to link — see audit doc §3.6
                event_title=classification.category,
                country=classification.impact,  # matches crawler/event_pipeline.py's existing (mis)use of this column
                importance=classification.surprise_score,
                confidence=classification.confidence,
                affected_sectors=classification.entities.get("sectors", []),
                affected_indices=[],
                bullish_stocks=classification.entities.get("companies", []) if classification.bullish else [],
                bearish_stocks=classification.entities.get("companies", []) if not classification.bullish else [],
                duration=str(classification.expected_half_life_hours),
            )
            session.add(causal)
            await session.commit()
            event_id = causal.id
    except Exception as exc:
        logger.warning(f"[news_engine] {ticker}: failed to persist CausalEvent: {exc}")

    if event_id is None:
        # Classification succeeded but persistence failed — under the
        # "NO EVENT -> NO TRADE" invariant there is no canonical row to trace
        # this trade to, so treat it the same as a classification failure.
        logger.warning(f"[news_engine] {ticker}: CausalEvent not persisted — no event, no trade")
        return None, None

    return evidence, event_id


async def _evidence_from_event_id(event_id: int, side: str, session) -> "object | None":
    """Reconstruct a DecisionEvidence directly from an already-persisted
    CausalEvent row, given just its id -- no headline needed (used by the
    re-entry watcher below, which only stores event_id/evidence_ids, not the
    original headline text). Mirrors _build_evidence()'s canonical-reuse
    branch exactly, minus the headline-similarity lookup."""
    from db.models import CausalEvent
    from engine.event_classifier import DecisionEvidence

    canonical = await session.get(CausalEvent, event_id)
    if canonical is None:
        return None
    direction = "BULLISH" if side == "BUY" else "BEARISH"
    return DecisionEvidence(
        source_type="CANONICAL_REUSE", source_id=str(canonical.id), title=canonical.event_title,
        summary="", event_category=canonical.event_title,
        materiality=canonical.country, direction=direction, confidence=canonical.confidence,
    )


async def _check_reentry_watches() -> None:
    """Checks active ReentryWatch rows (registered by a T1-reanalysis EXIT
    decision, see paper_trading/trade_simulator.py::_t1_reversal_exit) against
    live price. On a breakout in the trade's original direction, re-runs a
    FULL fresh multi-tool analysis (llm_tooluse_candidate — every parameter:
    fundamentals, price action, market depth, sector, macro, etc.) and, on a
    TAKE verdict, opens a brand-new position with fresh T1/T2/SL computed from
    the new entry price — re-authorized against the SAME canonical event
    (NO EVENT -> NO TRADE is still satisfied; this is what lets a re-entry
    happen without needing a brand-new news trigger). Expired watches (past
    their expires_at) are marked EXPIRED without triggering anything.
    """
    from sqlalchemy import select
    from db.models import ReentryWatch
    from crawler.market_snapshot import get_market_snapshot

    now = datetime.utcnow()
    async with AsyncSessionLocal() as session:
        watches = (await session.execute(
            select(ReentryWatch).where(ReentryWatch.status == "WATCHING")
        )).scalars().all()

        expired = [w for w in watches if w.expires_at <= now]
        active = [w for w in watches if w.expires_at > now]
        for w in expired:
            w.status = "EXPIRED"
            w.resolved_at = now
        if expired or active:
            await session.commit()

    for watch in active:
        try:
            snap = await get_market_snapshot(watch.symbol)
            if not snap or not snap.ltp or snap.ltp <= 0:
                continue
            price = snap.ltp
            broke_out = (
                (watch.direction == "BUY" and price > watch.watch_level)
                or (watch.direction == "SELL" and price < watch.watch_level)
            )
            if not broke_out:
                continue

            async with AsyncSessionLocal() as session:
                fresh = await session.get(ReentryWatch, watch.id)
                if fresh is None or fresh.status != "WATCHING":
                    continue  # another cycle already claimed this watch
                fresh.status = "TRIGGERED"
                fresh.resolved_at = datetime.utcnow()
                await session.commit()

            logger.warning(
                f"🔔 [reentry] {watch.symbol}: breakout {'above' if watch.direction == 'BUY' else 'below'} "
                f"₹{watch.watch_level:.2f} (now ₹{price:.2f}) — running fresh full re-analysis"
            )

            async with AsyncSessionLocal() as session:
                evidence = await _evidence_from_event_id(watch.event_id, watch.direction, session)
            if evidence is None:
                logger.warning(f"[reentry] {watch.symbol}: event_id={watch.event_id} no longer resolvable — skipping re-entry")
                continue

            cand = NewsCandidate(watch.direction, f"Re-entry watch breakout: {watch.symbol}", watch.reason or "")
            cand.evidence = evidence
            cand.event_id = watch.event_id
            dec = NewsDecision(watch.direction)

            result = await llm_tooluse_candidate(watch.symbol, cand, dec)
            if not (result and result.get("verdict") == "TAKE"):
                if result:
                    detail = f"verdict={result.get('verdict')}"
                else:
                    from engine.agent.decision_engine import get_last_tooluse_rejection_reason
                    detail = get_last_tooluse_rejection_reason() or "no verdict reached"
                logger.info(f"[reentry] {watch.symbol}: fresh re-analysis did not confirm re-entry ({detail}) — staying flat")
                continue

            from engine.event_classifier import validate_evidence_consistency
            consistency = validate_evidence_consistency(cand.evidence, result)
            if not consistency.consistent:
                logger.warning(f"[reentry] {watch.symbol}: ⛔ evidence inconsistency on re-entry: {consistency.reason}")
                continue

            await _execute_news_trade(
                watch.symbol, watch.direction, f"Re-entry breakout confirmed for {watch.symbol}", result,
                event_id=watch.event_id, evidence=cand.evidence, evidence_ids=list(watch.evidence_ids or [str(watch.event_id)]),
            )
        except Exception as exc:
            logger.error(f"[reentry] {watch.symbol}: re-entry check failed: {exc}")


async def _investigate_anomaly_catalyst(symbol: str, session) -> tuple[str, str, str] | None:
    """For an INVESTIGATE-tier anomaly, try to find a REAL catalyst before
    trusting the anomaly alone. Checks, in order: today's earnings calendar,
    NSE's symbol-scoped announcement feed, and recent RSS/newsdata headlines
    matching the symbol's name. Returns (headline, summary, side) for the
    first genuine catalyst found, or None.

    Finding nothing here is the expected, common outcome (see Case A-E in
    the user's review of the anomaly report: an abnormal move can be
    positioning, distribution, short-covering, a market-wide move, or a
    false breakout with no real catalyst at all) -- this function returning
    None means the caller does NOT construct a trade, only logs the
    unexplained anomaly.
    """
    from datetime import date as _date
    from engine.calendar_engine import get_events_for_range
    from crawler.news_crawler import fetch_nse_announcements_for_symbol

    bare = symbol.replace(".NS", "").replace(".BO", "")
    today = _date.today()

    # 1. Scheduled earnings/board-meeting event today. Filter client-side
    #    rather than passing symbol= to get_events_for_range() -- MarketEvent
    #    symbol storage convention isn't guaranteed to match our .NS-suffixed
    #    form, and an exact-match filter that silently returns nothing would
    #    be worse than fetching the (small) daily list and filtering here.
    try:
        all_events = await get_events_for_range(session, today, today, event_types=["EARNINGS"])
    except Exception as exc:
        logger.debug(f"[anomaly] {symbol}: earnings-calendar lookup failed: {exc}")
        all_events = []
    events = [ev for ev in all_events if getattr(ev, "symbol", None) == symbol]
    if events:
        ev = events[0]
        title = getattr(ev, "title", None) or f"{bare} scheduled earnings event"
        return (
            f"{bare}: {title} (scheduled earnings event, abnormal price/volume detected pre-filing)",
            getattr(ev, "description", "") or title,
            "BUY",
        )

    # 2. NSE symbol-scoped announcement feed -- ceiling-free, unlike the
    #    market-wide feed (docs/NEWS_INGESTION_LATENCY_FORENSIC_AUDIT.md).
    try:
        today_str = today.strftime("%d-%m-%Y")
        anns = await fetch_nse_announcements_for_symbol(bare, today_str)
    except Exception as exc:
        logger.debug(f"[anomaly] {symbol}: NSE symbol-scoped fetch failed: {exc}")
        anns = []
    if anns:
        ann = anns[0]
        text = f"{ann['category']} {ann['summary']}".lower()
        side = "SELL" if any(w in text for w in _ANNOUNCEMENT_BEARISH_KEYWORDS) else "BUY"
        return ann["headline"], ann["summary"] or ann["category"], side

    # 3. Recent RSS/newsdata headlines mentioning this company. Weak signal
    #    (no symbol->company-name mapping exists) -- crude substring match
    #    on the bare symbol only.
    try:
        rss_items = await fetch_free_rss_news()
    except Exception as exc:
        logger.debug(f"[anomaly] {symbol}: RSS fetch failed: {exc}")
        rss_items = []
    needle = bare.lower()
    for item in rss_items:
        headline = item.get("headline") or ""
        if needle in headline.lower():
            text = headline.lower()
            side = "SELL" if any(w in text for w in _ANNOUNCEMENT_BEARISH_KEYWORDS) else "BUY"
            return headline, headline, side

    return None


async def _run_anomaly_scan(market_open: bool) -> None:
    """Phase 1 of the pre-event anomaly engine: scans the tracked universe
    for abnormal price/volume behaviour (engine.anomaly_detector). An
    INVESTIGATE-tier reading (past its per-symbol cooldown) triggers
    _investigate_anomaly_catalyst(); a genuine catalyst is dispatched through
    the SAME process_ticker() path every other news trigger uses -- no new
    trade-authorization surface, no change to the News-Only gate. No
    catalyst found -> log only, no trade."""
    from utils.config import settings
    from engine.anomaly_detector import get_anomaly_reading

    universe = settings.nse_symbols + settings.nse_mid_symbols
    if not universe:
        return

    now = datetime.now()
    async with AsyncSessionLocal() as session:
        for symbol in universe:
            try:
                reading = await get_anomaly_reading(symbol, session)
            except Exception as exc:
                logger.debug(f"[anomaly] {symbol}: scan failed: {exc}")
                continue
            if reading is None or reading.tier == "NORMAL":
                continue
            if reading.tier in ("MONITOR", "ALERT"):
                logger.info(
                    f"📊 [anomaly] {symbol}: {reading.tier} score={reading.anomaly_score} "
                    f"z={reading.price_z} vol_ratio={reading.volume_ratio} rs={reading.relative_strength}"
                )
                continue

            # INVESTIGATE tier — cooldown-gated so we don't re-investigate
            # the same symbol every scan cycle.
            last = _last_anomaly_investigation.get(symbol)
            if last and (now - last).total_seconds() < _ANOMALY_INVESTIGATION_COOLDOWN_SEC:
                continue
            _last_anomaly_investigation[symbol] = now

            logger.warning(
                f"🚨 [anomaly] {symbol}: INVESTIGATE score={reading.anomaly_score} "
                f"z={reading.price_z} vol_ratio={reading.volume_ratio} rs={reading.relative_strength} "
                f"— searching for a real catalyst"
            )
            catalyst = await _investigate_anomaly_catalyst(symbol, session)
            if catalyst is None:
                logger.info(f"[anomaly] {symbol}: no catalyst found — unexplained anomaly, no trade")
                continue

            headline, summary, side = catalyst
            logger.warning(f"🔍 [anomaly] {symbol}: catalyst found — {headline}")
            if market_open:
                await process_ticker(symbol, side, headline, summary)
            else:
                logger.info(f"🌙 Market CLOSED. Adding {symbol} to DB Pre-Market Queue for tomorrow morning.")
                async with AsyncSessionLocal() as pm_session:
                    pm_session.add(PreMarketNewsQueue(
                        symbol=symbol, side=side, headline=headline,
                        summary=summary, status="PENDING",
                    ))
                    await pm_session.commit()


async def _log_evidence_gate_audit(ticker, side, evidence, verdict, consistency) -> None:
    """Audit trail for evidence-consistency blocks — separate from the central
    execution gate's own SimulationLog rows (event_type="EXECUTION_GATE") since
    this check runs BEFORE a TradeIntent is even constructed."""
    try:
        from db.models import SimulationLog
        async with AsyncSessionLocal() as session:
            session.add(SimulationLog(
                event_type="EVIDENCE_CONSISTENCY_GATE",
                symbol=ticker,
                message=f"BLOCKED | {side} | {consistency.reason}",
                data={
                    "action": side,
                    "strategy_family": "EVENT_DRIVEN",
                    "verdict_confidence": verdict.get("confidence"),
                    "verdict_bull": verdict.get("bull"),
                    "evidence_materiality": getattr(evidence, "materiality", None),
                    "evidence_category": getattr(evidence, "event_category", None),
                    "unsupported_claims": consistency.unsupported_claims,
                    "reason": consistency.reason,
                },
                timestamp=datetime.utcnow(),
            ))
            await session.commit()
    except Exception as exc:
        logger.debug(f"[news_engine] evidence-gate audit log failed: {exc}")


async def process_ticker(ticker, side, headline, summary):
    logger.info(f"⚡ Processing Ticker: {ticker} (Side: {side}) - Multi-Agent LLM Debate")
    cand = NewsCandidate(side, headline, summary)
    dec = NewsDecision(side)
    cand.evidence, event_id = await _build_evidence(ticker, side, headline, summary)
    cand.event_id = event_id

    if event_id is None:
        # "NO EVENT -> NO TRADE" (docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md §5) —
        # no canonical CausalEvent means this candidate can never legally pass the
        # gate, so don't spend an LLM call deliberating over it.
        logger.info(f"[news_engine] {ticker}: no canonical event — skipping (no LLM call)")
        return False

    try:
        result = await llm_tooluse_candidate(ticker, cand, dec)

        if result and result.get('verdict') == 'TAKE':
            logger.warning(f"🚨 TAKE VERDICT — attempting execution 🚨")
            logger.warning(f"Ticker: {ticker} | Action: {side} | Confidence: {result.get('confidence')}%")
            logger.warning(f"Bull Case: {result.get('bull')}")
            logger.warning(f"Bear Case: {result.get('bear')}")

            # Evidence Consistency Gate — the central execution gate (Phase 1-2,
            # engine/decision_router.py) validates confidence PROVENANCE (was it
            # calculated?), not whether the calculated thesis actually matches the
            # evidence it was shown. This is what would have blocked the
            # 2026-07-20 ULTRACEMCO trade (materiality=LOW, thesis claimed "Strong
            # earnings beat", confidence=71% — a genuinely-calculated number
            # attached to a thesis the evidence doesn't support).
            from engine.event_classifier import validate_evidence_consistency
            consistency = validate_evidence_consistency(cand.evidence, result)
            if not consistency.consistent:
                logger.warning(
                    f"[news_engine] ⛔ EVIDENCE INCONSISTENCY for {ticker}: {consistency.reason}"
                )
                await _log_evidence_gate_audit(ticker, side, cand.evidence, result, consistency)
                return False

            try:
                success = await _execute_news_trade(
                    ticker, side, headline, result,
                    event_id=event_id, evidence=cand.evidence, evidence_ids=[str(event_id)],
                )
                if success:
                    # Trigger 2nd-order graph trades
                    from engine.sector_graph import get_second_order_trades
                    event_sentiment = "positive" if side == "BUY" else "negative"
                    second_order_trades = await get_second_order_trades(ticker, headline, summary, event_sentiment)
                    
                    if second_order_trades:
                        logger.warning(f"🕸️ KNOWLEDGE GRAPH ACTIVATED: Found {len(second_order_trades)} 2nd-Order trades for {ticker}")
                        from engine.decision_router import ConfidenceSource, EventDirectness
                        event_strength = float(result.get("confidence") or 0.0)
                        for trade in second_order_trades:
                            st_ticker = trade["ticker"]
                            st_side = trade["action"]
                            st_reason = trade["reason"]
                            logger.info(f"⚡ Candidate 2nd-Order Trade: {st_ticker} {st_side} - {st_reason}")
                            # Phase 2.3 (News-Only Target Architecture Contract §4b),
                            # wired up for real (2026-07-22): second_order_confidence =
                            # event_strength x relationship_strength x company_exposure x
                            # market_confirmation. Previously this was hardcoded to
                            # confidence=0/HARDCODED (a WATCHLIST_ONLY-forever stub,
                            # after an earlier version hardcoded a fake 80% instead) --
                            # now it's a genuine, per-candidate computed number, with
                            # market_confirmation itself freshly checked against live
                            # price action rather than assumed.
                            rel_type   = trade.get("relationship_type")
                            rel_str    = float(trade.get("relationship_strength") or 0.0)
                            exposure   = float(trade.get("company_exposure") or 0.0)
                            confirmation = await _get_market_confirmation(st_ticker, st_side)
                            so_confidence, mkt_mult = _compute_second_order_confidence(
                                event_strength, rel_str, exposure, confirmation,
                            )
                            so_result = {"confidence": so_confidence, "bull": st_reason, "bear": st_reason}
                            so_confidence_factors = {
                                "kind": "second_order_formula",
                                "confidence": so_confidence,
                                "cascade_from": ticker,
                                "event_strength": event_strength,
                                "relationship_type": rel_type,
                                "relationship_strength": rel_str,
                                "company_exposure": exposure,
                                "market_confirmation": confirmation,
                                "market_confirmation_multiplier": mkt_mult,
                                "formula": "event_strength * relationship_strength * company_exposure * market_confirmation_multiplier",
                            }
                            await _execute_news_trade(
                                st_ticker, st_side, f"2nd Order Event from {ticker}: {headline}", so_result,
                                event_directness=EventDirectness.SECOND_ORDER,
                                confidence_source=ConfidenceSource.CALCULATED,
                                evidence_ids=[f"cascade_from:{ticker}", str(event_id)],
                                event_id=event_id,
                                extra_factors={
                                    "relationship_type": rel_type,
                                    "relationship_strength": rel_str,
                                    "company_exposure": exposure,
                                    "market_confirmation": confirmation,
                                },
                                confidence_factors=so_confidence_factors,
                            )
                
                return success
            except Exception as exc:
                logger.error(f"[news_engine] execution error for {ticker}: {exc}")
                return False
        else:
            # 2026-07-23 fix: llm_tooluse_candidate() returning None used to
            # always log this one generic message, indistinguishable from a
            # genuine round-exhaustion -- live-tested 2026-07-23: 3 of 7
            # candidates in one run showed this exact generic text while the
            # real reason (a grounding rejection catching a hallucinated
            # fact) sat in the debug log, invisible to anyone reading the
            # rejection reason alone. get_last_tooluse_rejection_reason()
            # surfaces the real one.
            if result:
                reason = result.get('key_risk', 'Did not meet criteria')
            else:
                from engine.agent.decision_engine import get_last_tooluse_rejection_reason
                reason = get_last_tooluse_rejection_reason() or "Agent failed to reach a decision (reason unavailable)"
            logger.info(f"❌ Agent Rejected Trade for {ticker}. Reason: {reason}")
            return False
    except Exception as exc:
        logger.error(f"Error executing trade for {ticker}: {exc}")
        return False

async def run_news_discovery_loop():
    logger.info("🚀 Starting 24/7 News-First Discovery Engine (Database Queue)...")
    
    while True:
        try:
            market_open = _is_india_trading_window()
            
            # 0. If Market is Open, Process DB Queue First
            if market_open:
                async with AsyncSessionLocal() as session:
                    res = await session.execute(select(PreMarketNewsQueue).where(PreMarketNewsQueue.status == "PENDING"))
                    queued_items = res.scalars().all()
                    
                    if queued_items:
                        logger.info(f"🌅 Market is OPEN! Processing {len(queued_items)} queued night/pre-market database alerts...")
                        for item in queued_items:
                            await process_ticker(item.symbol, item.side, item.headline, item.summary)
                            item.status = "PROCESSED"
                            item.processed_at = datetime.now()
                            session.add(item)
                        await session.commit()
            
            # 1. Fetch Global/Indian News (RSS)
            news_items = await fetch_free_rss_news() 
            new_articles = [n for n in news_items if n.get('headline', '') not in _processed_headlines]
            
            if new_articles:
                logger.info(f"📰 Found {len(new_articles)} new global/Indian headlines.")
                # Save to NewsItem table for the News Page UI
                from db.models import NewsItem
                analyser = _get_sentiment_analyser()
                try:
                    sentiments = analyser.analyse_batch(
                        [a.get('headline', '') for a in new_articles]
                    )
                except Exception as exc:
                    logger.error(f"[news_engine] sentiment scoring failed: {exc}")
                    sentiments = [{"sentiment": "neutral", "score": 0.0}] * len(new_articles)
                async with AsyncSessionLocal() as session:
                    for article, sent in zip(new_articles, sentiments):
                        headline = article.get('headline', '')
                        if headline:
                            new_item = NewsItem(
                                headline=headline,
                                source=article.get('source', 'RSS'),
                                url=article.get('url'),
                                published_at=article.get('published_at'),
                                sentiment=sent.get('sentiment', 'neutral'),
                                score=sent.get('score', 0.0),
                                tickers_affected=None,
                            )
                            session.add(new_item)
                    await session.commit()
            
            for article in new_articles:
                headline = article.get('headline', '')
                if not headline:
                    continue
                summary = article.get('summary', headline)
                _processed_headlines.add(headline)
                
                action_words = [
                    'surge', 'soar', 'plunge', 'jump', 'crash', 'fta', 'deal', 
                    'profit', 'loss', 'fda', 'acquire', 'acquisition', 'merger', 
                    'buyout', 'stake', 'invest', 'fund', 'spinoff', 'dividend', 
                    'bonus', 'split', 'resign', 'default', 'upgrade', 'downgrade'
                ]
                if not any(w in headline.lower() for w in action_words):
                    continue
                    
                logger.info(f"🔍 Analyzing High-Impact News: {headline}")
                
                ticker = await _extract_ticker_from_news(headline, summary)
                if not ticker:
                    continue
                    
                side = "SELL" if any(w in headline.lower() for w in ['plunge', 'crash', 'loss', 'down']) else "BUY"
                
                # Action based on Market Status
                if market_open:
                    await process_ticker(ticker, side, headline, summary)
                else:
                    logger.info(f"🌙 Market CLOSED. Adding {ticker} to DB Pre-Market Queue for tomorrow morning.")
                    async with AsyncSessionLocal() as session:
                        new_q = PreMarketNewsQueue(
                            symbol=ticker,
                            side=side,
                            headline=headline,
                            summary=summary,
                            status="PENDING"
                        )
                        session.add(new_q)
                        await session.commit()

            # 2. Fetch NSE corporate announcements (financial results, M&A,
            #    dividends, credit-rating actions, buybacks, resignations…) —
            #    on its own slower cadence, see _NSE_ANNOUNCEMENT_POLL_SEC.
            global _last_nse_announcement_fetch
            now = datetime.now()
            if (_last_nse_announcement_fetch is None
                    or (now - _last_nse_announcement_fetch).total_seconds() >= _NSE_ANNOUNCEMENT_POLL_SEC):
                _last_nse_announcement_fetch = now
                announcements = await fetch_nse_corporate_announcements()
                new_announcements = [
                    a for a in announcements if a["seq_id"] and a["seq_id"] not in _processed_seq_ids
                ]

                if new_announcements:
                    logger.info(f"📋 Found {len(new_announcements)} new high-impact NSE corporate announcements.")
                    from db.models import NewsItem
                    from crawler.pdf_parser import process_nse_announcement
                    from engine.sector_graph import get_second_order_trades

                    ann_sentiments = []
                    for ann in new_announcements:
                        try:
                            # 1. Download PDF -> 2. OCR -> 3. LLM Analysis
                            llm_res = await process_nse_announcement(ann["symbol"], ann["headline"], ann["pdf_url"])

                            # Map signal to sentiment for DB
                            sig = llm_res.get("trading_signal", "HOLD")
                            sent = "positive" if sig == "BUY" else ("negative" if sig == "SELL" else "neutral")
                            score = llm_res.get("impact_score", 0) / 100.0

                            # Update headline with deep LLM summary
                            ann["headline"] = f"{ann['headline']} | [LLM Summary: {llm_res.get('summary', '')}]"

                            ann_sentiments.append({"sentiment": sent, "score": score})
                        except Exception as exc:
                            logger.error(f"[news_engine] PDF LLM analysis failed for {ann['symbol']}: {exc}")
                            ann_sentiments.append({"sentiment": "neutral", "score": 0.0})

                    async with AsyncSessionLocal() as session:
                        for ann, sent in zip(new_announcements, ann_sentiments):
                            session.add(NewsItem(
                                headline=ann["headline"],
                                source=ann["source"],
                                url=ann["pdf_url"],
                                published_at=ann["published_at"],
                                sentiment=sent.get("sentiment", "neutral"),
                                score=sent.get("score", 0.0),
                                tickers_affected=[ann["symbol"]],
                                category=ann["category"],
                                company=ann["company"],
                            ))
                        await session.commit()

                    for ann in new_announcements:
                        _processed_seq_ids.add(ann["seq_id"])
                        ticker, headline, summary = ann["symbol"], ann["headline"], ann["summary"] or ann["category"]
                        text = f"{ann['category']} {ann['summary']}".lower()
                        side = "SELL" if any(w in text for w in _ANNOUNCEMENT_BEARISH_KEYWORDS) else "BUY"

                        logger.info(f"🔍 Analyzing NSE announcement: {headline}")
                        if market_open:
                            await process_ticker(ticker, side, headline, summary)
                        else:
                            logger.info(f"🌙 Market CLOSED. Adding {ticker} to DB Pre-Market Queue for tomorrow morning.")
                            async with AsyncSessionLocal() as session:
                                session.add(PreMarketNewsQueue(
                                    symbol=ticker, side=side, headline=headline,
                                    summary=summary, status="PENDING",
                                ))
                                await session.commit()

            # 2b. Pre-event anomaly scan (2026-07-23, Phase 1): abnormal
            #     price/volume behaviour can precede the official filing by
            #     several minutes (the Nestlé case) -- escalate to catalyst
            #     investigation instead of waiting for the announcement feed.
            global _last_anomaly_scan
            if market_open and (
                _last_anomaly_scan is None
                or (now - _last_anomaly_scan).total_seconds() >= _ANOMALY_SCAN_SEC
            ):
                _last_anomaly_scan = now
                await _run_anomaly_scan(market_open)

            # 3. Re-entry watches (2026-07-22): symbols a T1-reanalysis EXIT
            #    decision closed out on reversal risk, waiting for a real
            #    breakout to re-authorize a fresh entry. Checked every cycle
            #    (same as this loop's own 15s cadence) since a breakout can
            #    move fast and there are typically very few active watches.
            if market_open:
                await _check_reentry_watches()

        except Exception as exc:
            logger.error(f"Error in News Loop: {exc}")

        await asyncio.sleep(15)

if __name__ == '__main__':
    asyncio.run(run_news_discovery_loop())
