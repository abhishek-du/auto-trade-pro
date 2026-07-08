# News API — cached headlines with FinBERT sentiment scores.

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from api.schemas import NewsItemOut, SentimentOut
from crawler.news_crawler import get_market_sentiment
from db.database import get_db
from db.models import NewsItem
from engine.news_impact import is_high_impact_news
from utils.config import settings

router = APIRouter(tags=["News"])


def _item_out(item: NewsItem) -> NewsItemOut:
    return NewsItemOut(
        id=item.id,
        headline=item.headline,
        source=item.source,
        url=item.url,
        sentiment=item.sentiment,
        score=item.score,
        tickers_affected=item.tickers_affected,
        published_at=item.published_at,
        crawled_at=item.crawled_at,
        high_impact=is_high_impact_news(
            item.headline, item.sentiment, item.score,
            settings.NEWS_ALERT_MIN_ABS_SCORE,
        ),
    )


@router.get(
    "/",
    response_model=list[NewsItemOut],
    summary="Last 30 cached news items with optional sentiment filter",
)
async def get_recent_news(
    sentiment: Optional[str] = Query(
        None, description="Filter by sentiment: positive | negative | neutral"
    ),
    limit: int = Query(30, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(NewsItem).order_by(desc(NewsItem.crawled_at)).limit(limit)
    if sentiment:
        query = query.where(NewsItem.sentiment == sentiment.lower())
    result = await db.execute(query)
    return [_item_out(item) for item in result.scalars().all()]


@router.get(
    "/alerts",
    response_model=list[NewsItemOut],
    summary="High-impact market-shock headlines only (for the Market Alerts strip)",
)
async def get_high_impact_news(
    limit: int = Query(15, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Return recent crash-capable headlines (market-shock catalyst + strong
    negative sentiment), newest first — the same signal that fires the Telegram
    alert. Lets the /news page surface these on top instead of burying them in
    the chronological feed. Scans a recent window so the DB filter stays cheap.
    """
    rows = (await db.execute(
        select(NewsItem).order_by(desc(NewsItem.crawled_at)).limit(300)
    )).scalars().all()
    hits = [
        item for item in rows
        if is_high_impact_news(item.headline, item.sentiment, item.score,
                               settings.NEWS_ALERT_MIN_ABS_SCORE)
    ]
    return [_item_out(item) for item in hits[:limit]]


@router.get(
    "/sentiment/{symbol:path}",
    response_model=SentimentOut,
    summary="Aggregate FinBERT sentiment score for a symbol",
)
async def get_symbol_sentiment(symbol: str, db: AsyncSession = Depends(get_db)):
    """Average sentiment score from the last 10 news items that mention this symbol.
    Returns a value between -1 (very negative) and +1 (very positive)."""
    score = await get_market_sentiment(symbol.upper(), db)

    if score > 0.2:
        description = "positive"
    elif score < -0.2:
        description = "negative"
    else:
        description = "neutral"

    return SentimentOut(
        symbol=symbol.upper(),
        avg_score=round(score, 4),
        description=description,
    )


@router.get(
    "/narrative",
    summary="Live sector narrative boost cache (Eagle Eyes style top-down themes)",
)
async def get_narrative_intelligence():
    """Returns the current sector narrative boost cache built from RSS + Telegram.
    Auto-refreshes on first call or if cache is stale (>10 min).
    """
    import time
    import datetime
    import engine.narrative_engine as _ne  # import module, not variables

    # Auto-refresh if cache is empty or stale (>10 min)
    age = time.time() - _ne._LAST_REFRESH if _ne._LAST_REFRESH else 99999
    if not _ne.NARRATIVE_BOOST_CACHE or age > 600:
        await _ne.refresh_narrative_cache(force=True)

    cache       = _ne.NARRATIVE_BOOST_CACHE
    lr          = _ne._LAST_REFRESH
    age_seconds = int(time.time() - lr) if lr else None
    last_updated = (
        datetime.datetime.utcfromtimestamp(lr).isoformat() + "Z" if lr else None
    )

    return {
        "hot_sectors":       cache,
        "summary":           _ne.get_narrative_summary(),
        "last_updated":      last_updated,
        "cache_age_seconds": age_seconds,
        "total_hot_sectors": len(cache),
    }



