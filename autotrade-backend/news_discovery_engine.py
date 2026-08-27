import asyncio
import logging
import re
import time as _time
from datetime import datetime, timedelta
from db.database import AsyncSessionLocal
from sqlalchemy import text as _sa_text
from db.models import PreMarketNewsQueue
from sqlalchemy import select, update as sa_update
from crawler.news_crawler import (
    fetch_newsdata_india, fetch_free_rss_news, fetch_nse_corporate_announcements,
    SentimentAnalyser,
)
from engine.agent.decision_engine import llm_tooluse_candidate
from utils.llm import call_llm_chat
from crawler.india_price_feed import is_nse_market_open

# Timestamped format (2026-08-26, phase 19). basicConfig's default is
# "LEVEL:name:message" with NO timestamp, and this logger's output lands in
# logs/news-engine.err. That made every line in that file undatable, which is
# why the 2026-08-26 investigation could not attribute poller/queue/consumer
# counts to a session — the file appends since 2026-08-19, so seven days of
# lines were indistinguishable. Format only; level and handlers unchanged.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("news_engine")

# Track processed news headlines to avoid duplicates (persist in memory for the run)
_processed_headlines = set()

# Track processed NSE corporate-announcement seq_ids the same way.
_processed_seq_ids = set()

# NSE's anti-bot layer is far more aggressive on repeated /api/* hits than the
# free RSS feeds are — polling it every 15s (this loop's cadence) risks the
# IP getting blocked. Gate it behind its own, slower cadence instead.
_NSE_ANNOUNCEMENT_POLL_SEC = 60

# Oldest a premarket_news_queue row may be and still be drained at market open
# (2026-08-26, phase 19). Generous by design: 3 days covers a Friday-evening
# filing drained on Monday morning, including a long weekend. See the drain site
# for the measurement that motivated it — 2,451 PENDING rows reaching back to
# 2026-08-14, re-read from the start on every cycle.
_PREMARKET_MAX_AGE_DAYS = 3
_last_nse_announcement_fetch: datetime | None = None

# The announcement poller runs as its own asyncio task (2026-08-25). It has to,
# because section 2 used to sit *after* section 1 in the same loop body, and
# section 1 awaits process_ticker() — a full LLM ReAct loop — once per new RSS
# article. Measured on 2026-08-25: the NSE fetch ran at 09:14:50 IST and not
# again until 16:05:29, a 411-minute gap exactly spanned by 619 agent decisions.
# NSE's market-wide feed is a 20-item sliding window, so every filing made
# during the session scrolled out unseen. Result: zero in-session announcements
# ingested on every trading day from 2026-08-17 onward.
#
# The fetch is the only part that is time-critical (miss the window, lose the
# filing forever), so that is the only part that moved. PDF/OCR/LLM enrichment,
# persistence and candidate processing all still happen in the main loop,
# exactly where and in the order they did before — a queued item can wait, a
# missed poll cannot be recovered.
_NSE_QUEUE_MAX = 200
_NSE_QUEUE: "asyncio.Queue[dict] | None" = None      # created inside the running loop

# Instrumentation for the poller. Read-only outside the poller itself.
_NSE_POLL_STATS: dict = {
    "nse_poll_started_at":   None,
    "nse_poll_completed_at": None,
    "nse_poll_duration":     None,
    "nse_items_seen":        0,
    "nse_items_new":         0,
    "nse_items_duplicate":   0,
    "nse_items_enqueued":    0,
    "nse_items_dropped":     0,   # queue full — never silently discarded, always logged
    "nse_items_inserted":    0,   # incremented by the consumer after a successful persist
    "nse_errors":            0,
    "queue_depth":           0,
    "polls_total":           0,
}


def get_nse_poll_stats() -> dict:
    """Snapshot of the announcement poller's counters."""
    s = dict(_NSE_POLL_STATS)
    s["queue_depth"] = _NSE_QUEUE.qsize() if _NSE_QUEUE is not None else 0
    return s


async def _nse_announcement_poller() -> None:
    """Fetch NSE corporate announcements on a fixed cadence, forever.

    Deliberately does no LLM, PDF or OCR work and opens no long transaction:
    anything slow here would reintroduce the starvation this task exists to
    remove. It fetches, decides which seq_ids are new, and hands them to a
    BOUNDED queue for the main loop to process.

    Marking seq_ids as processed happens here, at enqueue time, not after
    processing. That is what makes a slow consumer safe: the next poll will not
    re-enqueue an item that is still sitting in the queue. The cost is that an
    item in flight is lost if the process dies — the same exposure the previous
    in-process set already had, since a restart cleared it anyway. Persistence
    is protected independently by ON CONFLICT DO NOTHING in the consumer.
    """
    global _NSE_QUEUE
    if _NSE_QUEUE is None:
        _NSE_QUEUE = asyncio.Queue(maxsize=_NSE_QUEUE_MAX)

    while True:
        started = datetime.now()
        _NSE_POLL_STATS["nse_poll_started_at"] = started
        try:
            announcements = await fetch_nse_corporate_announcements()
            seen = len(announcements)
            new = [a for a in announcements if a.get("seq_id") and a["seq_id"] not in _processed_seq_ids]
            _NSE_POLL_STATS["nse_items_seen"] += seen
            _NSE_POLL_STATS["nse_items_duplicate"] += seen - len(new)
            _NSE_POLL_STATS["nse_items_new"] += len(new)

            for ann in new:
                try:
                    _NSE_QUEUE.put_nowait(ann)
                except asyncio.QueueFull:
                    # Bounded on purpose. Dropping loudly beats growing without
                    # limit until the process is OOM-killed mid-session.
                    _NSE_POLL_STATS["nse_items_dropped"] += 1
                    logger.error(
                        f"[nse_poller] queue full ({_NSE_QUEUE_MAX}) — dropped "
                        f"{ann.get('symbol')} seq={ann.get('seq_id')}. The consumer "
                        f"is not keeping up."
                    )
                    continue
                _processed_seq_ids.add(ann["seq_id"])
                _NSE_POLL_STATS["nse_items_enqueued"] += 1

            # Telemetry on EVERY poll (2026-08-26, phase 19), not only when
            # something new arrived. A poll that finds nothing new is exactly
            # the case the 2026-08-26 investigation could not distinguish from
            # a poll that never happened: the fetch logged "N/20 high-impact"
            # from inside crawler.news_crawler, while this module stayed silent
            # unless `new` was non-empty. Counts and a queue depth only — no
            # headlines, no payloads, no credentials.
            logger.info(
                f"[nse_poller] poll seen={seen} new={len(new)} "
                f"dup={seen - len(new)} enqueued={_NSE_POLL_STATS['nse_items_enqueued']} "
                f"dropped={_NSE_POLL_STATS['nse_items_dropped']} "
                f"depth={_NSE_QUEUE.qsize()}/{_NSE_QUEUE_MAX} "
                f"polls={_NSE_POLL_STATS['polls_total']} errors={_NSE_POLL_STATS['nse_errors']}"
            )
            if new:
                logger.info(
                    f"📋 [nse_poller] {len(new)} new of {seen} fetched — queued "
                    f"(depth {_NSE_QUEUE.qsize()})"
                )
        except asyncio.CancelledError:
            logger.info("[nse_poller] cancelled — stopping cleanly")
            raise
        except Exception as exc:
            # A failed poll must not end the poller, and must not touch the
            # main loop. Next tick tries again.
            _NSE_POLL_STATS["nse_errors"] += 1
            logger.error(f"[nse_poller] poll failed: {exc}")
        finally:
            done = datetime.now()
            _NSE_POLL_STATS["nse_poll_completed_at"] = done
            _NSE_POLL_STATS["nse_poll_duration"] = (done - started).total_seconds()
            _NSE_POLL_STATS["polls_total"] += 1

        await asyncio.sleep(_NSE_ANNOUNCEMENT_POLL_SEC)


