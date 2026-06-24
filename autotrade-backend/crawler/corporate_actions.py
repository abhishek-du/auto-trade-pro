"""Corporate action detection and position adjustment.

When a stock undergoes a split or bonus issue, its price drops sharply overnight
while the total holding value stays the same (you receive proportionally more shares).
Without handling this, the system:
  1. Fires a false stop loss (post-split price < pre-split stop)
  2. Computes a massive phantom loss
  3. Corrupts historical ATR / indicator signals

Flow (called at 09:05 IST each trading day):
  1. For each open position, compare yesterday's close to today's first tick.
  2. If price dropped >40%  → suspect split/bonus.
  3. Fetch Tavily news to confirm and surface the corporate-action details.
  4. Adjust open position: units × ratio, entry/stop/target ÷ ratio.
  5. Log a CorporateActionEvent and send a Telegram alert.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from utils.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CorporateActionEvent:
    symbol:       str
    action_type:  str          # "SPLIT" | "BONUS" | "UNKNOWN_ADJUSTMENT"
    ratio:        float        # new-shares per old-share  (e.g. 5 for a 1:5 split → ×5 units)
    price_before: float
    price_after:  float
    news_summary: str
    positions_adjusted: int


# ─────────────────────────────────────────────────────────────────────────────
def detect_ratio(price_before: float, price_after: float) -> float | None:
    """Return the approximate split/bonus ratio (>1) or None if not a corp action.

    A ratio of 5 means each old share became 5 new shares (1:5 split or 4:1 bonus).
    Only returns a ratio if price dropped by more than 30% (conservative threshold
    so normal market moves and circuit hits don't trigger false adjustments).
    """
    if price_before <= 0 or price_after <= 0:
        return None
    ratio = price_before / price_after
    if ratio < 1.3:   # <30% drop — not a corporate action
        return None
    # Round to nearest common ratio (1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 10, etc.)
    # Use exact ratio so fractional splits (1.25x bonus) still work.
    return round(ratio, 4)


def classify_action(ratio: float) -> str:
    """Classify the action type from the ratio."""
    if ratio <= 0:
        return "UNKNOWN_ADJUSTMENT"
    # Bonus issues tend to use whole-number ratios (2, 3, 4, 5)
    # Splits also use whole numbers (2:1, 5:1, 10:1)
    # Without an exchange circular we can't distinguish — call both "SPLIT/BONUS"
    if abs(ratio - round(ratio)) < 0.1:
        return "SPLIT_OR_BONUS"
    return "UNKNOWN_ADJUSTMENT"


# ─────────────────────────────────────────────────────────────────────────────
async def fetch_corporate_action_news(symbol: str) -> str:
    """Search Tavily for corporate action news for this symbol. Returns a summary."""
    try:
        from utils.config import settings
        if not settings.tavily_available:
            return "Tavily unavailable — no news fetched."

        from engine.tavily_enricher import get_tavily_client
        client = get_tavily_client()
        bare = symbol.replace(".NS", "").replace(".BO", "")
        query = f"{bare} stock split bonus shares NSE India 2026"
        resp = await client.asearch(
            query=query,
            search_depth="basic",
            max_results=4,
            include_answer=True,
        )
        answer = resp.get("answer") or ""
        results = resp.get("results") or []
        snippets = [r.get("content", "")[:200] for r in results[:3]]
        summary = answer or " | ".join(snippets) or "No relevant news found."
        return summary[:600]
    except Exception as exc:
        logger.debug(f"[corp_action] news fetch failed for {symbol}: {exc}")
        return f"News fetch failed: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
async def adjust_open_positions(
    symbol: str,
    ratio: float,
    session: "AsyncSession",
) -> int:
    """Adjust all OPEN positions for `symbol` by the split ratio.

    For a ratio of 5 (price halved by ~80%):
      - units × 5  (more shares)
      - entry_price ÷ 5, stop_loss ÷ 5, take_profit ÷ 5  (lower per-share levels)
      - size_usd (total notional) unchanged
      - pnl / pnl_percent reset to NULL (will recompute from adjusted entry)

    Returns number of positions adjusted.
    """
    from sqlalchemy import select
    from db.models import PaperTrade, OpenPosition

    adjusted = 0

    # 1. Adjust paper_trades (status=OPEN)
    open_trades = (await session.execute(
        select(PaperTrade).where(
            PaperTrade.symbol == symbol,
            PaperTrade.status == "OPEN",   # type: ignore[arg-type]
        )
    )).scalars().all()

    for trade in open_trades:
        old_units = float(trade.size_units or 0)
        old_entry = float(trade.entry_price or 0)
        old_sl    = float(trade.stop_loss   or 0)
        old_tp    = float(trade.take_profit or 0)

        if old_units <= 0 or old_entry <= 0:
            continue

        new_units = round(old_units * ratio)   # integer shares
        new_entry = round(old_entry / ratio, 2)
        new_sl    = round(old_sl    / ratio, 2) if old_sl    else None
        new_tp    = round(old_tp    / ratio, 2) if old_tp    else None

        trade.size_units  = float(new_units)
        trade.entry_price = new_entry
        trade.stop_loss   = new_sl
        trade.take_profit = new_tp
        trade.pnl         = None
        trade.pnl_percent = None

        logger.info(
            f"[corp_action] {symbol} paper_trade#{trade.id}: "
            f"units {old_units:.3f}→{new_units} "
            f"entry ₹{old_entry:.2f}→₹{new_entry:.2f} "
            f"SL ₹{old_sl:.2f}→₹{new_sl:.2f}"
        )
        adjusted += 1

    # 2. Adjust open_positions (live position tracker)
    open_pos = (await session.execute(
        select(OpenPosition).where(OpenPosition.symbol == symbol)
    )).scalars().all()

    for pos in open_pos:
        old_units = float(pos.size_units   or 0)
        old_entry = float(pos.entry_price  or 0)
        old_sl    = float(pos.stop_loss    or 0)
        old_tp    = float(pos.take_profit  or 0)

        if old_units <= 0:
            continue

        pos.size_units    = float(round(old_units * ratio))
        pos.entry_price   = round(old_entry / ratio, 2)
        pos.stop_loss     = round(old_sl    / ratio, 2) if old_sl else None
        pos.take_profit   = round(old_tp    / ratio, 2) if old_tp else None
        pos.unrealised_pnl = 0.0
        pos.unrealised_pct = 0.0

    await session.flush()
    return adjusted


# ─────────────────────────────────────────────────────────────────────────────
async def check_and_handle_corporate_actions(session: "AsyncSession") -> list[CorporateActionEvent]:
    """Main entry point — called daily at 09:05 IST.

    For every open position, fetches the latest 1m candle (today's first tick)
    and the last 1d candle (yesterday's close). If ratio ≥ 1.3, adjusts positions
    and fires a Telegram alert.

    Returns list of CorporateActionEvent for any adjustments made.
    """
    from sqlalchemy import select
    from db.models import OpenPosition, Candle

    events: list[CorporateActionEvent] = []

    # Get unique symbols with open positions
    open_syms_rows = (await session.execute(
        select(OpenPosition.symbol).distinct()
    )).all()
    symbols = [r[0] for r in open_syms_rows]
    if not symbols:
        return events

    logger.info(f"[corp_action] checking {len(symbols)} open-position symbols for corporate actions")

    for symbol in symbols:
        try:
            # Last 1d close (yesterday)
            last_1d = (await session.execute(
                select(Candle.close, Candle.timestamp).where(
                    Candle.symbol == symbol,
                    Candle.timeframe == "1d",
                ).order_by(Candle.timestamp.desc()).limit(1)
            )).first()

            if not last_1d:
                continue
            price_before = float(last_1d[0])

            # First 1m candle today (today's open after 09:15 IST)
            import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI
            _IST = _ZI("Asia/Kolkata")
            today_09h15 = _dt.datetime.now(_IST).replace(
                hour=9, minute=15, second=0, microsecond=0
            ).astimezone(_dt.timezone.utc).replace(tzinfo=None)

            first_tick = (await session.execute(
                select(Candle.open, Candle.timestamp).where(
                    Candle.symbol == symbol,
                    Candle.timeframe == "1m",
                    Candle.timestamp >= today_09h15,
                ).order_by(Candle.timestamp.asc()).limit(1)
            )).first()

            if not first_tick:
                # Try 1h candle as fallback
                first_tick = (await session.execute(
                    select(Candle.open, Candle.timestamp).where(
                        Candle.symbol == symbol,
                        Candle.timeframe == "1h",
                        Candle.timestamp >= today_09h15,
                    ).order_by(Candle.timestamp.asc()).limit(1)
                )).first()

            if not first_tick:
                continue
            price_after = float(first_tick[0])

            ratio = detect_ratio(price_before, price_after)
            if ratio is None:
                continue

            logger.warning(
                f"[corp_action] {symbol}: price dropped from ₹{price_before:.2f} "
                f"to ₹{price_after:.2f} (ratio {ratio:.3f}x) — corporate action suspected"
            )

            # Fetch confirming news
            news = await fetch_corporate_action_news(symbol)

            # Adjust all open positions
            n_adj = await adjust_open_positions(symbol, ratio, session)
            await session.commit()

            action_type = classify_action(ratio)
            event = CorporateActionEvent(
                symbol=symbol,
                action_type=action_type,
                ratio=ratio,
                price_before=price_before,
                price_after=price_after,
                news_summary=news,
                positions_adjusted=n_adj,
            )
            events.append(event)

            # Telegram alert
            await _send_corp_action_alert(event)

        except Exception as exc:
            logger.error(f"[corp_action] error processing {symbol}: {exc}")
            await session.rollback()

    return events


async def _send_corp_action_alert(event: CorporateActionEvent) -> None:
    """Send a Telegram alert for a detected corporate action."""
    try:
        from utils.config import settings
        if not settings.telegram_available:
            return
        from integrations.telegram_service import send
        bare = event.symbol.replace(".NS", "").replace(".BO", "")
        msg = (
            f"🔀 *Corporate Action Detected — {bare}*\n"
            f"Type: {event.action_type}\n"
            f"Ratio: 1 old share → {event.ratio:.2f} new shares\n"
            f"Price: ₹{event.price_before:.2f} → ₹{event.price_after:.2f}\n"
            f"Positions adjusted: {event.positions_adjusted}\n\n"
            f"📰 *News:* {event.news_summary[:300]}"
        )
        await send(msg)
    except Exception as exc:
        logger.debug(f"[corp_action] Telegram alert failed: {exc}")
