import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import AsyncSessionLocal
from db.models import NewsItem, CausalEvent
from engine.event_classifier import classify_event
from utils.logger import logger

async def process_latest_events(session: AsyncSession):
    """
    Scans the latest high-importance news items that haven't been mapped 
    to a CausalEvent yet, and classifies them via the LLM Event Classifier.
    """
    # Find latest 10 news items without a corresponding CausalEvent
    stmt = (
        select(NewsItem)
        .outerjoin(CausalEvent, NewsItem.id == CausalEvent.news_id)
        .where(CausalEvent.id == None)
        .order_by(NewsItem.published_at.desc())
        .limit(10)
    )
    result = await session.execute(stmt)
    news_items = result.scalars().all()

    if not news_items:
        logger.info("[event_pipeline] No unclassified news items found.")
        return

    logger.info(f"[event_pipeline] Found {len(news_items)} unclassified news items. Classifying...")

    for item in news_items:
        try:
            # Send to LLM
            classification = await classify_event(item.headline)
            
            if classification:
                # Create DB Model
                causal = CausalEvent(
                    news_id=item.id,
                    event_title=classification.category,
                    country=classification.impact, # Using country column to store impact (HIGH/MEDIUM/LOW)
                    importance=classification.importance,
                    confidence=classification.confidence,
                    affected_sectors=classification.affected_sectors,
                    affected_indices=classification.affected_indices,
                    bullish_stocks=classification.bullish,
                    bearish_stocks=classification.bearish,
                    duration=str(classification.expected_half_life_hours)
                )
                session.add(causal)
                logger.info(f"[event_pipeline] Mapped: {item.headline[:40]} -> {classification.category}")
        except Exception as e:
            logger.error(f"[event_pipeline] Classification failed for item {item.id}: {e}")
            
    await session.commit()
    logger.info("[event_pipeline] Finished processing event pipeline.")

async def run_pipeline():
    async with AsyncSessionLocal() as session:
        await process_latest_events(session)

if __name__ == "__main__":
    asyncio.run(run_pipeline())
