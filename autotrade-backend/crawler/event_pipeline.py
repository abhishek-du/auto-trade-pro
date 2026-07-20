import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import AsyncSessionLocal
from db.models import NewsItem, CausalEvent
from engine.event_classifier import classify_event
from utils.logger import logger

from engine.news_discovery_engine import DuplicateEventEngine

async def process_latest_events(session: AsyncSession):
    """
    Scans the latest high-importance news items that haven't been mapped 
    to a CausalEvent yet, clusters them, and classifies them via the LLM Event Classifier.
    """
    stmt = (
        select(NewsItem)
        .outerjoin(CausalEvent, NewsItem.id == CausalEvent.news_id)
        .where(CausalEvent.id == None)
        .order_by(NewsItem.published_at.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    news_items = result.scalars().all()

    if not news_items:
        logger.info("[event_pipeline] No unclassified news items found.")
        return

    logger.info(f"[event_pipeline] Found {len(news_items)} unclassified news items. Clustering...")

    # Convert to dict for the clustering engine
    raw_articles = [{"id": item.id, "headline": item.headline} for item in news_items]
    
    engine = DuplicateEventEngine()
    clustered_events = await engine.cluster_news(raw_articles)
    
    logger.info(f"[event_pipeline] Clustered into {len(clustered_events)} unique events. Classifying...")

    for cluster in clustered_events:
        try:
            # We classify the primary headline of the cluster
            primary_headline = cluster["headline"]
            classification = await classify_event(primary_headline)
            
            if classification:
                # Map all underlying articles in this cluster to the same CausalEvent
                # (We will create one Master Event and link the first news_id for simplicity)
                primary_article_id = cluster["articles"][0]["id"]
                
                causal = CausalEvent(
                    news_id=primary_article_id,
                    event_title=classification.category,
                    country=classification.impact, 
                    importance=classification.surprise_score,
                    confidence=classification.confidence,
                    affected_sectors=classification.entities.get("sectors", []),
                    affected_indices=[],
                    bullish_stocks=classification.entities.get("companies", []) if classification.bullish else [],
                    bearish_stocks=classification.entities.get("companies", []) if not classification.bullish else [],
                    duration=str(classification.expected_half_life_hours)
                )
                session.add(causal)
                
                # For remaining articles in cluster, just mark them as processed by linking to the same event title
                for duplicate in cluster["articles"][1:]:
                    causal_dup = CausalEvent(
                        news_id=duplicate["id"],
                        event_title=classification.category,
                        country="DUPLICATE",
                        importance=0, # It's a duplicate, zero out to prevent inflation
                        confidence=0.0,
                        affected_sectors=[],
                        affected_indices=[],
                        bullish_stocks=[],
                        bearish_stocks=[],
                        duration="0"
                    )
                    session.add(causal_dup)
                    
                logger.info(f"[event_pipeline] Mapped Cluster: {primary_headline[:40]} -> {classification.category} ({len(cluster['articles'])} sources)")
        except Exception as e:
            logger.error(f"[event_pipeline] Classification failed for cluster: {e}")
            
    await session.commit()
    logger.info("[event_pipeline] Finished processing event pipeline.")

async def run_pipeline():
    async with AsyncSessionLocal() as session:
        await process_latest_events(session)

if __name__ == "__main__":
    asyncio.run(run_pipeline())
