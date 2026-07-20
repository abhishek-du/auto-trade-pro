# News API — cached headlines with FinBERT sentiment scores.

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from api.schemas import NewsItemOut, SentimentOut, SSEAnnouncementOut, CausalEventOut
from crawler.news_crawler import get_market_sentiment
from db.database import get_db
from db.models import NewsItem, SSEAnnouncement, CausalEvent
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
        category=item.category,
        company=item.company,
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
    "/announcements",
    response_model=list[NewsItemOut],
    summary="High-impact NSE corporate announcements only (results, M&A, dividends, credit rating, etc.)",
)
async def get_corporate_announcements(
    limit: int = Query(30, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Rows written by the News-First Discovery Engine's NSE corporate-
    announcements poller — a distinct source from the RSS/headline feed, kept
    on its own endpoint so the frontend can render them as a separate section
    instead of them being buried in the general chronological feed."""
    result = await db.execute(
        select(NewsItem)
        .where(NewsItem.source == "NSE-Announcements")
        .order_by(desc(NewsItem.crawled_at))
        .limit(limit)
    )
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


@router.get(
    "/sse-announcements",
    response_model=list[SSEAnnouncementOut],
    summary="NSE Social Stock Exchange (NPO/Social Enterprise) filings — informational only",
)
async def get_sse_announcements(
    limit: int = Query(30, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Full-fidelity feed from tasks.india_tasks.sync_sse_announcements —
    every field NSE returns for index=sse is kept (see SSEAnnouncement model),
    unlike the equities feed which is condensed into a single headline."""
    result = await db.execute(
        select(SSEAnnouncement).order_by(desc(SSEAnnouncement.crawled_at)).limit(limit)
    )
    out = []
    for row in result.scalars().all():
        out.append(SSEAnnouncementOut(
            id=row.id,
            comp_name=row.comp_name,
            symbol=row.symbol,
            an_desc=row.an_desc,
            text=row.text,
            an_attach=row.an_attach,
            att_file_size=row.att_file_size,
            has_xbrl=row.has_xbrl,
            ann_date=row.ann_date,
            crawled_at=row.crawled_at,
            diff_time=row.diff_time,
            sentiment=row.sentiment,
            score=row.score
        ))
    return out


@router.get(
    "/causal",
    response_model=list[CausalEventOut],
    summary="Get recent AI-classified Causal Events (Knowledge Graph nodes)"
)
async def get_causal_events(
    limit: int = Query(500, le=1000),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(CausalEvent, NewsItem.headline, NewsItem.source)
        .outerjoin(NewsItem, CausalEvent.news_id == NewsItem.id)
        .order_by(desc(CausalEvent.created_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    
    out = []
    for event, headline, source in result.all():
        out.append(CausalEventOut(
            id=event.id,
            news_id=event.news_id,
            event_title=event.event_title or "",
            country=event.country or "Global",
            importance=event.importance or 0.0,
            confidence=event.confidence or 0.0,
            affected_sectors=event.affected_sectors or [],
            affected_indices=event.affected_indices or [],
            bullish_stocks=event.bullish_stocks or [],
            bearish_stocks=event.bearish_stocks or [],
            duration=event.duration or "",
            created_at=event.created_at,
            headline=headline,
            source=source
        ))
    return out
