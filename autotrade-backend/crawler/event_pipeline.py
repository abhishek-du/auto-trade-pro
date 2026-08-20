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
    # Kept so the sector fallback can read tickers/score without re-querying.
    news_by_id = {item.id: item for item in news_items}
    
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
            else:
                # ── Sector-level fallback ────────────────────────────────────
                # The classifier is built around single-company events, so a
                # story about a whole sector matches no category and lands here
                # with classification=None. Before 2026-08-20 that meant the row
                # was silently dropped: five sugar headlines on 19-20 Aug, with
                # tickers extracted and sentiment up to 0.89, produced zero
                # CausalEvents while the sector ran 16%.
                #
                # Deliberately conservative -- a CausalEvent AUTHORISES a trade,
                # so every one of these must hold or nothing is created:
                #   1. the headline names a sector AND uses a collective cue
                #      ("sugar stocks", not "Balrampur Chini Q1")
                #   2. at least NEWS_SECTOR_MIN_TICKERS distinct tickers across
                #      the cluster
                #   3. sentiment score above NEWS_SECTOR_MIN_SCORE
                _created = await _try_sector_fallback(
                    cluster, primary_headline, news_by_id, session
                )
                if not _created:
                    logger.debug(
                        f"[event_pipeline] no category and no sector theme: "
                        f"{primary_headline[:60]}"
                    )
        except Exception as e:
            logger.error(f"[event_pipeline] Classification failed for cluster: {e}")
            
    await session.commit()
    logger.info("[event_pipeline] Finished processing event pipeline.")


async def _try_sector_fallback(cluster, primary_headline, news_by_id, session) -> bool:
    """Create one sector-level CausalEvent when the classifier found no category.

    Returns True if an event was created. Fails CLOSED on every uncertainty:
    an unrecognised theme, too few tickers or a weak score all return False.
    """
    from engine.event_classifier import detect_sector_theme
    from utils.config import settings

    if not getattr(settings, "NEWS_SECTOR_FALLBACK_ENABLED", True):
        return False

    theme = detect_sector_theme(primary_headline)
    if not theme:
        return False

    tickers: set[str] = set()
    best_score = 0.0
    for art in cluster["articles"]:
        item = news_by_id.get(art["id"])
        if item is None:
            continue
        for t in (item.tickers_affected or []):
            if t:
                tickers.add(str(t).upper())
        try:
            best_score = max(best_score, float(item.score or 0.0))
        except (TypeError, ValueError):
            pass

    min_t = int(getattr(settings, "NEWS_SECTOR_MIN_TICKERS", 2))
    min_s = float(getattr(settings, "NEWS_SECTOR_MIN_SCORE", 0.7))
    if len(tickers) < min_t or best_score <= min_s:
        logger.debug(
            f"[event_pipeline] sector '{theme}' below threshold "
            f"(tickers={len(tickers)}/{min_t} score={best_score:.2f}/{min_s}): "
            f"{primary_headline[:50]}"
        )
        return False

    session.add(CausalEvent(
        news_id=cluster["articles"][0]["id"],
        event_title="SECTOR_MOMENTUM",
        country="IN",
        # Below the 95 the LLM classifier assigns to a confirmed single-company
        # event: this is a sector read, not a company fact.
        importance=70,
        confidence=0.75,
        affected_sectors=[theme],
        affected_indices=[],
        bullish_stocks=sorted(tickers),
        bearish_stocks=[],
        duration="24",
    ))
    logger.info(
        f"[event_pipeline] SECTOR FALLBACK: {theme} "
        f"({len(tickers)} tickers, score {best_score:.2f}) <- {primary_headline[:50]}"
    )
    return True


async def run_pipeline():
    async with AsyncSessionLocal() as session:
        await process_latest_events(session)

if __name__ == "__main__":
    asyncio.run(run_pipeline())
