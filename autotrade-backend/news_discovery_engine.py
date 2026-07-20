import asyncio
import logging
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
        self.chart_brief = summary

class NewsDecision:
    def __init__(self, action):
        self.action = action
        self.confidence = 60
        self.regime = "NEUTRAL"
        self.master_score = 75
        self.confidence_factors = {}

async def _extract_ticker_from_news(headline: str, summary: str) -> str | None:
    """Uses a fast, low-token LLM call to extract the NSE ticker from the news."""
    sys_prompt = "You are a financial entity extractor. Extract the NSE trading symbol of the Indian stock mentioned in the news. Return ONLY the symbol with '.NS' appended. If no clear Indian stock is mentioned, return 'NONE'."
    prompt = f"Headline: {headline}\nSummary: {summary}\n\nTicker:"
    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}]
    
    try:
        resp = await call_llm_chat(messages, max_tokens=10, temperature=0.0)
        ticker = resp.strip().upper()
        if ticker == "NONE" or ".NS" not in ticker:
            return None
        return ticker
    except:
        return None

async def _execute_news_trade(
    ticker: str, side: str, headline: str, verdict: dict, *,
    event_directness=None, confidence_source=None, evidence_ids: list[str] | None = None,
) -> bool:
    """Build a TradeIntent from a TAKE verdict and route it through the central
    execution gate (engine.decision_router.execute_trade_intent), so a
    news-triggered trade obeys the same guardrails — cash buffer, sector caps,
    correlation limits, duplicate-position guard, drawdown breakers, AND the
    gate's confidence-provenance/event-directness checks — rather than
    bypassing risk management. Returns True only if a position was actually
    opened.

    event_directness/confidence_source default to DIRECT/CALCULATED (a primary
    TAKE verdict from llm_tooluse_candidate is a real evaluation). The 2nd-order
    cascade caller in process_ticker() overrides both explicitly, since its
    "confidence" is a fixed override, not an independent evaluation — the gate
    blocks that by design (BLOCKED_CONFIDENCE_INTEGRITY) until sector_graph.py
    produces a real per-candidate score.
    """
    from crawler.live_prices import get_price, yfinance_ltp_batch
    from engine.decision_router import (
        TradeIntent, ConfidenceSource, EventDirectness, execute_trade_intent, RoutingOutcome,
    )
    from utils.config import settings

    if event_directness is None:
        event_directness = EventDirectness.DIRECT
    if confidence_source is None:
        confidence_source = ConfidenceSource.CALCULATED

    # 1. Live entry price — process-local cache first, yfinance backstop second
    #    (this script runs as its own process, so it never shares the FastAPI/
    #    Celery worker's in-memory PRICE_CACHE for a symbol they haven't touched).
    snap = get_price(ticker)
    entry_price = snap["price"] if snap else None
    if not entry_price:
        batch = await yfinance_ltp_batch([ticker])
        entry_price = batch.get(ticker)
    if not entry_price or entry_price <= 0:
        logger.warning(f"[news_engine] {ticker}: no live price available — skipping execution")
        return False

    # 2. News catalysts carry no technical support/resistance, so a fixed
    #    percentage stop/target stands in for the ATR-based levels the other
    #    strategies compute — sized to the same ~2.5 R:R the LLM candidate is
    #    already scored against. (Flagged in the 2026-07-20 execution-authority
    #    audit as a template, not real intelligence — replacing this with an
    #    event-type/volatility-aware model is a separate, later fix.)
    stop_pct, target_pct = 0.03, 0.075
    if side == "BUY":
        stop_loss, take_profit = entry_price * (1 - stop_pct), entry_price * (1 + target_pct)
    else:
        stop_loss, take_profit = entry_price * (1 + stop_pct), entry_price * (1 - target_pct)

    confidence = float(verdict.get("confidence") or 60)
    product = "MIS" if side == "SELL" else "CNC"  # NSE: equity shorts must be intraday

    intent = TradeIntent(
        strategy="NEWS_CASCADE" if event_directness == EventDirectness.SECOND_ORDER else "NEWS_DIRECT",
        symbol=ticker, action=side, instrument_type="EQUITY",
        entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
        confidence=confidence, confidence_source=confidence_source,
        event_directness=event_directness, evidence_ids=evidence_ids or [],
        product=product,
        extra={"reasoning_points": [f"News catalyst: {headline}", str(verdict.get("bull", ""))[:200]]},
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


async def process_ticker(ticker, side, headline, summary):
    logger.info(f"⚡ Processing Ticker: {ticker} (Side: {side}) - Multi-Agent LLM Debate")
    cand = NewsCandidate(side, headline, summary)
    dec = NewsDecision(side)

    try:
        result = await llm_tooluse_candidate(ticker, cand, dec)

        if result and result.get('verdict') == 'TAKE':
            logger.warning(f"🚨 TAKE VERDICT — attempting execution 🚨")
            logger.warning(f"Ticker: {ticker} | Action: {side} | Confidence: {result.get('confidence')}%")
            logger.warning(f"Bull Case: {result.get('bull')}")
            logger.warning(f"Bear Case: {result.get('bear')}")
            try:
                success = await _execute_news_trade(ticker, side, headline, result)
                if success:
                    # Trigger 2nd-order graph trades
                    from engine.sector_graph import get_second_order_trades
                    event_sentiment = "positive" if side == "BUY" else "negative"
                    second_order_trades = await get_second_order_trades(ticker, headline, summary, event_sentiment)
                    
                    if second_order_trades:
                        logger.warning(f"🕸️ KNOWLEDGE GRAPH ACTIVATED: Found {len(second_order_trades)} 2nd-Order trades for {ticker}")
                        from engine.decision_router import ConfidenceSource, EventDirectness
                        for trade in second_order_trades:
                            st_ticker = trade["ticker"]
                            st_side = trade["action"]
                            st_reason = trade["reason"]
                            logger.info(f"⚡ Candidate 2nd-Order Trade: {st_ticker} {st_side} - {st_reason}")
                            # sector_graph.py proposes the candidate but never independently
                            # scores it — this "confidence" is a fixed override, not a real
                            # evaluation, so the gate blocks it (BLOCKED_CONFIDENCE_INTEGRITY)
                            # until sector_graph.py produces a genuine per-candidate score.
                            mock_result = {"confidence": 80, "bull": st_reason, "bear": st_reason}
                            await _execute_news_trade(
                                st_ticker, st_side, f"2nd Order Event from {ticker}: {headline}", mock_result,
                                event_directness=EventDirectness.SECOND_ORDER,
                                confidence_source=ConfidenceSource.HARDCODED,
                                evidence_ids=[f"cascade_from:{ticker}"],
                            )
                
                return success
            except Exception as exc:
                logger.error(f"[news_engine] execution error for {ticker}: {exc}")
                return False
        else:
            reason = result.get('key_risk', 'Did not meet criteria') if result else 'Agent failed to reach a decision (Timed out/Insufficient info)'
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

        except Exception as exc:
            logger.error(f"Error in News Loop: {exc}")

        await asyncio.sleep(15)

if __name__ == '__main__':
    asyncio.run(run_news_discovery_loop())