def _drain_nse_queue(limit: int = 25) -> list[dict]:
    """Take up to `limit` queued announcements without blocking."""
    if _NSE_QUEUE is None:
        return []
    out: list[dict] = []
    while len(out) < limit:
        try:
            out.append(_NSE_QUEUE.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out

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
from engine.event_classifier import resolve_nse_direction

# Fallback only. NSE's own filing category decides direction where the table
# knows it (see the announcement loop); this keyword scan covers the categories
# it does not, and is deliberately NOT the primary signal — it defaults to BUY,
# which is how every routine filing became a bullish candidate.
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


# ── Candidate lifecycle instrumentation (2026-08-25, Phase 1B task E) ────────
# Every headline that does not become an agent decision must say why, exactly
# once. Four of _extract_ticker_from_news()'s six exits used to return None
# silently, so a candidate could vanish with nothing in the record.
#
# Scope note: this is the stage where candidates actually disappear. The
# "event named 5 tickers, the agent evaluated 1" gap is NOT a loss here —
# causal_events.bullish_stocks is a verification input read by
# decision_router._verify_canonical_event, never a work queue. One headline
# yields one ticker by design.
_TICKER_DROP_REASONS: dict[str, int] = {}


def _drop_candidate(reason: str, headline: str, detail: str = "") -> None:
    """Record one terminal reason for a headline that produced no candidate."""
    _TICKER_DROP_REASONS[reason] = _TICKER_DROP_REASONS.get(reason, 0) + 1
    logger.info(
        f"[candidate_lifecycle] DROPPED reason={reason} "
        f"headline={headline[:90]!r}" + (f" detail={detail[:120]!r}" if detail else "")
    )


def get_candidate_drop_reasons() -> dict:
    """Terminal-reason tally since process start."""
    return dict(_TICKER_DROP_REASONS)


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
    except Exception as _exc:
        _drop_candidate("LLM_ERROR", headline, str(_exc))
        return None
    if not company_name:
        _drop_candidate("LLM_EMPTY", headline)
        return None
    if company_name.upper() == "NONE":
        _drop_candidate("NO_LISTED_COMPANY", headline)
        return None

    # Repetition/garbage guard (2026-07-27): live-observed nemotron looping on
    # this exact call — "Aye Finance" (a real, resolvable NSE company, AYE.NS)
    # came back as "AyeAyeAyeAye...Aye Finance" with the same short token
    # repeated ~80+ times. Feeding that straight into instrument search wastes
    # a query and logs a misleading "no NSE instrument match" (looks like a
    # RESOLUTION failure) when the real problem is a malformed EXTRACTION that
    # never should have reached search at all. A genuine company name is never
    # this long or this repetitive.
    _words = company_name.split()
    if len(company_name) > 80 or (
        len(_words) >= 6 and len(set(_words)) <= 2
    ):
        logger.info(
            f"[news_engine] ticker extraction produced malformed/repetitive "
            f"output ({len(company_name)} chars) — treating as extraction "
            f"failure, not a resolution failure: '{company_name[:60]}...'"
        )
        _drop_candidate("MALFORMED_EXTRACTION", headline, company_name)
        return None

    query = _strip_corporate_suffixes(company_name)
    if not query:
        _drop_candidate("EMPTY_AFTER_SUFFIX_STRIP", headline, company_name)
        return None

    from engine.portfolio_service import search_stocks_async

    try:
        async with AsyncSessionLocal() as session:
            matches = await search_stocks_async(query, session)
    except Exception as exc:
        logger.debug(f"[news_engine] instrument lookup failed for '{company_name}': {exc}")
        _drop_candidate("INSTRUMENT_LOOKUP_ERROR", headline, str(exc))
        return None

    if not matches:
        logger.info(f"[news_engine] no NSE instrument match for extracted company '{company_name}' — skipping (fail-closed)")
        _drop_candidate("UNKNOWN_SYMBOL", headline, company_name)
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
            # DAILY candles are the primary source for SL/TP structure — this
            # trade is meant to be HELD for hours-to-days on a news catalyst,
            # not scalped, so ATR/support/resistance must reflect the stock's
            # real day-to-day range. Root-caused 2026-07-27: this used to try
            # 1-MINUTE candles first, so on a day with ~60 min of 1m history
            # ATR came out as ~0.1% of price (e.g. ATR=1.06 on a ₹1057 stock)
            # instead of a normal 1.5-3% daily ATR. A 2×ATR stop off that tiny
            # number sits a fraction of a percent from entry — guaranteed to
            # be clipped by ordinary tick/spread noise regardless of whether
            # the news thesis plays out, producing a loss even when the stock
            # later moves in the predicted direction (AUBANK.NS 2026-07-27:
            # entry ₹1057.16, stop ₹1054.89 — only 0.21% away — stopped out
            # for -₹451 while the stock kept climbing after).
            candles_1d = await get_latest_candles(ticker, "1d", 60, session)
            df = None
            if len(candles_1d) >= 20:
                df = pd.DataFrame([{
                    "open": c.open, "high": c.high, "low": c.low,
                    "close": c.close, "volume": c.volume, "timestamp": c.timestamp,
                } for c in candles_1d])
            if df is None or df.empty:
                # 1-minute fallback only when daily history is unavailable —
                # still better than the static % fallback, but see the
                # MIN_STOP_DISTANCE_PCT floor in compute_trade_levels() below,
                # which now refuses to use ANY tier's stop if it comes out
                # unreasonably tight (the real, general-purpose fix — this
                # ordering change fixes the common case, the floor is the
                # backstop for whatever produces a tight stop next).
                candles_1m = await get_latest_candles(ticker, "1m", 60, session)
                if len(candles_1m) >= 20:
                    df = pd.DataFrame([{
                        "open": c.open, "high": c.high, "low": c.low,
                        "close": c.close, "volume": c.volume, "timestamp": c.timestamp,
                    } for c in candles_1m])
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

    # Admin toggle. Placed HERE, not around the discovery loop, so news is still
    # crawled, classified and persisted while execution is off -- disabling the
    # strategy must not create a gap in the event history that later analysis
    # would read as "no news happened".
    from utils.runtime_config import strategy_enabled

    if not await strategy_enabled("news_engine"):
        logger.info(f"[news_trade] {ticker}: skipped — news engine disabled by strategy toggle")
        return False

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

    # 1c. Multi-session late-entry gate (2026-08-18). Deliberately SEPARATE
    #     from the 30-minute check above, for two reasons learned from
    #     BSE.NS:
    #
    #     (a) The intraday check runs off 15-minute bars, and when none are
    #         stored for today `if _candles:` is False, so the whole gate is
    #         skipped silently. On the day BSE was shorted there were no 15m
    #         bars at all — the guard never executed.
    #     (b) Even with data, ~30 minutes cannot see a move that happened on
    #         previous sessions. BSE had already fallen 7.1% across four
    #         sessions on the Jefferies downgrade; we shorted the next morning
    #         at 3263, below the 3283 the news itself quoted as the low, and
    #         it bounced.
    #
    #     Daily candles are far more reliably present than intraday ones, so
    #     this runs off `1d` bars and covers the overnight gap too. Same
    #     fail-open posture: a data gap must not halt all news trading —
    #     authorize_trade_intent is the gate that fails closed.
    try:
        _look = int(getattr(settings, "NEWS_MULTISESSION_LOOKBACK_DAYS", 3))
        _max_move = float(getattr(settings, "NEWS_MAX_MULTISESSION_MOVE_PCT", 5.0)) / 100.0
        from crawler.price_feed import get_latest_candles
        async with AsyncSessionLocal() as _sess:
            _daily = await get_latest_candles(ticker, "1d", _look + 1, _sess)
        # get_latest_candles returns newest-first; the oldest of the window is
        # our reference close.
        if _daily and len(_daily) >= 2:
            _ref_close = float(_daily[-1].close)
            if _ref_close > 0:
                _move = (entry_price - _ref_close) / _ref_close
                if (side == "BUY" and _move > _max_move) or (side == "SELL" and _move < -_max_move):
                    logger.warning(
                        f"[news_engine] {ticker}: MULTI-SESSION LATE-ENTRY GATE — price already "
                        f"moved {_move:+.2%} over the last {len(_daily)-1} session(s) "
                        f"(ref ₹{_ref_close}, now ₹{entry_price}); the catalyst is priced in — "
                        f"skipping chase entry"
                    )
                    return False
    except Exception as _ms_exc:
        logger.debug(f"[news_engine] {ticker}: multi-session gate check failed (fail-open): {_ms_exc}")

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

    trade_id = (result.metadata or {}).get("trade_id")
    # Real quantity, not the previous hardcoded qty=0 -- fetch it from the
    # PaperTrade row execute_trade_intent just opened (position_size, where
    # the real units live, is local to decision_router.py and not returned).
    qty = 0
    if trade_id:
        try:
            async with AsyncSessionLocal() as _qty_session:
                from db.models import PaperTrade
                _trade = await _qty_session.get(PaperTrade, trade_id)
                if _trade:
                    qty = _trade.size_units or 0
        except Exception as exc:
            logger.debug(f"[news_engine] qty lookup for alert failed: {exc}")

    from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, TradeEntryPayload
    await publish(AlertEvent(
        category=AlertCategory.TRADE, action=AlertAction.ENTRY, severity=Severity.SUCCESS,
        symbol=ticker, trade_id=trade_id,
        payload=TradeEntryPayload(
            decision=_intent_to_signal_for_alert(ticker, side, entry_price, confidence),
            qty=qty,
        ),
    ))
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


async def _resolve_news_id(session, headline: str, published_at) -> int | None:
    """The NewsItem.id for a headline that ON CONFLICT DO NOTHING just skipped.

    `RETURNING id` yields NULL on conflict, so a duplicate headline would leave
    its CausalEvent with news_id=NULL — the very gap this exists to close. This
    re-reads the row using the CONFLICT TARGET ITSELF, so the lookup is exactly
    as unique as the constraint that rejected the insert:

        uq_news_items_headline_day
          UNIQUE (md5(headline), (COALESCE(published_at, crawled_at))::date)
          WHERE crawled_at >= '2026-08-21'

    All three parts are reproduced below, including the partial-index predicate
    — a conflict can only have been raised against a row inside that range, and
    without the predicate an older duplicate outside it could be returned
    instead. COALESCE(:published_at, now()) mirrors what the failed INSERT would
    have stored: crawled_at defaults to now(), so a row with no published_at
    keys on today's date.

    This is an exact key match, not a heuristic. It never falls back to
    timestamp proximity, symbol matching or fuzzy headline comparison: if the
    key does not resolve to exactly one row, it returns None and the event is
    written with news_id=NULL rather than a guess.
    """
    from sqlalchemy import text as _text
    try:
        rows = (await session.execute(_text("""
            SELECT id FROM news_items
            WHERE md5(headline) = md5(:headline)
              AND (COALESCE(published_at, crawled_at))::date
                  = (COALESCE(CAST(:published_at AS timestamp), now()))::date
              AND crawled_at >= TIMESTAMP '2026-08-21 00:00:00'
        """), {"headline": headline, "published_at": published_at})).scalars().all()
    except Exception as exc:
        logger.debug(f"[news_engine] news_id lookup failed for a duplicate headline: {exc}")
        return None
    if len(rows) == 1:
        return int(rows[0])
    if len(rows) > 1:
        # Should be impossible while the unique index exists. Refuse to pick.
        logger.warning(
            f"[news_engine] news_id lookup matched {len(rows)} rows — ambiguous, "
            f"leaving news_id NULL rather than guessing"
        )
    return None


async def _build_evidence(ticker: str, side: str, headline: str, summary: str,
                          news_id: int | None = None):
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

    # Pass FinBERT's score so classify_event can cross-check the LLM's
    # direction against an independent, deterministic read. See
    # engine.event_classifier._direction_contradicts_sentiment.
    _sent_score = None
    try:
        # Reuses this module's cached analyser — FinBERT load is lru_cached, so
        # a single-headline batch here costs nothing beyond the first call.
        _res = _get_sentiment_analyser().analyse_batch([headline])
        if _res:
            _sent_score = float(_res[0].get("score"))
    except Exception as _s_exc:
        logger.debug(f"[news_engine] sentiment cross-check unavailable: {_s_exc}")
        _sent_score = None
    classification = await classify_event(headline, summary, sentiment_score=_sent_score)
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
            bare_ticker = ticker.replace(".NS", "").replace(".BO", "").upper()
            llm_companies = classification.entities.get("companies", [])
            companies = list(set(llm_companies + [bare_ticker]))

            causal = CausalEvent(
                # news_id is threaded from the NewsItem this cycle inserted for
                # this exact headline (2026-08-25). It stays None when the
                # caller has no NewsItem — the anomaly-catalyst and pre-market
                # queue paths genuinely create events with no news row, and a
                # NULL there is correct, not a gap. Historical rows are NOT
                # backfilled: causal_events.news_id was 100% populated
                # 2026-07-16..07-21 by crawler/event_pipeline.py and 0% after
                # origination moved to this engine, and Phase 7 established the
                # historical linkage is unrecoverable (0 exact matches on every
                # candidate key), so only new rows carry it.
                news_id=news_id,
                event_title=classification.category,
                country=classification.impact,  # matches crawler/event_pipeline.py's existing (mis)use of this column
                importance=classification.surprise_score,
                confidence=classification.confidence,
                affected_sectors=classification.entities.get("sectors", []),
                affected_indices=[],
                bullish_stocks=companies if classification.bullish else [],
                bearish_stocks=companies if not classification.bullish else [],
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


async def _persist_news_decision(
    ticker, action, *, side, result, reason, headline, summary, event_id, entry_price=None,
):
    """Persist a news-pipeline decision (BUY / SELL / SKIP) to the agent_decisions
    table so the UI 'News Decision Journal' can show every processed stock — taken
    or skipped — with the FULL reasoning and the evidence/proof behind it.

    Best-effort and fully isolated: a failure here never affects the trade path.
    """
    try:
        from db.models import AgentDecision
        r = result or {}
        conf = 0
        try:
            conf = int(float(r.get("confidence") or 0))
        except (TypeError, ValueError):
            conf = 0
        grounding = r.get("grounding") or {}

        # Price at decision time — useful "proof" context in the journal.
        entry_px = entry_price
        if entry_px is None:
            try:
                from crawler.zerodha_market import get_live_prices
                _ns = ticker if ticker.endswith((".NS", ".BO")) else f"{ticker}.NS"
                q = await get_live_prices([_ns])
                qd = q.get(_ns) or q.get(ticker) or {}
                entry_px = float(qd.get("price") or qd.get("last_price") or 0) or None
            except Exception:
                entry_px = None

        factors = {
            "source": "NEWS",
            "news": {
                "headline": headline,
                "summary": (summary or "")[:1200],
                "event_id": event_id,
                "side": side,
            },
            "verdict": r.get("verdict"),
            "bull": r.get("bull"),
            "bear": r.get("bear"),
            "key_risk": r.get("key_risk"),
            "thesis": r.get("thesis"),
            "market_confirmation": r.get("market_confirmation"),
            "tools_used": r.get("tools_used") or [],
            "grounding": {
                "grounded": grounding.get("grounded"),
                "soft_failed": grounding.get("soft_failed"),
                "unsupported_claims": (grounding.get("unsupported_claims") or [])[:8],
            },
            "model_reasoning": (r.get("model_reasoning") or "")[:4000],
        }
        reasons = [x for x in (r.get("bull"), r.get("bear"), r.get("thesis")) if x] or [reason]

        async with AsyncSessionLocal() as session:
            session.add(AgentDecision(
                symbol=ticker, action=action, confidence=conf,
                strategy="NEWS", regime="",
                entry=entry_px, stop=None, target=None,
                reasons=reasons,
                skip_reason=(str(reason)[:200] if action == "SKIP" else None),
                confidence_factors=factors, is_paper=True,
            ))
            await session.commit()
    except Exception as exc:
        logger.debug(f"[news_engine] persist decision failed for {ticker}: {exc}")


async def process_ticker(ticker, side, headline, summary, news_id=None):
    logger.info(f"⚡ Processing Ticker: {ticker} (Side: {side}) - Multi-Agent LLM Debate")
    cand = NewsCandidate(side, headline, summary)
    dec = NewsDecision(side)
    cand.evidence, event_id = await _build_evidence(ticker, side, headline, summary, news_id)
    cand.event_id = event_id

    if event_id is None:
        # "NO EVENT -> NO TRADE" (docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md §5) —
        # no canonical CausalEvent means this candidate can never legally pass the
        # gate, so don't spend an LLM call deliberating over it.
        logger.info(f"[news_engine] {ticker}: no canonical event — skipping (no LLM call)")
        return False

    # ── P0 (2026-08-20): trust the CLASSIFIER for direction, not the keyword guess ──
    #
    # `side` arrives here from a crude keyword heuristic. There are three such
    # sites and they all default to BUY:
    #   :1395  side = "SELL" if any(w in headline for w in
    #                              ['plunge','crash','loss','down']) else "BUY"
    #   :1470  same shape against _ANNOUNCEMENT_BEARISH_KEYWORDS
    #   :947   ditto
    # Meanwhile `_build_evidence` has just produced a real LLM classification.
    # The two disagreed on 106 of 428 Direct-News evaluations on 2026-08-20
    # (24.8%), and `direct_news_strategy:198` fails closed on disagreement — so
    # a quarter of all classified news was discarded, and NOT ONE SELL ever
    # reached the execution gate: every bearish headline without one of those
    # four words defaulted to BUY and then contradicted its own classification.
    #
    # Correcting it HERE rather than at the three call sites fixes all of them at
    # once, and does it after the classification exists — which is the only point
    # where the true direction is actually known.
    #
    # `cand` and `dec` were built with the stale side, so both are re-pointed;
    # `dec.action` is what the LLM debate argues for and what the executed intent
    # inherits, so leaving it stale would ask the LLM to defend the wrong trade.
    #
    # NEUTRAL/unknown classifications are left alone: there is no direction to
    # take, and the downstream gate will reject them anyway.
    from utils.config import settings as _cfg_mod   # module-scope import is local-only here

    if bool(getattr(_cfg_mod, "NEWS_SIDE_FROM_CLASSIFIER", True)):
        _dir = (getattr(cand.evidence, "direction", "") or "").upper()
        if _dir in ("BULLISH", "BEARISH"):
            _correct = "BUY" if _dir == "BULLISH" else "SELL"
            if _correct != side:
                logger.info(
                    f"[news_engine] {ticker}: side corrected {side} -> {_correct} "
                    f"(classifier says {_dir}; keyword heuristic was wrong)"
                )
                side = _correct
                cand.side = _correct
                dec.action = _correct

    # Direct News strategy (2026-07-27) — fires on the SAME classified evidence,
    # completely independent of the LLM debate below. Never blocks on it, never
    # blocked by it; any failure here is swallowed internally and can't affect
    # the News strategy's own decision that follows.
    try:
        from engine.direct_news_strategy import maybe_direct_trade
        await maybe_direct_trade(ticker, side, event_id, cand.evidence, headline)
    except Exception as _dn_exc:
        logger.debug(f"[news_engine] direct_news hook failed for {ticker}: {_dn_exc}")

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
                await _persist_news_decision(
                    ticker, "SKIP", side=side, result=result,
                    reason=f"Evidence inconsistency: {consistency.reason}",
                    headline=headline, summary=summary, event_id=event_id,
                )
                return False

            try:
                success = await _execute_news_trade(
                    ticker, side, headline, result,
                    event_id=event_id, evidence=cand.evidence, evidence_ids=[str(event_id)],
                )
                await _persist_news_decision(
                    ticker, side if success else "SKIP", side=side, result=result,
                    reason=("TAKE — executed" if success else "TAKE verdict but execution gate blocked"),
                    headline=headline, summary=summary, event_id=event_id,
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
                # 2026-07-27 fix: key_risk is the LLM's OWN choice of field to
                # fill in, and it's frequently left out even when the model
                # gave real reasoning elsewhere (thesis/bear/bull) -- falling
                # straight to the generic "Did not meet criteria" string
                # (live-observed: MADHUCON.NS 27-Jul 16:05, key_risk absent
                # but thesis/bear were populated) threw away exactly the
                # detail a human reads the skip reason FOR. Prefer whichever
                # of these is actually populated, in the order a reader would
                # find most decision-relevant.
                reason = (
                    result.get('key_risk')
                    or result.get('thesis')
                    or result.get('bear')
                    or result.get('bull')
                    or 'Did not meet criteria'
                )
            else:
                from engine.agent.decision_engine import get_last_tooluse_rejection_reason
                reason = get_last_tooluse_rejection_reason() or "Agent failed to reach a decision (reason unavailable)"
            logger.info(f"❌ Agent Rejected Trade for {ticker}. Reason: {reason}")
            await _persist_news_decision(
                ticker, "SKIP", side=side, result=result, reason=reason,
                headline=headline, summary=summary, event_id=event_id,
            )
            return False
    except Exception as exc:
        logger.error(f"Error executing trade for {ticker}: {exc}")
        return False

async def run_news_discovery_loop():
    logger.info("🚀 Starting 24/7 News-First Discovery Engine (Database Queue)...")

    global _NSE_QUEUE
    _NSE_QUEUE = asyncio.Queue(maxsize=_NSE_QUEUE_MAX)
    nse_task = asyncio.create_task(_nse_announcement_poller(), name="nse_announcement_poller")
    logger.info(
        f"📡 NSE announcement poller started as an independent task "
        f"(every {_NSE_ANNOUNCEMENT_POLL_SEC}s, queue max {_NSE_QUEUE_MAX})"
    )

    try:
        await _news_discovery_cycles()
    finally:
        # Clean shutdown: cancel, then await so the CancelledError is actually
        # delivered and the task is not left pending at interpreter exit.
        nse_task.cancel()
        try:
            await nse_task
        except asyncio.CancelledError:
            pass
        logger.info("[news_engine] NSE announcement poller stopped")


async def _process_nse_announcements(market_open: bool) -> int:
    """Drain and process the NSE announcement queue. Returns items handled.

    MOVED TO THE TOP OF THE CYCLE (2026-08-27). It previously sat AFTER the RSS
    section, whose own comment already recorded the hazard: "this block sits
    after section 1 and section 1 awaits an LLM ReAct loop per article". The
    FETCH was moved out to _nse_announcement_poller() for exactly that reason —
    but the CONSUMER was left behind it, so the fix was only half applied.

    MEASURED 2026-08-27: 203 filings fetched, 33 high-impact, queue depth static
    at 33/200 across polls 62/63/64, and 3 rows stored. The consumer ran twice
    all day, at 08:30 and 08:54 — both BEFORE the open, when section 1 does no
    LLM work because market_open is False. From 09:15 onward section 1 never
    finished and this block was never reached.

    Announcements go first because the exchange's own category is the better
    signal: engine/event_classifier.py records ORDER_WIN at +1.053% mean excess
    under NSE's label versus -0.245% under our classifier, over 4,309 filings.
    The RSS section that used to precede it carries the weaker label.
    """
    now = datetime.now()
    # 2. Process NSE corporate announcements (financial results, M&A,
    #    dividends, credit-rating actions, buybacks, resignations…).
    #
    #    The FETCH no longer happens here — _nse_announcement_poller()
    #    owns it and runs as an independent task, because this block
    #    sits after section 1 and section 1 awaits an LLM ReAct loop per
    #    article. See the comment on _NSE_QUEUE for the measurement.
    #    Everything below this point is unchanged: same enrichment, same
    #    persistence, same direction resolution, same dispatch.
    new_announcements = _drain_nse_queue()

    # Skip filings we have ALREADY persisted, before spending anything on them.
    #
    # _processed_seq_ids is in-memory and resets on every restart -- and
    # watchmedo restarts this process on any .py write. Without a durable
    # check, a restart re-downloads the PDF, re-runs OCR and re-calls the LLM
    # for every filing already in the database. Measured 2026-08-27: five
    # restarts in six minutes, and the same Juniper Green filing processed
    # three times.
    #
    # One indexed query against seq_ids we are about to process. Failure here
    # is non-fatal: we fall through and re-process, which is what happened
    # before this existed.
    if new_announcements:
        try:
            _seqs = [a.get("seq_id") for a in new_announcements if a.get("seq_id")]
            if _seqs:
                async with AsyncSessionLocal() as _dedup_s:
                    _known = {
                        r[0] for r in (await _dedup_s.execute(
                            _sa_text(
                                "SELECT news_metadata->>'seq_id' FROM news_items "
                                "WHERE source = 'NSE-Announcements' "
                                "AND news_metadata->>'seq_id' = ANY(:s)"),
                            {"s": _seqs},
                        )).all() if r[0]
                    }
                if _known:
                    _before = len(new_announcements)
                    new_announcements = [
                        a for a in new_announcements if a.get("seq_id") not in _known
                    ]
                    logger.info(
                        f"[nse_consumer] skipped {_before - len(new_announcements)} "
                        f"already-persisted filing(s) before PDF/OCR/LLM"
                    )
                    for _sq in _known:
                        _processed_seq_ids.add(_sq)
        except Exception as _dd_exc:
            logger.warning(
                f"[nse_consumer] durable dedup check failed ({type(_dd_exc).__name__}) "
                f"— proceeding without it")

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

                # The LLM summary goes in METADATA, never in the headline.
                #
                # 2026-08-27: this used to append "| [LLM Summary: ...]" to
                # ann["headline"] BEFORE the insert below. The unique index is
                # uq_news_items_headline_day on (md5(headline), date), and the
                # summary is non-deterministic -- the same Juniper Green filing
                # produced md5 9a401c85 / 3eceb972 / 7b3dd191 on three passes.
                # Different hash every time, so ON CONFLICT DO NOTHING could
                # NEVER fire and every re-drain inserted a fresh row: 367
                # duplicates in 4,770 stored announcements (7.7%), 49% on
                # 2026-08-27 alone once the consumer started keeping up. Each
                # duplicate also costs a PDF download, an OCR pass and an LLM
                # call.
                #
                # The headline now stays exactly as the crawler built it, so
                # the dedup key is stable. Nothing is lost -- the summary is
                # more useful in news_metadata than concatenated into text.
                ann["llm_summary"] = llm_res.get("summary", "")
                ann["llm_signal"] = sig
                ann["llm_impact_score"] = llm_res.get("impact_score", 0)

                ann_sentiments.append({"sentiment": sent, "score": score})
            except Exception as exc:
                logger.error(f"[news_engine] PDF LLM analysis failed for {ann['symbol']}: {exc}")
                ann_sentiments.append({"sentiment": "neutral", "score": 0.0})

        # Same duplicate-tolerant insert as the RSS block above, and
        # for the same reason — but this one is worse if it raises.
        # A UniqueViolationError here aborts the announcement
        # section AFTER the PDF has been downloaded, OCR'd and sent
        # to the LLM, so the expensive work is thrown away and the
        # seq_ids below are never marked processed. The next cycle
        # then re-fetches the same filings and repeats the whole
        # cost, indefinitely.
        from sqlalchemy.dialects.postgresql import insert as _pg_insert

        # headline -> NewsItem.id, same purpose as the RSS map above.
        _ann_news_ids: dict[str, int] = {}
        async with AsyncSessionLocal() as session:
            for ann, sent in zip(new_announcements, ann_sentiments):
                _ann_id = (await session.execute(
                    _pg_insert(NewsItem.__table__)
                    .values(
                        headline=ann["headline"],
                        source=ann["source"],
                        url=ann["pdf_url"],
                        published_at=ann["published_at"],
                        sentiment=sent.get("sentiment", "neutral"),
                        score=sent.get("score", 0.0),
                        tickers_affected=[ann["symbol"]],
                        category=ann["category"],
                        company=ann["company"],
                        # Provenance. seq_id is NSE's own identifier for the
                        # filing and was previously discarded entirely, leaving
                        # `url` and a mutated headline as the only ways to
                        # recognise a filing we already held.
                        news_metadata={
                            "seq_id": ann.get("seq_id"),
                            "llm_summary": ann.get("llm_summary"),
                            "llm_signal": ann.get("llm_signal"),
                            "llm_impact_score": ann.get("llm_impact_score"),
                            "source_symbol": ann.get("symbol"),
                            "pdf_url": ann.get("pdf_url"),
                        },
                    )
                    .on_conflict_do_nothing()
                    .returning(NewsItem.__table__.c.id)
                )).scalar()
                if _ann_id is None:
                    _ann_id = await _resolve_news_id(
                        session, ann["headline"], ann["published_at"])
                if _ann_id is not None:
                    _ann_news_ids[ann["headline"]] = int(_ann_id)
            await session.commit()
            _NSE_POLL_STATS["nse_items_inserted"] += len(new_announcements)

        for ann in new_announcements:
            _processed_seq_ids.add(ann["seq_id"])
            ticker, headline, summary = ann["symbol"], ann["headline"], ann["summary"] or ann["category"]
            text = f"{ann['category']} {ann['summary']}".lower()

            # NSE's own filing category decides whether this is a
            # trade candidate at all (2026-08-24).
            #
            # The keyword scan below defaults to BUY, so EVERY
            # routine filing became a bullish candidate. Replayed
            # over 4,500 historical announcements it agreed with the
            # exchange category on direction almost always — 9
            # disagreements, 0.2% — but it also turned 3,504 of them
            # (77.9%) into BUY/SELL candidates that the category says
            # carry no direction at all.
            #
            # That is where the damage was. Those are NSE's routine
            # categories, dominated by "Outcome of Board Meeting",
            # measured at -0.737% mean excess return with a 36.3% win
            # rate over 1,169 observations
            # (docs/2026-08-24_PHASE3_GROUND_TRUTH_NEWS_ALPHA.md).
            # Acting on them lost money; the fix is to not act.
            #
            # So the value here is suppression, not direction
            # correction. The keyword scan survives only as the
            # fallback for categories the table does not know.
            _res = resolve_nse_direction(ann["category"], text)
            if _res is not None and _res[0] == "NEUTRAL":
                logger.info(
                    f"⏭️  NSE category '{ann['category']}' carries no direction "
                    f"— not a trade candidate: {ticker}"
                )
                continue
            if _res is not None:
                side = "BUY" if _res[0] == "LONG" else "SELL"
            else:
                # Unmapped category: no exchange opinion, keep the
                # old heuristic rather than inventing a direction.
                side = "SELL" if any(w in text for w in _ANNOUNCEMENT_BEARISH_KEYWORDS) else "BUY"

            logger.info(f"🔍 Analyzing NSE announcement: {headline}")
            if market_open:
                await process_ticker(ticker, side, headline, summary,
                                     news_id=_ann_news_ids.get(ann['headline']))
            else:
                logger.info(f"🌙 Market CLOSED. Adding {ticker} to DB Pre-Market Queue for tomorrow morning.")
                async with AsyncSessionLocal() as session:
                    session.add(PreMarketNewsQueue(
                        symbol=ticker, side=side, headline=headline,
                        summary=summary, status="PENDING",
                    ))
                    await session.commit()

    return len(new_announcements) if new_announcements else 0


async def _news_discovery_cycles():
    """The main loop body. Split out so run_news_discovery_loop() owns only
    task lifecycle — start, run, cancel — and the shutdown path stays readable."""
    while True:
        try:
            # Root-caused 2026-07-27: this used to read
            # tasks.india_tasks._is_india_trading_window(), which deliberately
            # extends to 16:00 IST ("market hours plus 30 minutes after
            # close") for a DIFFERENT purpose (letting position-management/
            # reconciliation tasks keep running a bit past the real close) --
            # but that same extended flag was ALSO gating whether a candidate
            # gets processed live (potentially opening a NEW trade) vs queued
            # for tomorrow, so live news between 15:30-16:00 IST could open a
            # position after NSE's real close (confirmed: SHAKTIPUMP.BO
            # opened live at 15:51 IST). is_nse_market_open() is the strict,
            # real-hours definition (09:15-15:30 IST) — the correct check for
            # "may a new trade open right now." The central execution gate
            # (engine.decision_router.authorize_trade_intent) also enforces
            # this independently now, as a backstop for any other caller.
            market_open = is_nse_market_open()

            # 0a. NSE ANNOUNCEMENTS FIRST (2026-08-27).
            #
            # This call used to sit AFTER the RSS section, behind an unbounded
            # `for article in new_articles: await process_ticker(...)` loop
            # where each iteration is a full LLM ReAct loop. Measured that day:
            # 33 high-impact filings fetched, queue depth static at 33/200
            # across consecutive polls, 3 rows stored. The consumer ran twice
            # all day -- 08:30 and 08:54, both BEFORE the open, when section 1
            # does no LLM work. From 09:15 it was never reached again.
            #
            # Exchange-filed announcements are also the better-measured signal
            # (see _process_nse_announcements' docstring), so they get the head
            # of the cycle rather than the tail.
            #
            # Failure here must not cost us the rest of the cycle.
            try:
                _ann_done = await _process_nse_announcements(market_open)
                if _ann_done:
                    logger.info(f"[nse_consumer] processed {_ann_done} announcement(s) this cycle")
            except Exception as _ann_exc:
                logger.error(f"[nse_consumer] announcement processing failed: {_ann_exc}")

            # 0. If Market is Open, Process DB Queue First
            #
            # Read the queue, CLOSE the transaction, then process. This used to
            # be one `async with` that held a transaction open across the whole
            # loop — and process_ticker() below is the full trade pipeline:
            # evidence building, the LLM ReAct loop (up to 20 rounds), grounding
            # checks and execution, i.e. minutes per ticker. The session sat in
            # `idle in transaction` for that entire time; on 2026-08-26 one was
            # observed at 1,955 seconds, holding a connection out of
            # max_connections=100 the whole while.
            #
            # Marking each item PROCESSED in its own short transaction also
            # removes a duplicate-trade risk: previously a failure on item 3 of
            # 5 rolled back the single commit at the end, so items 1 and 2 were
            # left PENDING after their trades had already been placed and were
            # re-processed on the next cycle.
            if market_open:
                async with AsyncSessionLocal() as session:
                    # Only drain news that is genuinely "overnight" (2026-08-26,
                    # phase 19). This table's own docstring says it exists for
                    # "high-impact news captured outside of trading hours for
                    # processing at market open" — but nothing ever bounded how
                    # old a PENDING row may be, and the drain had no cutoff.
                    #
                    # Measured on 2026-08-26: 2,451 PENDING rows reaching back to
                    # 2026-08-14 — twelve days. The engine's own log shows the
                    # drain announcing "Processing 2611 queued" on 24 separate
                    # occasions, i.e. re-reading the same backlog from the start
                    # every time, spending a full LLM ReAct loop per item on
                    # headlines up to twelve days stale. Only 570 process_ticker
                    # invocations were logged against 65,270 drained items, so the
                    # loop never got near the end before the cycle turned over —
                    # and live NSE announcements were reached 4 times in 7 days.
                    #
                    # _PREMARKET_MAX_AGE_DAYS is deliberately generous: 3 days
                    # covers a Friday-evening filing drained on Monday morning
                    # (and a long weekend), while excluding the stale bulk. Older
                    # rows are LEFT PENDING and simply not drained — no row is
                    # mutated, deleted or expired here, so this is reversible by
                    # reverting this file alone.
                    _cutoff = datetime.now() - timedelta(days=_PREMARKET_MAX_AGE_DAYS)
                    res = await session.execute(
                        select(PreMarketNewsQueue).where(
                            PreMarketNewsQueue.status == "PENDING",
                            PreMarketNewsQueue.captured_at >= _cutoff,
                        )
                    )
                    # Detach to plain values: the ORM instances die with the
                    # session below, and nothing here needs them to stay live.
                    queued_items = [
                        (i.id, i.symbol, i.side, i.headline, i.summary, i.captured_at)
                        for i in res.scalars().all()
                    ]

                if queued_items:
                    logger.info(f"🌅 Market is OPEN! Processing {len(queued_items)} queued night/pre-market database alerts...")
                    # Record how far the drain actually gets (2026-08-26, phase
                    # 19). The log used to announce the batch size and then say
                    # nothing more, so a drain that announced 2,611 items and
                    # completed 3 before the cycle turned over was
                    # indistinguishable from one that finished. Counts only.
                    _drain_t0 = _time.monotonic()
                    _drain_done = 0
                    for item_id, symbol, side, headline, summary, captured_at in queued_items:
                        # Recover provenance (2026-08-27). This drain called
                        # process_ticker() with no news_id, so every event it
                        # created carried news_id=NULL. Measured: the premarket
                        # drain was the DOMINANT event source -- 262 items on
                        # 2026-08-27 producing 247 events, all unlinked -- and
                        # causal_events.news_id has been 0% populated since
                        # 2026-07-21, when origination moved off
                        # crawler/event_pipeline.py (which did set it, and is
                        # no longer in the beat schedule).
                        #
                        # The queued headline is the SAME text the RSS path
                        # inserted into news_items, so the id is recoverable by
                        # the existing resolver. Best-effort: an unresolved
                        # lookup yields None, which is exactly today's
                        # behaviour, so this can only add linkage.
                        _pm_news_id = None
                        try:
                            async with AsyncSessionLocal() as _pm_s:
                                # captured_at, NOT None. _resolve_news_id keys on
                                # (md5(headline), COALESCE(published_at, now())::date),
                                # so passing None would key on TODAY while the
                                # queued row was inserted on its capture date --
                                # the lookup would never match and this fix would
                                # be a silent no-op that looked like a fix.
                                # captured_at is tz-aware (the only such column in
                                # this schema); the resolver compares ::date, and
                                # a UTC-vs-IST date boundary can still miss for an
                                # item captured after 18:30 UTC. That residual is
                                # recorded rather than papered over.
                                _pm_news_id = await _resolve_news_id(
                                    _pm_s, headline, captured_at)
                        except Exception as _pm_exc:
                            logger.debug(f"[premarket_drain] news_id lookup failed: {_pm_exc}")
                        await process_ticker(symbol, side, headline, summary,
                                             news_id=_pm_news_id)
                        async with AsyncSessionLocal() as mark_session:
                            await mark_session.execute(
                                sa_update(PreMarketNewsQueue)
                                .where(PreMarketNewsQueue.id == item_id)
                                .values(status="PROCESSED", processed_at=datetime.now())
                            )
                            await mark_session.commit()
                        _drain_done += 1
                    logger.info(
                        f"[premarket_drain] completed={_drain_done}/{len(queued_items)} "
                        f"elapsed_s={int(_time.monotonic() - _drain_t0)} "
                        f"max_age_days={_PREMARKET_MAX_AGE_DAYS}"
                    )
            
            # 1. Fetch Global/Indian News (RSS)
            news_items = await fetch_free_rss_news() 
            new_articles = [n for n in news_items if n.get('headline', '') not in _processed_headlines]

            # headline -> NewsItem.id for the rows this cycle inserted or found
            # (2026-08-25). Initialised before the `if new_articles:` guard so
            # the dispatch below can always read it, even on an empty cycle.
            _news_ids: dict[str, int] = {}
            
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
                # INSERT ... ON CONFLICT DO NOTHING, not an ORM add (2026-08-24).
                #
                # This block used to session.add() every article and commit once.
                # `uq_news_items_headline_day` is a unique index on
                # (md5(headline), COALESCE(published_at, crawled_at)::date), and
                # RSS feeds re-serve the same story every cycle — so one repeat
                # headline raised UniqueViolationError, which propagated to this
                # loop's outer `except Exception` and skipped the REST OF THE
                # CYCLE.
                #
                # Everything after this point is what was being skipped, every
                # 15 seconds: the ticker extraction below, and — the expensive
                # one — section 2's NSE corporate-announcement fetch. Measured
                # consequence: NSE announcements stopped being ingested entirely
                # after 2026-08-21 03:29 while the loop appeared healthy, logging
                # only its RSS fetches. The exchange feed itself was fine; called
                # directly it returned today's filings immediately.
                #
                # Bare on_conflict_do_nothing() with no index inference, matching
                # crawler/news_crawler.py: inferring a PARTIAL expression index
                # means restating its exact predicate, which silently stops
                # matching the day the index changes.
                from sqlalchemy.dialects.postgresql import insert as _pg_insert

                _dupes = 0
                async with AsyncSessionLocal() as session:
                    for article, sent in zip(new_articles, sentiments):
                        headline = article.get('headline', '')
                        if not headline:
                            continue
                        stmt = (
                            _pg_insert(NewsItem.__table__)
                            .values(
                                headline=headline,
                                source=article.get('source', 'RSS'),
                                url=article.get('url'),
                                published_at=article.get('published_at'),
                                sentiment=sent.get('sentiment', 'neutral'),
                                score=sent.get('score', 0.0),
                                tickers_affected=None,
                            )
                            .on_conflict_do_nothing()
                            .returning(NewsItem.__table__.c.id)
                        )
                        _new_id = (await session.execute(stmt)).scalar()
                        if _new_id is None:
                            # ON CONFLICT DO NOTHING returns NULL — the row
                            # already exists. Resolve it by the conflict target
                            # itself so the CausalEvent still links correctly.
                            _dupes += 1
                            _new_id = await _resolve_news_id(
                                session, headline, article.get('published_at'))
                        if _new_id is not None:
                            _news_ids[headline] = int(_new_id)
                    await session.commit()
                if _dupes:
                    logger.debug(
                        f"[news_engine] {_dupes}/{len(new_articles)} duplicate "
                        f"headline(s) suppressed at insert"
                    )
            
            # Each section of this loop is fault-isolated (2026-08-24).
            #
            # The outer `except` at the bottom catches everything and then
            # sleeps to the next cycle, so ANY error raised here skipped every
            # section below it — including section 2's NSE fetch. That is how
            # NSE corporate announcements stopped being ingested after
            # 2026-08-21 03:29 while the loop looked healthy: a duplicate RSS
            # headline raised UniqueViolationError here, every 15 seconds, and
            # the exchange feed below was never reached. The duplicate itself
            # is fixed above; this stops the NEXT unexpected error in RSS
            # handling from silently starving the filing path again.
            try:
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
                        'bonus', 'split', 'resign', 'default', 'upgrade', 'downgrade',
                        # 2026-07-27 coverage widening — real catalysts that lacked a
                        # matching word were silently dropped before ever reaching the
                        # event classifier (LAURUSLABS/ORIENTTECH/LODHA-class misses):
                        'order', 'wins', ' win', 'won ', 'bags', 'bag ', 'secures', 'secured',
                        'contract', 'result', 'record', 'beat', 'beats', 'rises', 'rise ',
                        'doubles', 'triples', 'rally', 'rallies', 'gains', 'gain ', 'awarded',
                        'award', 'approval', 'approved', 'launch', 'expansion', 'guidance',
                        'q1', 'q2', 'q3', 'q4', 'earnings', 'revenue', 'pat ', 'ebitda',
                        'buyback', 'demerger', 'raises', 'cuts', 'hikes', 'slumps', 'tumbles',
                        'falls', 'drops', 'sinks', 'high', 'multibagger', 'block deal',
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
                        await process_ticker(ticker, side, headline, summary,
                                             news_id=_news_ids.get(headline))
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
            except Exception as _rss_exc:
                logger.error(
                    f'[news_engine] RSS article handling failed, continuing to '
                    f'the announcement feed: {_rss_exc}'
                )

            # (NSE announcements are handled at the TOP of this cycle now —
            #  see the _process_nse_announcements() call above and its
            #  docstring for the starvation this fixes.)

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
