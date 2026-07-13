"""
Strategy #5: Event-Driven Arbitrage (News Flash Trading)
When a breaking headline hits, the LLM parses it in milliseconds to assess the
Surprise Factor against market expectations. If the event is an unexpected
black swan or massively bullish catalyst, it issues an instant execution signal.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger
from utils.llm import call_llm_chat

async def evaluate_news_flash(headline: str, summary: str, source: str, session: AsyncSession) -> None:
    """
    Evaluates a breaking news headline for a high 'Surprise Factor'.
    If the LLM determines it's a massive surprise event (black swan or massive catalyst),
    we immediately execute an instant paper trade.
    """
    prompt = f"""You are an elite event-driven algorithmic trading engine for the NSE.
A news flash or market update has just crossed the wire. Evaluate its "Actionability Factor" and immediate price impact on specific Indian listed companies.

Headline: {headline}
Details: {summary}
Source: {source}

Analyze this deeply: Are there geopolitical impacts? Annual/quarterly report beats? Thematic hot topics (like Water, Semi-conductors, Defence, auto)?
Even small news or a strong "hot topic" narrative can lead to a 5-10% swing.
If this news is completely irrelevant to any listed Indian stock, IGNORE it (actionability < 50).
If this news provides a clear narrative, theme, or catalyst for a specific stock (e.g. order win, government policy, thematic momentum, earnings growth), issue an instant market-order signal.

Respond ONLY with valid JSON in this exact format:
{{
  "is_actionable": true/false,
  "actionability_factor_0_to_100": 85,
  "affected_symbol": "TICKER_WITHOUT_NS",
  "direction": "LONG" or "SHORT",
  "reasoning": "Detailed 2-3 sentence explanation of the news impact, sector narrative, and why this stock will move before public catches on."
}}
"""
    try:
        resp = await call_llm_chat(
            [{"role": "system", "content": "You are an instant-reaction thematic and event arbitrage AI fund manager."},
             {"role": "user", "content": prompt}],
            max_tokens=500, temperature=0.2
        )
        
        from engine.agent.decision_engine import _parse_first_json
        data = _parse_first_json(resp)
        if not data or not data.get("is_actionable"):
            return
            
        surprise = int(data.get("actionability_factor_0_to_100", 0))
        if surprise < 50:
            return  # Needs at least 50% conviction
            
        symbol = data.get("affected_symbol")
        direction = data.get("direction")
        if not symbol or direction not in ("LONG", "SHORT"):
            return
            
        ticker = f"{symbol.upper()}.NS"
        logger.warning(f"[event_arbitrage] 🔥 HIGH SURPRISE EVENT DETECTED: {ticker} {direction} (Surprise: {surprise})")
        logger.warning(f"[event_arbitrage] Reasoning: {data.get('reasoning')}")
        
        # In a real system, we would instantly hit the Zerodha API with a limit order.
        # Here, we record an instant paper trade to bypass the standard 5-minute trade loop.
        await _execute_instant_trade(ticker, direction, data.get("reasoning"), session)
        
    except Exception as exc:
        logger.error(f"[event_arbitrage] Failed to evaluate news flash: {exc}")

async def _execute_instant_trade(symbol: str, direction: str, reasoning: str, session: AsyncSession) -> None:
    """Instant execution bypasses the standard technical loop."""
    from crawler.live_prices import PRICE_CACHE
    tick = PRICE_CACHE.get(symbol, {})
    price = tick.get("price", 0.0)
    if not price:
        # Fallback to historical if live isn't cached (usually because market is closed or it's a new ticker)
        logger.warning(f"[event_arbitrage] No live price for {symbol}, aborting instant execution.")
        return
        
    # Calculate crude R:R limits for the instant trade
    # If LONG, stop loss 3% down, target 6% up. If SHORT, stop loss 3% up, target 6% down.
    if direction == "LONG":
        sl = price * 0.97
        t1 = price * 1.06
    else:
        sl = price * 1.03
        t1 = price * 0.94
        
    class MockCandidate:
        strategy = "EVENT_ARBITRAGE"
        entry = round(price, 2)
        stop = round(sl, 2)
        target = round(t1, 2)
        risk_reward = 2.0
        
    class MockDecision:
        symbol = symbol
        action = direction
        entry = round(price, 2)
        stop = round(sl, 2)
        qty = max(1, int(100000 / price))  # allocate ~1L for event trades
        regime = "NEWS_FLASH"
        master_score = 99
        confidence = 99
        confidence_factors = {"news_factor": "+50"}
        candidate = MockCandidate()
        
    decision = MockDecision()
    
    # Execute entry via AgentExecutionManager
    logger.info(f"[event_arbitrage] Instantly executing {direction} on {symbol} at {price}")
    from engine.agent.execution import AgentExecutionManager
    exec_mgr = AgentExecutionManager()
    res = await exec_mgr.execute(decision, session)
    logger.info(f"[event_arbitrage] Result: {res}")
