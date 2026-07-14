import asyncio
import logging
from datetime import datetime
from db.database import AsyncSessionLocal
from db.models import PreMarketNewsQueue
from sqlalchemy import select
from crawler.news_crawler import fetch_newsdata_india, fetch_free_rss_news, SentimentAnalyser
from engine.agent.decision_engine import llm_tooluse_candidate
from utils.llm import call_llm_chat
from tasks.india_tasks import _is_india_trading_window

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("news_engine")

# Track processed news headlines to avoid duplicates (persist in memory for the run)
_processed_headlines = set()

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

async def _execute_news_trade(ticker: str, side: str, headline: str, verdict: dict) -> bool:
    """Route a TAKE verdict through the same risk gate and paper execution path
    the automatic India Trade Loop (Path B) uses, so a news-triggered trade
    obeys the same guardrails — cash buffer, sector caps, correlation limits,
    duplicate-position guard, drawdown breakers — rather than bypassing risk
    management. Returns True only if a position was actually opened.
    """
    from sqlalchemy import select as _select
    from crawler.live_prices import get_price, yfinance_ltp_batch
    from engine.risk_manager import validate_signal, calculate_position_size
    from engine.signal_generator import TradingSignal
    from paper_trading.trade_simulator import open_paper_trade
    from paper_trading.virtual_wallet import VirtualWallet
    from db.models import OpenPosition
    from utils.config import settings

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
    #    already scored against.
    stop_pct, target_pct = 0.03, 0.075
    if side == "BUY":
        stop_loss, take_profit = entry_price * (1 - stop_pct), entry_price * (1 + target_pct)
    else:
        stop_loss, take_profit = entry_price * (1 + stop_pct), entry_price * (1 - target_pct)

    confidence = float(verdict.get("confidence") or 60)
    signal = TradingSignal(
        symbol=ticker, timeframe="news", action=side, confidence=confidence,
        entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
        pattern_score=0.0, indicator_score=0.0, sentiment_score=95.0,
        final_score=confidence, risk_reward_ratio=round(target_pct / stop_pct, 2),
        reasoning_points=[f"News catalyst: {headline}", str(verdict.get("bull", ""))[:200]],
        regime="NEUTRAL",
    )

    async with AsyncSessionLocal() as session:
        summary_row    = await VirtualWallet.get_summary(session)
        balance        = summary_row["balance"]
        pos_result     = await session.execute(
            _select(OpenPosition).where(OpenPosition.product != "MIS")
        )
        open_positions = list(pos_result.scalars().all())

        validated, reason = await validate_signal(signal, balance, open_positions, session)
        if not validated:
            logger.info(f"[news_engine] {ticker} rejected by risk manager: {reason}")
            return False

        pos_size = calculate_position_size(signal, balance)
        product  = "MIS" if side == "SELL" else "CNC"  # NSE: equity shorts must be intraday
        try:
            await open_paper_trade(signal, pos_size, session, product=product)
        except ValueError as exc:
            logger.warning(f"[news_engine] {ticker} execution failed: {exc}")
            return False
        await session.commit()

    logger.warning(
        f"✅ NEWS-TRIGGERED TRADE OPENED: {ticker} {side} "
        f"qty={pos_size.get('units')} @ {entry_price}"
    )
    if getattr(settings, "telegram_available", False):
        try:
            from integrations.telegram_service import send, fmt_entry
            await send(fmt_entry(signal, qty=pos_size.get("units", 0)))
        except Exception as exc:
            logger.warning(f"[news_engine] Telegram alert failed: {exc}")
    return True


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
                return await _execute_news_trade(ticker, side, headline, result)
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

        except Exception as exc:
            logger.error(f"Error in News Loop: {exc}")
        
        await asyncio.sleep(15)

if __name__ == '__main__':
    asyncio.run(run_news_discovery_loop())
