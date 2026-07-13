import asyncio
from engine.tavily_enricher import fetch_news_score, research_stock_for_alert

async def test_ddg():
    print("Testing DDG integration for news fetch...")
    score, headlines = await fetch_news_score("TCS.NS", "TCS")
    print(f"News Score: {score}")
    for h in headlines:
        print(f" - {h}")
        
    print("\nTesting DDG integration for deep research...")
    note = await research_stock_for_alert("TCS.NS", 0, 0, 0, "BULL", 100, 90, 110, 120)
    print(f"Research Note: {note}")

if __name__ == "__main__":
    asyncio.run(test_ddg())
