import asyncio
import sys
from utils.logger import logger
import logging

# Ensure debug logs are visible


async def test_all():
    print("=== Testing fetch_news_score (DDG News) ===")
    from engine.tavily_enricher import fetch_news_score
    score, headlines = await fetch_news_score("INFY.NS", "Infosys")
    print(f"Score: {score}")
    for h in headlines:
        print(f" -> {h}")
        
    print("\n=== Testing research_stock_for_alert (DDG Text) ===")
    from engine.tavily_enricher import research_stock_for_alert
    note = await research_stock_for_alert("INFY.NS", 80, 80, 80, "BULL", 100, 95, 105, 110)
    print(f"Research Note Output:\n{note}")
    
    print("\n=== Testing stock_enricher._tavily_news (DDG News Fallback) ===")
    from engine.stock_enricher import _tavily_news
    note_str, sent_score = await _tavily_news("INFY.NS", "Infosys")
    print(f"Note: {note_str}")
    print(f"Sentiment: {sent_score}")

if __name__ == "__main__":
    asyncio.run(test_all())
