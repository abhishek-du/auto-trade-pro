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
from sqlalchemy.orm import selectinload

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
    3. Deduct full trade value from VirtualWallet (full-equity, no leverage).
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

    # ── Trade management levels ───────────────────────────────────────────────
    # signal.take_profit = Target 1 (first checkpoint / trailing trigger).
    # signal.target_2    = final target — the position RIDES to T2 with a 1×ATR
    # trailing stop activated once T1 is touched (see update_positions_…).
    # If target_2 wasn't set (legacy caller), the position uses T1 as its TP.
    target_1 = signal.take_profit
    target_2 = getattr(signal, "target_2", 0.0) or target_1
    atr      = getattr(signal, "atr", 0.0) or 0.0
    # Trail distance: 1×ATR — backtested as optimal for NSE equity volatility.
    # 1.5× ATR gives back too much profit in the choppy markets that dominate 2025.
    trail_dist = atr if atr > 0 else round(actual_entry * 0.02, 4)
    # The position's hard take-profit is the FINAL target so winners can run.
    position_tp = target_2

    trade_meta = {
        "target_1":   round(target_1, 4),
        "target_2":   round(target_2, 4),
        "atr":        round(atr, 4),
        "trail_dist": round(trail_dist, 4),
        "trailing":   False,                 # becomes True once T1 is hit
        "peak_price": round(actual_entry, 6),
        "level_source": next((p.split("[", 1)[1].split("]", 1)[0]
                              for p in signal.reasoning_points if p.startswith("Trade levels [")), "unknown"),
        # MFE/MAE running trackers (updated every tick in update_positions_…)
        "peak_upnl":   0.0,
        "trough_upnl": 0.0,
    }

    # ── Attribution values (already in signal, just not persisted until now) ─
    _initial_r   = round(abs(actual_entry - signal.stop_loss) * units, 2)
    _conf_bucket = str((int(signal.confidence) // 10) * 10)
    _strategy    = getattr(signal, "strategy", getattr(signal, "strategy_name", None))
    _regime_entr = getattr(signal, "regime", None)
    _entry_rsn   = (signal.reasoning_points[0] if signal.reasoning_points else "")[:40]

    # ── Step 2a: Persist PaperTrade ───────────────────────────────────────────
    trade = PaperTrade(
        symbol=signal.symbol,
        direction=direction,
        status=TradeStatus.OPEN,
        entry_price=round(actual_entry, 6),
        stop_loss=signal.stop_loss,
        take_profit=position_tp,
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
            "trade_mgmt":      trade_meta,
        },
        news_sentiment_score=signal.sentiment_score / 100.0,
        slippage_applied=round(slippage_applied, 6),
        opened_at=now,
        # Attribution
        strategy_name=(_strategy[:40] if _strategy else None),
        regime_at_entry=(_regime_entr[:20] if _regime_entr else None),
        entry_reason=_entry_rsn,
        confidence_bucket=_conf_bucket,
        instrument_segment="EQUITY_CNC",
        initial_risk_inr=_initial_r,
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
        take_profit=position_tp,
        size_units=units,
        size_usd=usd_value,
        unrealised_pnl=0.0,
        unrealised_pct=0.0,
        trade_id=trade.id,
        opened_at=now,
    )
    session.add(position)
    await session.flush()

    # ── Step 3: Deduct full trade value (full-equity model, no leverage) ─────
    margin = usd_value
    ok, msg = await VirtualWallet.deduct_margin(session, margin, signal.symbol)
    if not ok:
        # Roll back the persisted records so a failed balance check leaves no orphaned rows.
        await session.execute(delete(OpenPosition).where(OpenPosition.id == position.id))
        await session.execute(delete(PaperTrade).where(PaperTrade.id == trade.id))
        await session.flush()
        logger.warning(f"open_paper_trade: BLOCKED {signal.symbol} — {msg}")
        raise ValueError(f"Insufficient virtual funds to open {signal.symbol}: {msg}")

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
    4. Return full trade value + PnL to VirtualWallet.
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
    # If a partial scale-out fired at T1, pos.size_units was reduced to the
    # remaining half. Use position.size_units (remaining) for the final leg,
    # then add the already-realised partial_pnl for the total trade P&L.
    snap_data   = (trade.indicator_snapshot or {}) if trade.indicator_snapshot else {}
    partial_pnl = float(snap_data.get("trade_mgmt", {}).get("partial_pnl", 0.0))
    remaining   = position.size_units   # may be < trade.size_units after partial

    if position.direction == TradeDirection.BUY:
        pnl = (close_price - trade.entry_price) * remaining + partial_pnl
    else:
        pnl = (trade.entry_price - close_price) * remaining + partial_pnl

    notional    = trade.entry_price * trade.size_units   # original full notional
    pnl_percent = (pnl / notional * 100) if notional > 0 else 0.0

    # ── Step 2: Update PaperTrade ─────────────────────────────────────────────
    duration_hours = (now - trade.opened_at).total_seconds() / 3600

    trade.exit_price  = round(close_price, 6)
    trade.pnl         = round(pnl, 4)
    trade.pnl_percent = round(pnl_percent, 4)
    trade.closed_at   = now
    trade.status      = TradeStatus.STOPPED if reason == "STOP_LOSS" else TradeStatus.CLOSED

    # ── Exit attribution ──────────────────────────────────────────────────────
    trade.exit_reason   = reason[:20]
    trade.holding_hours = round(duration_hours, 2)

    initial_r = float(trade.initial_risk_inr or 0)
    trade.r_multiple = round(pnl / initial_r, 3) if initial_r > 0 else None

    # Read MFE/MAE peak/trough from the running excursion tracker in trade_mgmt
    _snap_d   = (trade.indicator_snapshot or {}) if trade.indicator_snapshot else {}
    _tm_d     = (_snap_d.get("trade_mgmt") or {}) if isinstance(_snap_d, dict) else {}
    peak_upnl  = float(_tm_d.get("peak_upnl",   0.0))
    trough_upnl = float(_tm_d.get("trough_upnl", 0.0))

    trade.mfe_abs       = round(peak_upnl, 2)
    trade.mae_abs       = round(trough_upnl, 2)
    trade.max_open_profit = round(peak_upnl, 2)
    if notional > 0:
        trade.mfe_pct = round(peak_upnl   / notional * 100, 2)
        trade.mae_pct = round(trough_upnl / notional * 100, 2)
    if initial_r > 0:
        trade.mfe_r = round(peak_upnl   / initial_r, 3)
        trade.mae_r = round(trough_upnl / initial_r, 3)

    # ── Step 3: Delete OpenPosition ───────────────────────────────────────────
    await session.execute(
        delete(OpenPosition).where(OpenPosition.id == position.id)
    )
    await session.flush()

    # ── Step 4: Return margin + PnL to wallet ─────────────────────────────────
    margin      = trade.size_usd          # return full equity (no leverage)
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


async def compute_live_pnl(
    positions: list, session: AsyncSession,
) -> dict[int, tuple[float, float, float]]:
    """Compute LIVE current_price + unrealised P&L for each position, on demand.

    Returns {position_id: (current_price, unrealised_pnl, unrealised_pct)} using:
      • Equity  → live Kite LTP (batched), 1h candle fallback
      • Options → live Kite option LTP → snapshot/Black-Scholes
      • Futures → live Kite future LTP → index candle

    Used by the read endpoints so a brand-new position shows live P&L immediately,
    independent of the periodic mark-to-market task. Falls back to the stored
    value only when no live price can be resolved.
    """
    from paper_trading.pnl_calculator import PnLCalculator

    # Batch equity LTP in one Kite call.
    eq_syms = [p.symbol for p in positions
               if getattr(p, "instrument_type", "EQUITY") == "EQUITY"]
    live_px: dict[str, float] = {}
    if eq_syms:
        try:
            from crawler.zerodha_market import get_live_prices
            quotes = await get_live_prices(eq_syms)
            for sym, q in (quotes or {}).items():
                px = q.get("price") or q.get("last_price")
                if px and px > 0:
                    live_px[sym] = float(px)
        except Exception as exc:
            logger.debug(f"compute_live_pnl: equity LTP failed: {exc}")

    out: dict[int, tuple[float, float, float]] = {}
    for p in positions:
        cur = None
        itype = getattr(p, "instrument_type", "EQUITY")
        try:
            if itype in ("CE", "PE"):
                from engine.fno.selection import current_option_premium
                cur = await current_option_premium(p, session)
            elif itype == "FUTURE":
                from engine.fno.futures import current_future_price
                cur = await current_future_price(p, session)
            else:
                cur = live_px.get(p.symbol)
                if cur is None:
                    row = (await session.execute(
                        select(Candle.close)
                        .where(Candle.symbol == p.symbol, Candle.timeframe == "1h")
                        .order_by(Candle.timestamp.desc()).limit(1)
                    )).scalar_one_or_none()
                    cur = float(row) if row else None
        except Exception as exc:
            logger.debug(f"compute_live_pnl: price failed for {p.symbol}: {exc}")

        if cur and cur > 0:
            pnl = PnLCalculator.unrealised_for_position(p, cur)
            pct = PnLCalculator.unrealised_pct_for_position(p, cur)
            out[p.id] = (round(cur, 4), round(pnl, 2), round(pct, 2))
        else:
            out[p.id] = (p.current_price, p.unrealised_pnl, p.unrealised_pct)
    return out


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
    now = datetime.utcnow()
    # Eager-load the linked PaperTrade so we can read/update its trade_mgmt JSON
    # (trailing-stop state) without triggering a lazy load in async context.
    result    = await session.execute(
        select(OpenPosition).options(selectinload(OpenPosition.trade))
    )
    positions = list(result.scalars().all())

    # ── Prefetch LIVE Kite LTP for all equity positions (real-time, not the
    # stale 1h candle). One batched LTP call per cycle. Falls back to candle
    # per-symbol if Kite has no quote. This is what makes prices/P&L/Telegram live.
    live_px: dict[str, float] = {}
    eq_syms = [p.symbol for p in positions
               if getattr(p, "instrument_type", "EQUITY") == "EQUITY"]
    if eq_syms:
        try:
            from crawler.zerodha_market import get_live_prices
            quotes = await get_live_prices(eq_syms)
            for sym, q in (quotes or {}).items():
                px = q.get("price") or q.get("last_price")
                if px and px > 0:
                    live_px[sym] = float(px)
        except Exception as exc:
            logger.debug(f"update_positions: live Kite LTP prefetch failed: {exc}")

    auto_closed: list[dict] = []

    for pos in positions:
        # ── F&O positions: mark to live option/future price (not candles) ──────
        if getattr(pos, "instrument_type", "EQUITY") in ("CE", "PE", "FUTURE"):
            try:
                if pos.instrument_type == "FUTURE":
                    from engine.fno.futures import current_future_price, future_pnl
                    cur = await current_future_price(pos, session)
                    if cur:
                        pos.current_price = cur
                        pos.unrealised_pnl, pos.unrealised_pct = future_pnl(pos, cur)
                else:
                    from engine.fno.selection import current_option_premium, option_pnl
                    cur = await current_option_premium(pos, session)
                    if cur:
                        pos.current_price = cur
                        pos.unrealised_pnl, pos.unrealised_pct = option_pnl(pos, cur)
            except Exception as exc:
                logger.debug(f"update_positions: F&O mark failed for {pos.symbol}: {exc}")
            continue

        # Prefer the LIVE Kite price; fall back to the latest 1h candle.
        price = live_px.get(pos.symbol)
        if price is None:
            candle_row = await session.execute(
                select(Candle)
                .where(Candle.symbol == pos.symbol, Candle.timeframe == "1h")
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )
            candle = candle_row.scalar_one_or_none()
            if candle is None:
                logger.debug(f"update_positions: no price for {pos.symbol} — skipping")
                continue
            price = candle.close

        is_buy = pos.direction == TradeDirection.BUY

        # ── Trailing stop after Target 1 ──────────────────────────────────────
        # Once price touches T1, ratchet the stop to trail the high-water mark by
        # 1×ATR (or 2% proxy). The position then rides toward T2 (its take_profit)
        # protected by the trailed stop. Stop only ever tightens, never loosens.
        trailed = False
        snap = (pos.trade.indicator_snapshot or {}) if pos.trade else {}
        tm   = snap.get("trade_mgmt") if isinstance(snap, dict) else None
        if tm:
            t1         = tm.get("target_1")
            trail_dist = tm.get("trail_dist") or 0.0
            trailing   = bool(tm.get("trailing"))
            peak       = tm.get("peak_price") or pos.entry_price

            exit_policy = settings.AGENT_EXIT_POLICY  # "partial_fixed" | "current"

            if is_buy:
                peak = max(peak, price)
                # T1 hit: always book 50% regardless of exit policy
                if not tm.get("partial_done") and t1 and price >= t1:
                    partial_qty = int(pos.size_units * 0.5)
                    if partial_qty > 0:
                        partial_pnl = round((price - pos.entry_price) * partial_qty, 4)
                        tm["partial_done"]  = True
                        tm["partial_qty"]   = partial_qty
                        tm["partial_price"] = round(price, 4)
                        tm["partial_pnl"]   = partial_pnl
                        pos.size_units      = pos.size_units - partial_qty
                        # Break-even stop: remaining half can never lose
                        pos.stop_loss = max(pos.stop_loss, pos.entry_price)
                        trailed = True
                        logger.info(
                            f"[T1 partial] {pos.symbol}: booked {partial_qty} units "
                            f"@ ₹{price:.2f} (pnl=₹{partial_pnl:.2f}), "
                            f"{'holding' if exit_policy == 'partial_fixed' else 'trailing'} "
                            f"{int(pos.size_units)} units to T2"
                        )
                    # "current" policy: activate trailing stop after T1
                    if exit_policy != "partial_fixed":
                        trailing = True
                # Trailing stop ratchet — only for "current" policy
                if exit_policy != "partial_fixed" and trailing and trail_dist > 0:
                    new_stop = peak - trail_dist
                    if new_stop > pos.stop_loss:
                        pos.stop_loss = round(new_stop, 4)
                        trailed = True
            else:  # SELL
                peak = min(peak, price)
                if not trailing and t1 and price <= t1:
                    trailing = True
                if trailing and trail_dist > 0:
                    new_stop = peak + trail_dist
                    if new_stop < pos.stop_loss:
                        pos.stop_loss = round(new_stop, 4)
                        trailed = True

            # Persist mutated trailing state (reassign dict so SQLAlchemy detects it)
            if trailing != bool(tm.get("trailing")) or peak != tm.get("peak_price") or trailed:
                tm = {**tm, "trailing": trailing, "peak_price": round(peak, 6)}
                pos.trade.indicator_snapshot = {**snap, "trade_mgmt": tm}
                if trailing and not bool(snap.get("trade_mgmt", {}).get("trailing")):
                    pos.take_profit = round(tm.get("target_2") or pos.take_profit, 4)

        # ── SL/TP check (uses the possibly-trailed stop) ──────────────────────
        hit_sl = (
            is_buy and price <= pos.stop_loss
            or (not is_buy) and price >= pos.stop_loss
        )
        hit_tp = (
            is_buy and price >= pos.take_profit
            or (not is_buy) and price <= pos.take_profit
        )

        if hit_sl or hit_tp:
            is_trailing = bool(tm.get("trailing")) if tm else False
            reason = ("TRAIL_STOP" if hit_sl and is_trailing
                      else "STOP_LOSS" if hit_sl else "TAKE_PROFIT")
            closed_trade = await close_paper_trade(pos, price, reason, session)
            auto_closed.append({
                "trade_id":    closed_trade.id,
                "symbol":      closed_trade.symbol,
                "reason":      reason,
                "exit_price":  price,
                "pnl":         closed_trade.pnl,
                "entry_price": closed_trade.entry_price,
                "size_units":  closed_trade.size_units,
                "direction":   pos.direction.value,
            })
            continue

        # ── Time-based stale exit ─────────────────────────────────────────────
        # Exit positions that have been held >45 calendar days (~30 trading days)
        # AND are still in a loss. This only targets genuinely dead losing trades —
        # NOT slow winners. Backtest showed that exiting <1%-profit trades at 20
        # bars kills slow-developing winners; the correct threshold is: negative
        # return after 45 days, where the stop clearly isn't working as protection.
        if pos.trade and pos.trade.opened_at:
            days_held = (now - pos.trade.opened_at).days
            if days_held >= 45:
                notional_now = pos.entry_price * pos.size_units
                upnl_now = (
                    (price - pos.entry_price) * pos.size_units if is_buy
                    else (pos.entry_price - price) * pos.size_units
                )
                upct_now = (upnl_now / notional_now * 100) if notional_now > 0 else 0.0
                if upct_now < -2.0:  # only exit if actually losing (not just slow)
                    closed_trade = await close_paper_trade(pos, price, "STALE_EXIT", session)
                    auto_closed.append({
                        "trade_id":    closed_trade.id,
                        "symbol":      closed_trade.symbol,
                        "reason":      "STALE_EXIT",
                        "exit_price":  price,
                        "pnl":         closed_trade.pnl,
                        "entry_price": closed_trade.entry_price,
                        "size_units":  closed_trade.size_units,
                        "direction":   pos.direction.value,
                    })
                    logger.info(
                        f"[stale] {pos.symbol}: {days_held}d held, "
                        f"upct={upct_now:.1f}% — stale loser exit at ₹{price:.2f}"
                    )
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

        # ── MFE/MAE running tracker ────────────────────────────────────────────
        # Update peak_upnl (best it's ever been) and trough_upnl (worst) in
        # trade_mgmt JSON so close_paper_trade() can read them without a full
        # per-tick DB scan. Uses a fresh read of indicator_snapshot in case the
        # trailing-stop block above already mutated it this tick.
        if pos.trade:
            _snap_now = pos.trade.indicator_snapshot or {}
            _tm_now   = (_snap_now.get("trade_mgmt") or {}) if isinstance(_snap_now, dict) else {}
            _prev_peak   = float(_tm_now.get("peak_upnl",   upnl))
            _prev_trough = float(_tm_now.get("trough_upnl", upnl))
            _new_peak    = max(_prev_peak,   upnl)
            _new_trough  = min(_prev_trough, upnl)
            if _new_peak != _prev_peak or _new_trough != _prev_trough:
                _tm_now = {**_tm_now,
                           "peak_upnl":   round(_new_peak,   4),
                           "trough_upnl": round(_new_trough, 4)}
                pos.trade.indicator_snapshot = {**_snap_now, "trade_mgmt": _tm_now}

            # Optional per-tick samples (exact MFE/MAE; disabled by default)
            if getattr(settings, "ENABLE_EXCURSION_SAMPLES", False) and pos.trade_id:
                from db.models import TradeExcursionSample
                _init_r = float(pos.trade.initial_risk_inr or 0)
                session.add(TradeExcursionSample(
                    trade_id=pos.trade_id,
                    ts=now,
                    price=round(price, 4),
                    unrealised_pnl=round(upnl, 4),
                    unrealised_r=round(upnl / _init_r, 3) if _init_r > 0 else None,
                ))

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
