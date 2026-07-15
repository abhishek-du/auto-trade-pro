"""
Institutional-Grade Knowledge Graph for 2nd Order Event-Driven Trading.
Maps direct news events on a primary entity to its suppliers, competitors, or sector beneficiaries.
"""
from typing import List, Dict

# Mapping of Primary Ticker/Sector to its 2nd Order Beneficiaries or Victims
KNOWLEDGE_GRAPH = {
    # ── Auto Sector ────────────────────────────────────────────────────────────
    "TATAMOTORS.NS": {
        "suppliers": ["SONACOMS.NS", "MOTHERSON.NS", "TATAELXSI.NS"],
        "competitors": ["M&M.NS", "MARUTI.NS"],
    },
    "M&M.NS": {
        "suppliers": ["BOSCHLTD.NS", "UNOMINDA.NS"],
        "competitors": ["TATAMOTORS.NS", "MARUTI.NS"],
    },
    
    # ── Commodities & Macro ────────────────────────────────────────────────────
    "CRUDE_OIL_DOWN": {
        "beneficiaries": ["ASIANPAINT.NS", "BERGERPAINT.NS", "INDIGO.NS", "PIDILITIND.NS"],
        "victims": ["ONGC.NS", "OIL.NS", "RELIANCE.NS"],
    },
    "CRUDE_OIL_UP": {
        "beneficiaries": ["ONGC.NS", "OIL.NS"],
        "victims": ["ASIANPAINT.NS", "BERGERPAINT.NS", "INDIGO.NS"],
    },
    
    # ── Real Estate & Infrastructure ───────────────────────────────────────────
    "DLF.NS": {
        "sector_proxies": ["ULTRACEMCO.NS", "AMBUJACEM.NS", "ASTRAL.NS", "KAJARIACER.NS", "HAVELLS.NS"],
        "competitors": ["MACROTECH.NS", "GODREJPROP.NS"],
    },
    "LT.NS": {
        "suppliers": ["SIEMENS.NS", "ABB.NS", "CUMMINSIND.NS"],
        "sector_proxies": ["ULTRACEMCO.NS"],
    },
    
    # ── IT & Tech ──────────────────────────────────────────────────────────────
    "TCS.NS": {
        "competitors": ["INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
    },
    "INFY.NS": {
        "competitors": ["TCS.NS", "WIPRO.NS", "HCLTECH.NS"],
    },
    
    # ── Financials ─────────────────────────────────────────────────────────────
    "HDFCBANK.NS": {
        "competitors": ["ICICIBANK.NS", "AXISBANK.NS", "KOTAKBANK.NS", "SBIN.NS"],
    },
    "BAJFINANCE.NS": {
        "competitors": ["BAJAJFINSV.NS", "CHOLAFIN.NS"],
    }
}

def get_second_order_trades(primary_ticker: str, event_sentiment: str) -> List[Dict[str, str]]:
    """
    Given a primary ticker and its event sentiment ("positive" or "negative"),
    returns a list of 2nd order trades to execute instantly.
    
    Returns: [{"ticker": "SONACOMS.NS", "action": "BUY", "reason": "Supplier of TATAMOTORS.NS"}]
    """
    trades = []
    
    # Normalize ticker
    ticker = primary_ticker.upper()
    if not ticker.endswith(".NS") and not ticker.endswith(".BO") and not ticker.startswith("CRUDE"):
        ticker += ".NS"
        
    if ticker not in KNOWLEDGE_GRAPH:
        return trades
        
    graph = KNOWLEDGE_GRAPH[ticker]
    
    if event_sentiment == "positive":
        # Primary is doing well -> Buy Suppliers, Buy Sector Proxies
        for supplier in graph.get("suppliers", []):
            trades.append({"ticker": supplier, "action": "BUY", "reason": f"Supplier of {ticker} (Positive Event)"})
        for proxy in graph.get("sector_proxies", []):
            trades.append({"ticker": proxy, "action": "BUY", "reason": f"Sector Proxy for {ticker} (Positive Event)"})
            
    elif event_sentiment == "negative":
        # Primary is doing poorly -> Short Suppliers, Buy Competitors (Zero-sum gain)
        for supplier in graph.get("suppliers", []):
            trades.append({"ticker": supplier, "action": "SELL", "reason": f"Supplier of {ticker} (Negative Event)"})
        for comp in graph.get("competitors", []):
            trades.append({"ticker": comp, "action": "BUY", "reason": f"Competitor of {ticker} (Negative Event - Market Share Gain)"})
            
    return trades
