# News API — cached headlines with FinBERT sentiment scores.

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from api.schemas import NewsItemOut, SentimentOut
from crawler.news_crawler import get_market_sentiment
from db.database import get_db
from db.models import NewsItem

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
