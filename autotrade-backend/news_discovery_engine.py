import asyncio
import logging
from datetime import datetime
from db.database import AsyncSessionLocal
from db.models import PreMarketNewsQueue
from sqlalchemy import select
from crawler.news_crawler import fetch_newsdata_india, fetch_free_rss_news
from engine.agent.decision_engine import llm_tooluse_candidate
from utils.llm import call_llm_chat
from tasks.india_tasks import _is_india_trading_window

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("news_engine")

# Track processed news headlines to avoid duplicates (persist in memory for the run)
_processed_headlines = set()

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

async def process_ticker(ticker, side, headline, summary):
    logger.info(f"⚡ Processing Ticker: {ticker} (Side: {side}) - Multi-Agent LLM Debate")
    cand = NewsCandidate(side, headline, summary)
    dec = NewsDecision(side)
    
    try:
        result = await llm_tooluse_candidate(ticker, cand, dec)
        
        if result and result.get('verdict') == 'TAKE':
            logger.warning(f"🚨 TRADE EXECUTED 🚨")
            logger.warning(f"Ticker: {ticker} | Action: {side} | Confidence: {result.get('confidence')}%")
            logger.warning(f"Bull Case: {result.get('bull')}")
            logger.warning(f"Bear Case: {result.get('bear')}")
            return True
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
                async with AsyncSessionLocal() as session:
                    for article in new_articles:
                        headline = article.get('headline', '')
                        if headline:
                            new_item = NewsItem(
                                headline=headline,
                                source=article.get('source', 'RSS'),
                                url=article.get('url'),
                                sentiment="neutral",
                                score=0.0,
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
                
                action_words = ['surge', 'soar', 'plunge', 'jump', 'crash', 'fta', 'deal', 'profit', 'loss', 'fda']
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
