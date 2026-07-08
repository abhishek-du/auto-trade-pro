"""Fast market-shock guard — proactive de-risking on a sudden index/news shock.

The 15-minute hub cycle and the score-based (HUB_REVERSAL / SECTOR_REVERSAL)
exits are too slow for a geopolitical shock that gaps the index down in minutes
(e.g. a war headline). This guard runs every 30 s and reacts to the *market*,
not just per-symbol price:

  • Index shock — NIFTY / BANKNIFTY intraday drop over a short look-back window
    (measured from yfinance 1-minute bars, which work in any Celery worker).
  • News shock — a burst of high-severity, negative market headlines.

Two escalating actions on open EQUITY longs:
  • TIGHTEN — raise every stop to lock gains / cap loss just below the live
    price, so the 5-second fast-SL loop exits on any further dip.
  • FLATTEN — close every long at the live price immediately, then block new
    entries for a cooldown so the next trade-loop doesn't re-buy the crash.

Gated OFF by default (settings.ENABLE_SHOCK_GUARD). Long-only / equity-only by
design — this engine holds equity longs; F&O shock handling is out of scope.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger

# Shock levels (ordered — higher is more severe)
SHOCK_NONE    = 0
SHOCK_TIGHTEN = 1
SHOCK_FLATTEN = 2
_LEVEL_NAME = {SHOCK_NONE: "NONE", SHOCK_TIGHTEN: "TIGHTEN", SHOCK_FLATTEN: "FLATTEN"}

# On TIGHTEN, pull each stop to within this % of the live price (locks most of
# an open gain; caps further downside on a losing long). Kept as a module
# constant to avoid config sprawl — the trigger thresholds are the tunable part.
_TIGHTEN_TRAIL_PCT = 0.5

# Negative headlines matching these stems mark a market-wide shock (geopolitics
# or panic selling) rather than routine single-stock news.
_SHOCK_KEYWORDS = (
    # geopolitical catalysts
    "war", "strike", "missile", "attack", "invasion", "ceasefire", "nuclear",
    "emergency", "sanction", "terror", "bomb", "escalat", "conflict",
    "airstrike", "retaliat", "hostilit",
    # market-panic language
    "crash", "plunge", "collapse", "tumble", "slump", "selloff", "sell-off",
    "rout", "bloodbath", "tank", "sink", "nosediv", "freefall",
)


@dataclass
class ShockAssessment:
    level:           int   = SHOCK_NONE
    index_drop_pct:  float = 0.0     # worst (most negative) index move over window
    index_detail:    str   = ""
    news_hits:       int   = 0
    news_sample:     list[str] = field(default_factory=list)
    reason:          str   = ""

    @property
    def level_name(self) -> str:
        return _LEVEL_NAME[self.level]


# ── Signal computation ────────────────────────────────────────────────────────

def _sync_worst_index_drop(symbols: list[str], window_min: int) -> tuple[float, str]:
    """Worst intraday % move across index symbols over the last `window_min`.

    Returns (worst_drop_pct, detail). Negative = down. yfinance 1-minute bars are
    a plain HTTP call, so this works inside a forked Celery worker where the
    in-process tick caches are empty.
    """
    import yfinance as yf

    worst_pct = 0.0
    worst_sym = ""
    for sym in symbols:
        try:
            h = yf.Ticker(sym).history(period="1d", interval="1m")
            if h is None or len(h) < window_min + 1:
                continue
            last = float(h["Close"].iloc[-1])
            ref  = float(h["Close"].iloc[-(window_min + 1)])
            if ref <= 0:
                continue
            pct = (last - ref) / ref * 100.0
            if pct < worst_pct:
                worst_pct = pct
                worst_sym = f"{sym} {pct:+.2f}% in {window_min}m ({ref:.0f}→{last:.0f})"
        except Exception as exc:
            logger.debug(f"[shock] index drop fetch failed for {sym}: {exc}")
            continue
    return round(worst_pct, 2), worst_sym


async def _news_shock_hits(session: AsyncSession, window_min: int) -> tuple[int, list[str]]:
    """Count high-severity negative market headlines in the last `window_min`."""
    from db.models import NewsItem
    from sqlalchemy import func, or_

    cutoff = datetime.utcnow() - timedelta(minutes=window_min)
    try:
        rows = (await session.execute(
            select(NewsItem.headline)
            .where(
                func.coalesce(NewsItem.published_at, NewsItem.crawled_at) >= cutoff,
                NewsItem.sentiment == "negative",
            )
            .order_by(func.coalesce(NewsItem.published_at, NewsItem.crawled_at).desc())
            .limit(200)
        )).scalars().all()
    except Exception as exc:
        logger.debug(f"[shock] news query failed: {exc}")
        return 0, []

    hits: list[str] = []
    for headline in rows:
        low = (headline or "").lower()
        if any(kw in low for kw in _SHOCK_KEYWORDS):
            hits.append(headline)
    return len(hits), hits[:5]


async def assess_market_shock(session: AsyncSession) -> ShockAssessment:
    """Combine the index-drop and news signals into a single shock level."""
    symbols = [s.strip() for s in settings.SHOCK_INDEX_SYMBOLS.split(",") if s.strip()]
    loop = asyncio.get_event_loop()
    drop_pct, detail = await loop.run_in_executor(
        None, _sync_worst_index_drop, symbols, int(settings.SHOCK_INDEX_WINDOW_MIN)
    )
    news_hits, news_sample = await _news_shock_hits(
        session, int(settings.SHOCK_NEWS_WINDOW_MIN)
    )

    # Index-driven base level
    level = SHOCK_NONE
    if drop_pct <= -abs(settings.SHOCK_FLATTEN_PCT):
        level = SHOCK_FLATTEN
    elif drop_pct <= -abs(settings.SHOCK_TIGHTEN_PCT):
        level = SHOCK_TIGHTEN

    # News escalation: a burst of shock headlines lifts the level one notch
    # (NONE→TIGHTEN, TIGHTEN→FLATTEN) so we react even before the index prints
    # the full move.
    news_shock = news_hits >= int(settings.SHOCK_NEWS_MIN_HITS)
    if news_shock:
        level = min(SHOCK_FLATTEN, level + 1)

    reasons = []
    if drop_pct <= -abs(settings.SHOCK_TIGHTEN_PCT):
        reasons.append(f"index {detail}")
    if news_shock:
        reasons.append(f"{news_hits} shock headlines/{settings.SHOCK_NEWS_WINDOW_MIN}m")

    return ShockAssessment(
        level=level,
        index_drop_pct=drop_pct,
        index_detail=detail,
        news_hits=news_hits,
        news_sample=news_sample,
        reason=" + ".join(reasons),
    )


# ── Action ────────────────────────────────────────────────────────────────────

async def _live_prices_for(symbols: list[str]) -> dict[str, float]:
    """Kite LTP first, yfinance backstop — same reliable path the fast-SL loop uses."""
    out: dict[str, float] = {}
    if not symbols:
        return out
    try:
        from crawler.zerodha_market import get_live_prices
        quotes = await get_live_prices(symbols)
        for sym, q in (quotes or {}).items():
            px = q.get("price") or q.get("last_price")
            if px and px > 0:
                out[sym] = float(px)
    except Exception as exc:
        logger.debug(f"[shock] Kite LTP fetch failed: {exc}")
    missing = [s for s in symbols if s not in out]
    if missing:
        try:
            from crawler.live_prices import yfinance_ltp_batch
            out.update(await yfinance_ltp_batch(missing))
        except Exception as exc:
            logger.debug(f"[shock] yfinance backstop failed: {exc}")
    return out


async def apply_shock_action(assessment: ShockAssessment, session: AsyncSession) -> dict:
    """Tighten stops (level 1) or flatten longs (level 2) on open EQUITY longs."""
    from db.models import OpenPosition, TradeDirection
    from paper_trading.trade_simulator import close_paper_trade

    longs = [
        p for p in (await session.execute(select(OpenPosition))).scalars().all()
        if p.direction == TradeDirection.BUY
        and getattr(p, "instrument_type", "EQUITY") == "EQUITY"
    ]
    result = {"level": assessment.level_name, "reason": assessment.reason,
              "closed": [], "tightened": [], "skipped": []}
    if not longs:
        return result

    prices = await _live_prices_for([p.symbol for p in longs])

    for pos in longs:
        price = prices.get(pos.symbol, 0.0)
        if price <= 0:
            result["skipped"].append(pos.symbol)   # can't act without a live price
            continue

        if assessment.level == SHOCK_FLATTEN:
            try:
                trade = await close_paper_trade(pos, price, "MARKET_SHOCK_FLATTEN", session)
                await session.commit()
                result["closed"].append({"symbol": pos.symbol, "price": price, "pnl": trade.pnl})
                logger.warning(
                    f"[shock] FLATTEN {pos.symbol} @ ₹{price:.2f} | pnl=₹{trade.pnl:,.2f} | "
                    f"{assessment.reason}"
                )
            except Exception as exc:
                await session.rollback()
                result["skipped"].append(pos.symbol)
                logger.warning(f"[shock] flatten failed for {pos.symbol}: {exc}")
        else:  # SHOCK_TIGHTEN — lock stops just under the live price, never loosen
            new_stop = round(price * (1 - _TIGHTEN_TRAIL_PCT / 100.0), 2)
            old_stop = float(pos.stop_loss or 0.0)
            if new_stop > old_stop:
                pos.stop_loss = new_stop
                await session.commit()
                result["tightened"].append(
                    {"symbol": pos.symbol, "old_stop": old_stop, "new_stop": new_stop}
                )
                logger.info(
                    f"[shock] TIGHTEN {pos.symbol} stop ₹{old_stop:.2f} → ₹{new_stop:.2f} "
                    f"(live ₹{price:.2f}) | {assessment.reason}"
                )

    # After a flatten, block new entries for the cooldown window so the 60 s
    # trade-loop / 15-min hub cycle doesn't immediately re-buy into the shock.
    if assessment.level == SHOCK_FLATTEN and result["closed"]:
        try:
            from utils.runtime_config import RuntimeConfig
            until = datetime.utcnow() + timedelta(minutes=int(settings.SHOCK_COOLDOWN_MIN))
            await RuntimeConfig.set(session, "shock_cooldown_until", until.isoformat())
            await session.commit()
            logger.warning(
                f"[shock] entry cooldown set until {until.isoformat()}Z "
                f"({settings.SHOCK_COOLDOWN_MIN}m)"
            )
        except Exception as exc:
            logger.warning(f"[shock] failed to set entry cooldown: {exc}")

    return result


async def run_shock_guard(session: AsyncSession) -> dict | None:
    """Assess and (if a shock is live) act. Returns a summary or None when calm."""
    if not settings.ENABLE_SHOCK_GUARD:
        return None
    assessment = await assess_market_shock(session)
    if assessment.level == SHOCK_NONE:
        return None
    logger.warning(
        f"[shock] {assessment.level_name} detected — {assessment.reason}"
    )
    return await apply_shock_action(assessment, session)
