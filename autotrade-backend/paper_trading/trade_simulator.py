"""Virtual trade execution engine for AutoTrade Pro paper trading.

ALL trades are VIRTUAL — this simulates a real brokerage with realistic
slippage, but no real money is ever involved.

Public API (new high-level functions)
--------------------------------------
open_paper_trade(signal, position_size, session) -> PaperTrade
close_paper_trade(position, close_price, reason, session) -> PaperTrade
update_positions_with_current_prices(session) -> list[dict]

Legacy (used by position_tracker.py)
--------------------------------------
FillResult dataclass
TradeSimulator.execute_buy / execute_sell / size_from_risk
"""

import random
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Candle, OpenPosition, PaperTrade, TradeDirection, TradeStatus
from paper_trading.simulation_logger import SimulationLogger
from paper_trading.virtual_wallet import VirtualWallet
from utils.config import settings
from utils.logger import logger

# ── Slippage constants ────────────────────────────────────────────────────────

# New-style: uniform fraction of entry price (spec §1)
_SLIP_MIN = 0.0001
_SLIP_MAX = 0.0003

# Legacy: basis-point range used by TradeSimulator
_SLIP_BPS_MIN = 2
_SLIP_BPS_MAX = 8
_MAX_POSITION_PCT = 0.20


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy dataclass + TradeSimulator (kept for position_tracker.py compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FillResult:
    symbol:          str
    direction:       str      # "BUY" | "SELL"
    requested_price: float
    fill_price:      float    # after simulated slippage
    size_units:      float
    size_usd:        float    # fill_price * size_units
    slippage_pct:    float
    slippage_usd:    float
    commission:      float    # always 0.0 for paper trading
    executed_at:     datetime


class TradeSimulator:
    """Stateless fill simulator — BUY fills above, SELL fills below (adverse)."""

    @staticmethod
    def _apply_slippage(price: float, direction: str) -> tuple[float, float]:
        bps  = random.uniform(_SLIP_BPS_MIN, _SLIP_BPS_MAX)
        frac = bps / 10_000
        fill = price * (1 + frac) if direction == "BUY" else price * (1 - frac)
        return round(fill, 6), round(frac, 8)

    @staticmethod
    def size_from_risk(
        balance: float,
        entry_price: float,
        stop_loss_price: float,
        risk_fraction: float | None = None,
    ) -> tuple[float, float]:
        rf            = risk_fraction if risk_fraction is not None else settings.MAX_RISK_PER_TRADE
        stop_distance = abs(entry_price - stop_loss_price)
        if stop_distance == 0:
            return 0.0, 0.0
        risk_usd   = balance * rf
        size_units = risk_usd / stop_distance
        size_usd   = size_units * entry_price
        max_usd    = balance * _MAX_POSITION_PCT
        if size_usd > max_usd:
            size_usd   = max_usd
            size_units = size_usd / entry_price
        return round(size_units, 6), round(size_usd, 4)

    @staticmethod
    def execute_buy(symbol: str, requested_price: float, size_units: float) -> FillResult:
        fill_price, slip_frac = TradeSimulator._apply_slippage(requested_price, "BUY")
        size_usd = round(fill_price * size_units, 4)
        slip_usd = round(abs(fill_price - requested_price) * size_units, 4)
        logger.info(
            f"PAPER BUY  {symbol}: units={size_units:.4f} "
            f"req={requested_price:.4f} fill={fill_price:.4f} "
            f"slip={slip_frac*100:.4f}% (${slip_usd:.4f}) cost=${size_usd:.2f}"
        )
        return FillResult(symbol, "BUY", requested_price, fill_price,
                          size_units, size_usd, slip_frac, slip_usd, 0.0, datetime.utcnow())

    @staticmethod
    def execute_sell(symbol: str, requested_price: float, size_units: float) -> FillResult:
        fill_price, slip_frac = TradeSimulator._apply_slippage(requested_price, "SELL")
        size_usd = round(fill_price * size_units, 4)
        slip_usd = round(abs(requested_price - fill_price) * size_units, 4)
        logger.info(
            f"PAPER SELL {symbol}: units={size_units:.4f} "
            f"req={requested_price:.4f} fill={fill_price:.4f} "
            f"slip={slip_frac*100:.4f}% (${slip_usd:.4f}) proceeds=${size_usd:.2f}"
        )
        return FillResult(symbol, "SELL", requested_price, fill_price,
                          size_units, size_usd, slip_frac, slip_usd, 0.0, datetime.utcnow())


# ═══════════════════════════════════════════════════════════════════════════════
# New high-level execution functions
# ═══════════════════════════════════════════════════════════════════════════════

async def open_paper_trade(
    signal,                     # TradingSignal (import deferred to avoid circular)
    position_size: dict,
    session: AsyncSession,
) -> PaperTrade:
    """Open a virtual paper trade from a TradingSignal.

    Steps
    -----
    1. Simulate adverse slippage on entry price.
    2. Persist PaperTrade + OpenPosition records.
    3. Deduct 10 % margin from VirtualWallet.
    4. Write TRADE_OPENED entry to SimulationLog.
    5. Log to loguru.

    Parameters
    ----------
    signal        : TradingSignal with action, entry_price, stop_loss, take_profit.
    position_size : dict from calculate_position_size() — must have 'units' + 'usd_value'.
    session       : Active async SQLAlchemy session (caller owns the transaction).

    Returns
    -------
    PaperTrade — the newly persisted trade record.
    """
    now = datetime.utcnow()

    # ── Step 1: Slippage simulation ───────────────────────────────────────────
    slippage      = random.uniform(_SLIP_MIN, _SLIP_MAX) * signal.entry_price
    if signal.action == "BUY":
        actual_entry = signal.entry_price + slippage
    else:
        actual_entry = signal.entry_price - slippage
    slippage_applied = actual_entry - signal.entry_price

    direction = TradeDirection(signal.action)
    units     = position_size["units"]
    usd_value = position_size["usd_value"]

    # ── Step 2a: Persist PaperTrade ───────────────────────────────────────────
    trade = PaperTrade(
        symbol=signal.symbol,
        direction=direction,
        status=TradeStatus.OPEN,
        entry_price=round(actual_entry, 6),
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        size_units=units,
        size_usd=usd_value,
        signal_confidence=signal.confidence,
        pattern_name=(signal.patterns_detected[0] if signal.patterns_detected else "")[:80],
        ai_reason="\n".join(signal.reasoning_points),
        indicator_snapshot={
            "indicator_score": signal.indicator_score,
            "pattern_score":   signal.pattern_score,
            "sentiment_score": signal.sentiment_score,
            "final_score":     signal.final_score,
        },
        news_sentiment_score=signal.sentiment_score / 100.0,
        slippage_applied=round(slippage_applied, 6),
        opened_at=now,
    )
    session.add(trade)
    await session.flush()                           # populate trade.id

    # ── Step 2b: Persist OpenPosition ─────────────────────────────────────────
    position = OpenPosition(
        symbol=signal.symbol,
        direction=direction,
        entry_price=round(actual_entry, 6),
        current_price=round(actual_entry, 6),
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        size_units=units,
        size_usd=usd_value,
        unrealised_pnl=0.0,
        unrealised_pct=0.0,
        trade_id=trade.id,
        opened_at=now,
    )
    session.add(position)
    await session.flush()

    # ── Step 3: Deduct 10 % margin ────────────────────────────────────────────
    margin = usd_value * 0.1
    ok, msg = await VirtualWallet.deduct_margin(session, margin, signal.symbol)
    if not ok:
        logger.warning(f"open_paper_trade: margin deduction failed for {signal.symbol}: {msg}")

    # ── Step 4: Simulation log ────────────────────────────────────────────────
    rr = (
        abs(signal.take_profit - actual_entry) / abs(signal.stop_loss - actual_entry)
        if abs(signal.stop_loss - actual_entry) > 0 else 0.0
    )
    log_msg = (
        f"OPENED {signal.action} {signal.symbol} at {actual_entry:.5f} "
        f"| SL: {signal.stop_loss:.5f} | TP: {signal.take_profit:.5f} "
        f"| Confidence: {signal.confidence:.0f}% | Size: ${usd_value:.2f}"
    )
    await SimulationLogger.log(
        session, "TRADE_OPENED", signal.symbol, log_msg,
        {
            "symbol":      signal.symbol,
            "direction":   signal.action,
            "entry_price": round(actual_entry, 6),
            "stop_loss":   signal.stop_loss,
            "take_profit": signal.take_profit,
            "units":       units,
            "usd_value":   usd_value,
            "confidence":  signal.confidence,
            "patterns":    signal.patterns_detected,
            "reasoning":   signal.reasoning_points,
            "risk_reward": round(rr, 2),
        },
    )

    # ── Step 5: loguru ────────────────────────────────────────────────────────
    logger.info(
        f"TRADE OPENED │ {signal.action} {signal.symbol} │ "
        f"Entry: {actual_entry:.5f} │ "
        f"SL: {signal.stop_loss:.5f} │ "
        f"TP: {signal.take_profit:.5f} │ "
        f"Size: ${usd_value:.2f}"
    )
    return trade


async def close_paper_trade(
    position:    OpenPosition,
    close_price: float,
    reason:      str,
    session:     AsyncSession,
) -> PaperTrade:
    """Close an open virtual position at the given price.

    Valid reason values: 'TAKE_PROFIT', 'STOP_LOSS', 'MANUAL', 'SIGNAL_REVERSAL'.

    Steps
    -----
    1. Calculate realised PnL.
    2. Update PaperTrade status / exit fields.
    3. Delete the OpenPosition snapshot.
    4. Return margin + PnL to VirtualWallet.
    5. Write TRADE_CLOSED entry to SimulationLog.
    6. loguru.success on profit, loguru.warning on loss.

    Returns
    -------
    The updated PaperTrade record.
    """
    # Fetch the parent trade
    trade_row = await session.execute(
        select(PaperTrade).where(PaperTrade.id == position.trade_id)
    )
    trade = trade_row.scalar_one()
    now   = datetime.utcnow()

    # ── Step 1: P&L ───────────────────────────────────────────────────────────
    if position.direction == TradeDirection.BUY:
        pnl = (close_price - trade.entry_price) * trade.size_units
    else:
        pnl = (trade.entry_price - close_price) * trade.size_units

    notional    = trade.entry_price * trade.size_units
    pnl_percent = (pnl / notional * 100) if notional > 0 else 0.0

    # ── Step 2: Update PaperTrade ─────────────────────────────────────────────
    duration_hours = (now - trade.opened_at).total_seconds() / 3600

    trade.exit_price  = round(close_price, 6)
    trade.pnl         = round(pnl, 4)
    trade.pnl_percent = round(pnl_percent, 4)
    trade.closed_at   = now
    trade.status      = TradeStatus.STOPPED if reason == "STOP_LOSS" else TradeStatus.CLOSED

    # ── Step 3: Delete OpenPosition ───────────────────────────────────────────
    await session.execute(
        delete(OpenPosition).where(OpenPosition.id == position.id)
    )
    await session.flush()

    # ── Step 4: Return margin + PnL to wallet ─────────────────────────────────
    margin      = trade.size_usd * 0.1
    new_balance = await VirtualWallet.return_margin(session, margin, pnl, trade.symbol)

    # ── Step 5: Simulation log ────────────────────────────────────────────────
    sign    = "+" if pnl >= 0 else ""
    log_msg = (
        f"CLOSED {position.direction.value} {trade.symbol} "
        f"| P&L: {sign}${pnl:.2f} ({sign}{pnl_percent:.1f}%) "
        f"| Reason: {reason} "
        f"| New Balance: ${new_balance:.2f}"
    )
    await SimulationLogger.log(
        session, "TRADE_CLOSED", trade.symbol, log_msg,
        {
            "symbol":            trade.symbol,
            "direction":         position.direction.value,
            "entry_price":       trade.entry_price,
            "exit_price":        round(close_price, 6),
            "pnl":               round(pnl, 4),
            "pnl_percent":       round(pnl_percent, 2),
            "reason":            reason,
            "duration_hours":    round(duration_hours, 2),
            "opening_reasoning": trade.ai_reason,
        },
    )

    # ── Step 6: loguru (success vs warning — losses are normal, not errors) ───
    if pnl > 0:
        logger.success(
            f"TRADE CLOSED ✓ │ {position.direction.value} {trade.symbol} │ "
            f"P&L: +${pnl:.2f} ({pnl_percent:.1f}%) │ "
            f"Reason: {reason} │ Balance: ${new_balance:.2f}"
        )
    else:
        logger.warning(
            f"TRADE CLOSED ✗ │ {position.direction.value} {trade.symbol} │ "
            f"P&L: ${pnl:.2f} ({pnl_percent:.1f}%) │ "
            f"Reason: {reason} │ Balance: ${new_balance:.2f}"
        )

    return trade


async def update_positions_with_current_prices(session: AsyncSession) -> list[dict]:
    """Refresh all open positions with the latest candle prices.

    For each OpenPosition:
      • Looks up the most recent 1h candle close for that symbol.
      • Updates current_price and unrealised_pnl.
      • Auto-closes any position that has hit its stop-loss or take-profit.

    After processing, syncs the total unrealised PnL into the VirtualWallet.

    Returns
    -------
    list[dict]
        One entry per auto-closed position — useful for WebSocket broadcast.
        Each dict: {trade_id, symbol, reason, exit_price, pnl}
    """
    result    = await session.execute(select(OpenPosition))
    positions = list(result.scalars().all())

    auto_closed: list[dict] = []

    for pos in positions:
        # ── Fetch latest candle price ─────────────────────────────────────────
        candle_row = await session.execute(
            select(Candle)
            .where(Candle.symbol == pos.symbol, Candle.timeframe == "1h")
            .order_by(Candle.timestamp.desc())
            .limit(1)
        )
        candle = candle_row.scalar_one_or_none()
        if candle is None:
            logger.debug(f"update_positions: no candle data for {pos.symbol} — skipping")
            continue

        price = candle.close

        # ── SL/TP check ────────────────────────────────────────────────────────
        hit_sl = (
            pos.direction == TradeDirection.BUY  and price <= pos.stop_loss
            or pos.direction == TradeDirection.SELL and price >= pos.stop_loss
        )
        hit_tp = (
            pos.direction == TradeDirection.BUY  and price >= pos.take_profit
            or pos.direction == TradeDirection.SELL and price <= pos.take_profit
        )

        if hit_sl or hit_tp:
            reason       = "STOP_LOSS" if hit_sl else "TAKE_PROFIT"
            closed_trade = await close_paper_trade(pos, price, reason, session)
            auto_closed.append({
                "trade_id":   closed_trade.id,
                "symbol":     closed_trade.symbol,
                "reason":     reason,
                "exit_price": price,
                "pnl":        closed_trade.pnl,
            })
            continue

        # ── Update unrealised PnL ──────────────────────────────────────────────
        if pos.direction == TradeDirection.BUY:
            upnl = (price - pos.entry_price) * pos.size_units
        else:
            upnl = (pos.entry_price - price) * pos.size_units

        notional        = pos.entry_price * pos.size_units
        upct            = (upnl / notional * 100) if notional > 0 else 0.0
        pos.current_price   = price
        pos.unrealised_pnl  = round(upnl, 4)
        pos.unrealised_pct  = round(upct, 4)

    await session.flush()

    # ── Sync wallet unrealised PnL ────────────────────────────────────────────
    remaining_result  = await session.execute(select(OpenPosition))
    remaining         = remaining_result.scalars().all()
    total_unrealised  = sum(p.unrealised_pnl for p in remaining)
    await VirtualWallet.update_unrealised_pnl(session, total_unrealised)

    if auto_closed:
        logger.info(
            f"update_positions: {len(auto_closed)} position(s) auto-closed  "
            f"({', '.join(d['reason'] + ' ' + d['symbol'] for d in auto_closed)})"
        )

    return auto_closed
