import asyncio
from tasks.celery_app import celery_app
from utils.logger import logger
from datetime import datetime, timedelta

def _run_async(coro):
    return asyncio.run(coro)

async def _supply_chain_shock_scan():
    from db.database import AsyncSessionLocal
    from sqlalchemy import select
    from db.models import NewsItem
    from engine.agent.unstructured_alpha import analyze_supply_chain_shock
    import json

    logger.info("[alpha_scan] Starting supply chain shock scan")
    async with AsyncSessionLocal() as session:
        recent = datetime.utcnow() - timedelta(hours=1)
        rows = (await session.execute(
            select(NewsItem)
            .where(NewsItem.published_at >= recent)
            .where(NewsItem.score <= -0.6)
            .limit(10)
        )).scalars().all()

        for news in rows:
            if news.headline and "apple" in news.headline.lower():
                res = await analyze_supply_chain_shock("Apple", news.headline, news.summary or news.headline)
                if res and res.get("affected_suppliers"):
                    logger.info(f"[alpha_scan] Supply Chain Impact Detected: {json.dumps(res['affected_suppliers'])}")
                    # Ideally, we would insert these into a 'trading_signals' table or cache

@celery_app.task(name="tasks.unstructured_alpha_scan")
def unstructured_alpha_scan():
    _run_async(_supply_chain_shock_scan())
