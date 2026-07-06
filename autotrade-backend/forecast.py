import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Trigger cache update first so we have latest narrative boosts
        from engine.narrative_engine import refresh_narrative_cache
        await refresh_narrative_cache(force=True)
        
        # Get the top 10 stocks the agent wants to BUY
        res = (await db.execute(text("""
            SELECT m.symbol, m.master_score, m.signal, m.technical_score, m.news_score, m.sector_score
            FROM master_intelligence_scores m
            INNER JOIN (
                SELECT symbol, MAX(scored_at) as max_at 
                FROM master_intelligence_scores 
                GROUP BY symbol
            ) latest ON m.symbol = latest.symbol AND m.scored_at = latest.max_at
            WHERE m.is_blocked = False AND m.signal IN ('BUY', 'STRONG_BUY')
            ORDER BY m.master_score DESC
            LIMIT 15
        """))).fetchall()
        
        print("=== TOP PICKS FOR TOMORROW ===")
        for r in res:
            print(f"{r.symbol:12} | Score: {r.master_score:5.1f} | Tech: {r.technical_score:4.1f} | News: {r.news_score:4.1f} | Sector: {r.sector_score:4.1f}")
            
asyncio.run(main())
