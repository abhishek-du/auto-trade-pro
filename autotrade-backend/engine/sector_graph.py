import json
from loguru import logger
from typing import List, Dict

async def get_second_order_trades(primary_ticker: str, headline: str, summary: str, event_sentiment: str) -> List[Dict[str, str]]:
    """
    Dynamically infers 2nd-order beneficiaries or victims across the entire Indian stock market
    (Large, Mid, and Small Cap) using the 120B LLM.
    
    Returns: [{"ticker": "SONACOMS.NS", "action": "BUY", "reason": "Supplier of TATAMOTORS.NS"}]
    """
    from utils.llm import call_llm_chat
    
    ticker = primary_ticker.upper()
    if not ticker.endswith(".NS") and not ticker.endswith(".BO") and not ticker.startswith("CRUDE"):
        ticker += ".NS"
        
    logger.info(f"🕸️ Dynamically analyzing 2nd-order market effects for {ticker}...")
    
    system_prompt = (
        "You are an expert Indian equities prop-desk analyst. "
        "Your job is to identify 2nd-order effect trades based on a primary news event. "
        "If a primary company has a major event (e.g., massive order win, FDA approval, crude drop, management fraud), "
        "identify 1 to 3 OTHER Indian stocks (NSE symbols ending in .NS, can be small/mid/large cap) that will be heavily impacted as a direct consequence. "
        "Examples of relationships: Suppliers, Competitors (market share shift), Customers, or Sector Proxies. "
        "Return ONLY a raw JSON array. DO NOT wrap it in markdown block quotes. If there are no obvious 2nd-order trades, return []. "
        "Format: [{\"ticker\": \"SYMBOL.NS\", \"action\": \"BUY\" or \"SELL\", \"reason\": \"Short explanation\"}]"
    )
    
    user_prompt = (
        f"Primary Stock: {ticker}\n"
        f"Event Sentiment: {event_sentiment}\n"
        f"Headline: {headline}\n"
        f"Summary: {summary}\n\n"
        f"Output JSON array of 2nd-order trades:"
    )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        response = await call_llm_chat(messages, max_tokens=1000, temperature=0.2)
        response = response.strip()
        
        # Clean up any potential markdown formatting
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
            
        trades = json.loads(response.strip())
        
        # Basic validation
        valid_trades = []
        if isinstance(trades, list):
            for t in trades:
                if isinstance(t, dict) and "ticker" in t and "action" in t and "reason" in t:
                    if t["ticker"] != ticker and t["action"] in ["BUY", "SELL"]:
                        valid_trades.append(t)
                        
        return valid_trades
        
    except Exception as exc:
        logger.error(f"[sector_graph] Failed to generate dynamic 2nd-order trades for {ticker}: {exc}")
        return []
