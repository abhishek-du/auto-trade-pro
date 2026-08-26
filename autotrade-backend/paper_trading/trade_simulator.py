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
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import AgentTrade, Candle, OpenPosition, PaperTrade, ReentryWatch, TradeDirection, TradeStatus
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
_MAX_POSITION_PCT = 0.05

# ── DIRECT_NEWS post-entry re-confirmation (2026-07-29) ─────────────────────
# See update_positions_with_current_prices()'s CONFIRMATION_LOST block below
# for the CARTRADE.NS incident this closes. The "don't re-verify more than
# every 15 min" throttle itself lives in Redis (direct_news_recheck:{trade_id},
# SET NX EX 900) rather than process memory (2026-07-31 fix) -- an in-process
# dict here doesn't hold across Celery's 4 worker children or its frequent
# auto-restarts, both of which reset it independently, so the same trade was
# observed getting re-checked every 1-8 min instead of every 15 (AARTIIND.NS
# 2026-07-31: 4 separate re-checks from 4 different worker PIDs in 12 min,
# each rolling the dice against a noisy order-book snapshot).
_DIRECT_NEWS_RECHECK_WINDOW = timedelta(hours=2)  # only during the early post-entry window
_DIRECT_NEWS_RECHECK_GRACE_PERIOD = timedelta(minutes=15)  # give stock time to breathe before enforcing rule

# ── PRE_EVENT_EXPECTATION_GAP post-event exits (2026-08-17 forensic) ─────────
# Post-mortem of 223 trades (docs/2026-08-17_FORENSIC_POST_MORTEM.md) found the
# holding period relative to the EVENT date is the single strongest predictor in
# the whole dataset — stronger than score, confidence, sector or regime:
#
#     exited <=2 days after event : n=43  win 46.5%  PnL +30,432
#     held 3-5 days after event   : n=18  win 27.8%  PnL -25,412
#     held  >5 days after event   : n= 4  win  0.0%  PnL  -2,473
#
# Every rupee of profit is made in the 0-2 day post-event window; beyond it the
# book loses -27,885. Validated that this cap does NOT cut the winners: all 10
# TAKE_PROFIT exits and 14 of 15 T1_REVERSAL_EXIT exits already complete within
# 2 days of the event (only TEGA.NS, +1,426, sits outside).
_POST_EVENT_MAX_TRADING_DAYS = 2

# Unrealised % that counts as "the event resolved against the nowcast". Kept at
# the original -3.0 threshold; what changed (2026-08-17) is the RESPONSE to it —
# see the POST_EVENT_REVERSAL block below.
_POST_EVENT_ADVERSE_PCT = -3.0


def _pre_event_metadata(trade) -> tuple[str | None, str | None]:
    """(event_date_iso, nowcast_direction) for a PRE_EVENT_EXPECTATION_GAP trade.

    Reads `indicator_snapshot.confidence_factors` first, then falls back to
    parsing `ai_reason`. The fallback is load-bearing, not defensive: trades
    opened before those keys started being persisted carry ONLY the structured
    score_breakdown in confidence_factors, with the event date living in the
    ai_reason text ("Event: QUARTERLY_RESULT on 2026-08-04"). Without it the
    time-based exit silently skipped every legacy position — i.e. exactly the
    most overdue ones. Caught live 2026-08-17: the first run of the new exits
    closed 16 positions but left 9 untouched, RITES.NS among them at 9 trading
    days past its event.
    """
    snap = (trade.indicator_snapshot or {}) if trade else {}
    cf = snap.get("confidence_factors") or {}
    event_date = cf.get("event_date")
    nowcast_dir = cf.get("nowcast_direction")

    if not (event_date and nowcast_dir):
        reason = getattr(trade, "ai_reason", "") or ""
        if not event_date:
            m = re.search(r"\bon (\d{4}-\d{2}-\d{2})", reason)
            if m:
                event_date = m.group(1)
        if not nowcast_dir:
            m = re.search(r"Nowcast profit:\s*(POSITIVE|NEGATIVE|NEUTRAL)", reason)
            if m:
                nowcast_dir = m.group(1)
    return event_date, nowcast_dir


def _trading_days_since(event_date, today) -> int:
    """NSE trading days elapsed from event_date to today (exclusive of the event
    day itself). Weekends and NSE holidays don't count, so a Friday event
    checked on the following Monday is 1 trading day, not 3 calendar days.

    Falls back to calendar days if the holiday calendar can't be loaded — the
    time-based exit must never be silently disabled by a calendar failure.
    """
    if today <= event_date:
        return 0
    try:
        from engine.calendar_engine import _HOLIDAY_SET
    except Exception:
        _HOLIDAY_SET = set()
    days = 0
    cur = event_date
    while cur < today:
        cur += timedelta(days=1)
        if cur.weekday() < 5 and cur not in _HOLIDAY_SET:
            days += 1
    return days


def estimate_trade_cost(qty: int, price: float, side: str = "BUY") -> float:
    """Realistic Indian equity delivery transaction cost (Varsity Module 7).

    Brokerage (capped ₹20) + STT + exchange turnover + SEBI + stamp (buy only)
    + 18% GST. Charged on both legs of every close so paper P&L reflects real
    round-trip friction instead of an over-optimistic zero-commission fill.
    """
    notional  = qty * price
    brokerage = min(20.0, 0.0003 * notional)
    stt       = notional * 0.001
    exchange  = notional * 0.0000345
    sebi      = notional * 0.000001
    stamp     = notional * 0.00015 if side == "BUY" else 0.0
    gst       = (brokerage + exchange + sebi) * 0.18
    return round(brokerage + stt + exchange + sebi + stamp + gst, 2)


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
        rf            = risk_fraction if risk_fraction is not None else float(getattr(settings, "AGENT_MAX_RISK_PER_TRADE", 0.01))
        stop_distance = abs(entry_price - stop_loss_price)
        if stop_distance == 0:
            return 0.0, 0.0
        risk_usd   = balance * rf
        # Whole shares only — NSE/BSE equity quantity is always an integer.
        size_units = int(risk_usd / stop_distance)
        max_usd    = balance * _MAX_POSITION_PCT
        if size_units * entry_price > max_usd and entry_price > 0:
            size_units = int(max_usd // entry_price)
        size_usd   = size_units * entry_price
        return size_units, round(size_usd, 4)

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
    *,
    product: str = "CNC",       # CNC=delivery | MIS=intraday | NRML=F&O overnight
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

    # ── HARD GUARD: last line of defense against oversized/duplicate trades ───
    # This gate catches bugs in ANY caller (india_trade_loop, agent_loop,
    # paper_trade_loop, manual trigger). No trade touches the DB without passing.
    _guard_equity = float(getattr(settings, "AGENT_EQUITY", 2_000_000))
    # Family-aware: TACTICAL carries its own 10% cap (see
    # engine.risk_manager.max_position_weight_for). Imported locally because
    # engine.decision_router imports this module, so a top-level import back
    # into engine.risk_manager would close a cycle.
    from engine.risk_manager import max_position_weight_for
    _guard_max_w  = max_position_weight_for(signal)
    _guard_max_notional = _guard_equity * _guard_max_w
    usd_value_check = position_size.get("usd_value", 0)
    if usd_value_check > _guard_max_notional * 1.10:
        msg = (
            f"HARD GUARD BLOCKED: {signal.symbol} notional ₹{usd_value_check:,.0f} "
            f"exceeds {_guard_max_w*100:.0f}% of ₹{_guard_equity:,.0f} "
            f"(max ₹{_guard_max_notional:,.0f})"
        )
        logger.error(f"open_paper_trade: {msg}")
        raise ValueError(msg)

    _bare_sym = signal.symbol.replace(".NS", "").replace(".BO", "").upper()
    existing = (await session.execute(
        select(OpenPosition.symbol)
    )).scalars().all()
    for s in existing:
        if s == signal.symbol or s.replace(".NS", "").replace(".BO", "").upper() == _bare_sym:
            msg = f"HARD GUARD BLOCKED: duplicate position for {signal.symbol} (already have {s})"
            logger.error(f"open_paper_trade: {msg}")
            raise ValueError(msg)

    # ── Step 1: Slippage simulation ───────────────────────────────────────────
    slippage      = random.uniform(_SLIP_MIN, _SLIP_MAX) * signal.entry_price
    if signal.action == "BUY":
        actual_entry = signal.entry_price + slippage
    else:
        actual_entry = signal.entry_price - slippage
    slippage_applied = actual_entry - signal.entry_price

    direction = TradeDirection(signal.action)
    # Equity quantity must be a whole number of shares (NSE/BSE: 1.2 is not a legal
    # order size). Sizing floors to an int upstream; coerce + guard here so no caller
    # can ever open a fractional or zero-share position.
    units     = int(position_size["units"])
    if units < 1:
        msg = (
            f"HARD GUARD BLOCKED: {signal.symbol} sized to {units} shares "
            f"(risk budget too small for 1 share at ₹{signal.entry_price:,.2f})"
        )
        logger.warning(f"open_paper_trade: {msg}")
        raise ValueError(msg)
    # Charge the wallet the ACTUAL fill cost (post-slippage), not the pre-slippage
    # notional from sizing. The position is booked at `actual_entry`, so deducting
    # the signal-price notional instead left the entry slippage uncharged — cash +
    # cost-basis overshot the wallet base by the accumulated slippage and inflated
    # equity / unrealised P&L. Matching cash to the booked price makes slippage a
    # real cost and keeps cash + holdings reconcile to the wallet base exactly.
    usd_value = round(units * actual_entry, 4)

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
        # News-Only traceability (2026-07-22, T1-reanalysis/re-entry feature)
        # -- see TradingSignal.event_id's docstring. None/[] for non-event-
        # driven signals, which is exactly when the re-entry watcher must
        # not try to re-authorize a trade with no event to trace to.
        "event_id":      getattr(signal, "event_id", None),
        "evidence_ids":  list(getattr(signal, "evidence_ids", None) or []),
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
            # Confidence transparency (2026-07-22) -- see TradeIntent.confidence_factors's
            # docstring. {} for legacy/TECHNICAL callers that don't set it.
            "confidence_factors": dict(getattr(signal, "confidence_factors", None) or {}),
        },
        news_sentiment_score=signal.sentiment_score / 100.0,
        slippage_applied=round(slippage_applied, 6),
        opened_at=now,
        # Attribution
        strategy_name=(_strategy[:40] if _strategy else None),
        # _intent_to_signal attaches this as a plain string; a signal that never
        # passed through the router has none, which is correctly stored as NULL
        # rather than guessed at.
        strategy_family=(str(getattr(signal, "strategy_family", None) or "")[:20] or None),
        source=(getattr(signal, "source", None) or ("AI Predict" if _strategy == "PRE_EVENT_EXPECTATION_GAP" else None)),
        regime_at_entry=(_regime_entr[:20] if _regime_entr else None),
        entry_reason=_entry_rsn,
        confidence_bucket=_conf_bucket,
        instrument_segment=f"EQUITY_{product}",
        initial_risk_inr=_initial_r,
        product=product,
    )
    session.add(trade)
    await session.flush()                           # populate trade.id

    # ── Step 2b: Persist OpenPosition ─────────────────────────────────────────
    from datetime import timedelta
    is_swing = product == "CNC"
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
        product=product,
        trade_style="SWING" if is_swing else product,
        swing_min_hold=now + timedelta(hours=48) if is_swing else None,
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



async def scale_out_paper_trade(
    position,
    scale_pct: float,
    current_price: float,
    reason: str,
    session
) -> float:
    """Book a percentage of the open position.
    
    1. Reduces position.size_units and size_usd by scale_pct.
    2. Calculates realised PnL on the closed portion.
    3. Saves partial_pnl in trade.indicator_snapshot.
    4. Returns margin + partial PnL to wallet.
    """
    trade = position.trade
    if not trade:
        # Fallback if lazy-loaded
        from db.models import PaperTrade
        trade = await session.get(PaperTrade, position.trade_id)
        if not trade:
            return 0.0
            
    close_units = position.size_units * scale_pct
    close_usd = position.size_usd * scale_pct
    
    # Calculate P&L for the scaled-out portion
    if position.direction.value == "BUY":
        gross_pnl = (current_price - trade.entry_price) * close_units
        cost = estimate_trade_cost(close_units, trade.entry_price, "BUY") + estimate_trade_cost(close_units, current_price, "SELL")
    else:
        gross_pnl = (trade.entry_price - current_price) * close_units
        cost = estimate_trade_cost(close_units, trade.entry_price, "SELL") + estimate_trade_cost(close_units, current_price, "BUY")
        
    partial_pnl = gross_pnl - cost
    
    # Update position
    position.size_units -= close_units
    position.size_usd -= close_usd
    
    # Update snapshot
    snap = trade.indicator_snapshot or {}
    tm = snap.get("trade_mgmt", {})
    tm["partial_pnl"] = tm.get("partial_pnl", 0.0) + partial_pnl
    snap["trade_mgmt"] = tm
    # re-assign to trigger SQLAlchemy JSON mutation
    trade.indicator_snapshot = dict(snap)
    
    # Return margin + P&L
    from paper_trading.wallet import VirtualWallet
    await VirtualWallet.return_margin(session, close_usd, partial_pnl, trade.symbol)
    
    from db.models import SimulationLog
    session.add(SimulationLog(
        event_type="TRADE_SCALEOUT",
        symbol=trade.symbol,
        message=f"{reason} | Booked {scale_pct*100:.0f}% @ {current_price:.2f} | PnL: {partial_pnl:.2f}",
        data={"units": close_units, "pnl": partial_pnl, "price": current_price},
    ))
    return partial_pnl

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
    # Fetch the parent trade with a row-level lock (Bug B2 fix)
    trade_row = await session.execute(
        select(PaperTrade)
        .where(PaperTrade.id == position.trade_id)
        .where(PaperTrade.status == TradeStatus.OPEN)
        .with_for_update()
    )
    trade = trade_row.scalar_one_or_none()
    if trade is None:
        raise ValueError(f"Trade {position.trade_id} is already closed or does not exist.")
        
    now = datetime.utcnow()

    # ── Step 1: P&L ───────────────────────────────────────────────────────────
    # If a partial scale-out fired at T1, pos.size_units was reduced to the
    # remaining half. Use position.size_units (remaining) for the final leg,
    # then add the already-realised partial_pnl for the total trade P&L.
    # NameError fix: estimate_trade_cost was used below but never imported, so
    # EVERY close raised NameError (silently caught by callers → positions never
    # closed, SL/TP never fired). Defined locally (module-level `estimate_trade_cost`)
    # so the live close path has no dependency on the heavy backtester module.
    snap_data   = (trade.indicator_snapshot or {}) if trade.indicator_snapshot else {}
    partial_pnl = float(snap_data.get("trade_mgmt", {}).get("partial_pnl", 0.0))
    remaining   = position.size_units   # may be < trade.size_units after partial

    if position.direction == TradeDirection.BUY:
        gross_pnl = (close_price - trade.entry_price) * remaining
        cost = estimate_trade_cost(remaining, trade.entry_price, "BUY") + estimate_trade_cost(remaining, close_price, "SELL")
    else:
        gross_pnl = (trade.entry_price - close_price) * remaining
        cost = estimate_trade_cost(remaining, trade.entry_price, "SELL") + estimate_trade_cost(remaining, close_price, "BUY")
    
    pnl = gross_pnl - cost + partial_pnl

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

    # ── Step 7: keep the agent ledger in sync (NO second wallet return) ────────
    # close_paper_trade is the single close path for paper positions. The agent's
    # AgentTrade ledger row must be closed here too — otherwise positions closed
    # by the mark-to-market task leak as phantom-open rows in /agent/positions.
    # Margin was already returned in Step 4, so this only sets the exit fields.
    try:
        agent_trade = (await session.execute(
            select(AgentTrade).where(
                AgentTrade.symbol == trade.symbol,
                AgentTrade.exit_ts == None,
                AgentTrade.is_paper == settings.AGENT_PAPER_MODE,
            ).order_by(AgentTrade.entry_ts.desc()).limit(1)
        )).scalar_one_or_none()
        if agent_trade is not None:
            agent_trade.exit_price  = round(close_price, 6)
            agent_trade.exit_ts     = now
            agent_trade.exit_reason = reason
            agent_trade.pnl         = round(pnl, 4)
            await session.flush()
    except Exception as exc:
        logger.debug(f"close_paper_trade: agent ledger sync skipped for {trade.symbol}: {exc}")

    # Level-4 reflection: distil a transferable lesson from this closed trade
    # (gated by AGENT_LLM_REFLECTION_ENABLED, fail-open, never blocks the close).
    try:
        from engine.agent.reflection import reflect_on_closed_trade
        await reflect_on_closed_trade(trade)
    except Exception as exc:
        logger.debug(f"close_paper_trade: reflection skipped for {trade.symbol}: {exc}")

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
            quotes = await get_live_prices(eq_syms, exit_bucket=True)   # D6: reserved exit quota
            for sym, q in (quotes or {}).items():
                px = q.get("price") or q.get("last_price")
                if px and px > 0:
                    live_px[sym] = float(px)
        except Exception as exc:
            logger.debug(f"compute_live_pnl: equity LTP failed: {exc}")

    out: dict[int, tuple[float, float, float]] = {}
    for p in positions:
        cur = None
        try:
            cur = live_px.get(p.symbol)
            if cur is None:
                # Freshest candle across ALL timeframes (small/SME stocks often
                # have fresh 1m but no 1h — a '1h'-only read froze the price).
                from crawler.price_feed import get_freshest_candle
                _cl, _ = await get_freshest_candle(p.symbol, session)
                cur = _cl
        except Exception as exc:
            logger.debug(f"compute_live_pnl: price failed for {p.symbol}: {exc}")

        if cur and cur > 0:
            pnl = PnLCalculator.unrealised_for_position(p, cur)
            pct = PnLCalculator.unrealised_pct_for_position(p, cur)
            out[p.id] = (round(cur, 4), round(pnl, 2), round(pct, 2))
        else:
            out[p.id] = (p.current_price, p.unrealised_pnl, p.unrealised_pct)
    return out


async def _t1_reversal_exit(pos: OpenPosition, price: float, analysis: dict, session: AsyncSession) -> dict:
    """Full-close a position on a T1-reanalysis EXIT decision (see
    engine/agent/t1_reanalysis.py) and register a ReentryWatch IF the
    position traces back to a real canonical event -- NO EVENT -> NO TRADE
    still applies to any re-entry, so a position with no event_id on record
    (e.g. a legacy/technical position predating this feature) just closes,
    with no watch registered rather than a made-up one.
    """
    closed_trade = await close_paper_trade(pos, price, "T1_REVERSAL_EXIT", session)

    snap = (closed_trade.indicator_snapshot or {}) if closed_trade else {}
    tm = snap.get("trade_mgmt") or {}
    event_id = tm.get("event_id")
    evidence_ids = tm.get("evidence_ids") or []
    direction = pos.direction.value

    reasoning = str(analysis.get("reasoning") or "")[:500]
    watch_level = analysis.get("watch_level")
    if watch_level is None:
        # Deterministic fallback so SOME level is always watched even if the
        # LLM didn't name one: a modest confirmation buffer beyond the exit
        # price in the trade's original direction.
        watch_level = round(price * (1.01 if direction == "BUY" else 0.99), 2)

    if event_id is not None:
        session.add(ReentryWatch(
            symbol=pos.symbol, direction=direction, watch_level=watch_level,
            event_id=event_id, evidence_ids=evidence_ids, reason=reasoning,
            status="WATCHING",
            original_confidence=float(getattr(closed_trade, "signal_confidence", 0.0) or 0.0),
            expires_at=datetime.utcnow() + timedelta(hours=6),
        ))
        logger.warning(
            f"[t1_reanalysis] {pos.symbol}: T1-REVERSAL EXIT @ ₹{price:.2f} | watching for "
            f"re-entry {'above' if direction == 'BUY' else 'below'} ₹{watch_level:.2f} | {reasoning[:150]}"
        )
    else:
        logger.warning(
            f"[t1_reanalysis] {pos.symbol}: T1-REVERSAL EXIT @ ₹{price:.2f} | no canonical "
            f"event on this position -- re-entry watch not registered | {reasoning[:150]}"
        )

    return {
        "trade_id":    closed_trade.id,
        "symbol":      closed_trade.symbol,
        "reason":      "T1_REVERSAL_EXIT",
        "exit_price":  price,
        "pnl":         closed_trade.pnl,
        "entry_price": closed_trade.entry_price,
        "size_units":  closed_trade.size_units,
        "direction":   direction,
    }


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
            quotes = await get_live_prices(eq_syms, exit_bucket=True)   # D6: reserved exit quota
            for sym, q in (quotes or {}).items():
                px = q.get("price") or q.get("last_price")
                if px and px > 0:
                    live_px[sym] = float(px)
        except Exception as exc:
            logger.debug(f"update_positions: live Kite LTP prefetch failed: {exc}")

    auto_closed: list[dict] = []

    # ── Sector-mood reversal exit ──────────────────────────────────────────────
    # Pure price-based SL/TP (below) only reacts once a move has already
    # happened — bad sector/market news doesn't get ahead of it. build_sector_context()
    # is a cheap in-memory cache read (no DB/API call), safe to call every cycle.
    # A prior version of this check existed (tasks/india_tasks.py's
    # run_master_intelligence_cycle) but operated on OpenPosition — a parallel
    # portfolio object that has never held a real position (confirmed empty,
    # 2026-07-06) — so it never actually protected anything. This is the same
    # check wired into the loop that manages the real, live positions.
    try:
        from engine.intelligence_hub import build_sector_context, _get_sector_for_symbol
        _sector_moods = build_sector_context().sector_moods
    except Exception as exc:
        logger.debug(f"update_positions: sector context unavailable: {exc}")
        _sector_moods = {}

    for pos in positions:
        # Prefer the LIVE Kite price. If the batched LTP call missed this
        # symbol, try ONE direct Zerodha fetch for it before ever trusting our
        # own DB's Candle table — that table is only as fresh as its last sync
        # job, and 2026-07-22 surfaced SL/TP firing off a price that had
        # already drifted from the real live Zerodha/Upstox tape.
        price = live_px.get(pos.symbol)
        if price is None:
            try:
                from crawler.zerodha_market import get_kite_historical
                _today = now.strftime("%Y-%m-%d")
                _candles = await get_kite_historical(
                    pos.symbol, _today, _today, interval="minute", session=session,
                )
            except Exception as exc:
                logger.debug(f"update_positions: direct Zerodha fetch failed for {pos.symbol}: {exc}")
                _candles = []
            if _candles:
                _last = _candles[-1]
                _age = (now - _last["timestamp"]).total_seconds()
                if _age <= 120:
                    price = _last["close"]
                else:
                    logger.debug(
                        f"update_positions: {pos.symbol} freshest direct-Zerodha candle is "
                        f"{_age / 60:.1f} min old — not fresh enough, trying DB fallback"
                    )

        if price is None:
            # Last resort: our own DB candle table. Small/SME stocks often have
            # fresh 1m candles but no 1h, so a '1h'-only read froze the price at
            # entry (fake ₹0.00 P&L observed 9-Jul: TBZ +15% shown as 0).
            from crawler.price_feed import get_freshest_candle
            _cl, _c_ts = await get_freshest_candle(pos.symbol, session)
            if _cl is None or _c_ts is None:
                logger.debug(f"update_positions: no price for {pos.symbol} — skipping")
                continue
            if getattr(_c_ts, "tzinfo", None):
                _c_ts = _c_ts.replace(tzinfo=None)
            # B14 fix (tightened 2026-07-22): during market hours a DB candle
            # is expected to be seconds-to-minutes old, not hours — bound it
            # tightly so a stalled sync job can't feed a stale price into an
            # SL/TP decision. Outside market hours the last real print IS
            # legitimately from hours ago, so keep the old 4-day/weekend bound.
            from tasks.india_tasks import _is_india_trading_window
            _max_age = 900 if _is_india_trading_window() else 4 * 86400
            _age = (now - _c_ts).total_seconds()
            if _age > _max_age:
                logger.warning(
                    f"update_positions: {pos.symbol} newest DB candle is "
                    f"{_age / 60:.1f} min old — skipping (stale, no phantom close)"
                )
                continue
            price = _cl

        is_buy = pos.direction == TradeDirection.BUY

        # ── Sector-mood reversal exit ──────────────────────────────────────────
        # Exit ahead of the stop-loss when this position's sector has turned
        # hard against it — a long whose sector just went STRONGLY_BEARISH, or
        # a short whose sector just went STRONGLY_BULLISH. Proactive, not
        # reactive: doesn't wait for price to travel all the way to the SL.
        if _sector_moods:
            _sec = _get_sector_for_symbol(pos.symbol)
            _mood = _sector_moods.get(_sec)
            _sector_hit = (
                (is_buy and _mood == "STRONGLY_BEARISH") or
                (not is_buy and _mood == "STRONGLY_BULLISH")
            )
            if _sector_hit:
                try:
                    async with session.begin_nested():
                        closed_trade = await close_paper_trade(pos, price, "SECTOR_REVERSAL", session)
                    auto_closed.append({
                        "trade_id":    closed_trade.id,
                        "symbol":      closed_trade.symbol,
                        "reason":      "SECTOR_REVERSAL",
                        "exit_price":  price,
                        "pnl":         closed_trade.pnl,
                        "entry_price": closed_trade.entry_price,
                        "size_units":  closed_trade.size_units,
                        "direction":   pos.direction.value,
                    })
                    logger.warning(
                        f"[sector_exit] {pos.symbol}: sector {_sec} turned {_mood} "
                        f"against {'long' if is_buy else 'short'} — exited @ ₹{price:.2f}"
                    )
                except Exception as exc:
                    logger.warning(f"update_positions: {pos.symbol} sector-exit close failed: {exc}")
                continue

        # ── Post-event exits: the thesis has resolved, stop holding ────────────
        # PRE_EVENT_EXPECTATION_GAP bets on a scheduled corporate event (results,
        # etc.) not yet being priced in. Once that event's date has passed the
        # trade is no longer "pre-event" — it's resolved, and its original
        # rationale no longer applies regardless of which way the market reacted.
        #
        # REWRITTEN 2026-08-17 after the forensic post-mortem
        # (docs/2026-08-17_FORENSIC_POST_MORTEM.md). The previous version only
        # TIGHTENED the stop (to the midpoint between price and the old stop) and
        # fired once per position. Measured over 10 live firings it went
        # 0-for-10 for -16,275: because it can only trigger once the position is
        # ALREADY >=3% under water and then merely halves the remaining room, it
        # is mathematically incapable of producing a winner — a "lose more
        # slowly" device, not a protective one. The EPACKPEB.NS whipsaw that
        # motivated the gentle version (rallied back near entry the next session
        # before fading again) turned out to be the exception; across the full
        # sample, holding through post-event noise lost money consistently.
        #
        # Two exits now, both closing the position outright:
        #   P0-2  adverse   — event resolved against the nowcast -> exit now.
        #   P0-1  time-based — >2 TRADING days past the event -> exit regardless
        #                      of direction, since all profit is made in the 0-2
        #                      day window (see _POST_EVENT_MAX_TRADING_DAYS).
        if pos.trade and pos.trade.strategy_name == "PRE_EVENT_EXPECTATION_GAP":
            _event_date_str, _nowcast_dir = _pre_event_metadata(pos.trade)
            if _event_date_str:
                from datetime import date as _date
                try:
                    _event_date = _date.fromisoformat(_event_date_str)
                except ValueError:
                    _event_date = None
                # UTC->IST offset for the trading-day comparison (matches candle
                # timestamp convention used elsewhere in this codebase).
                _today_ist = (now + timedelta(hours=5, minutes=30)).date()
                if _event_date and _today_ist > _event_date:
                    _cur_pct = (
                        (price - pos.entry_price) / pos.entry_price * 100.0 if is_buy
                        else (pos.entry_price - price) / pos.entry_price * 100.0
                    )
                    # P0-2: reaction contradicts the nowcast that opened the trade.
                    # _nowcast_dir may be absent on older rows — then this check is
                    # skipped and the time-based exit below still applies.
                    _expected_up = _nowcast_dir == "POSITIVE"
                    _adverse = bool(_nowcast_dir) and (
                        (is_buy and _expected_up and _cur_pct <= _POST_EVENT_ADVERSE_PCT) or
                        (not is_buy and not _expected_up and _cur_pct <= _POST_EVENT_ADVERSE_PCT)
                    )
                    # P0-1: held past the profitable post-event window.
                    _elapsed = _trading_days_since(_event_date, _today_ist)
                    _stale = _elapsed > _POST_EVENT_MAX_TRADING_DAYS

                    if _adverse or _stale:
                        _reason = "POST_EVENT_REVERSAL" if _adverse else "POST_EVENT_TIME_EXIT"
                        try:
                            async with session.begin_nested():   # see SAVEPOINT note below
                                closed_trade = await close_paper_trade(pos, price, _reason, session)
                            auto_closed.append({
                                "trade_id":    closed_trade.id,
                                "symbol":      closed_trade.symbol,
                                "reason":      _reason,
                                "exit_price":  price,
                                "pnl":         closed_trade.pnl,
                                "entry_price": closed_trade.entry_price,
                                "size_units":  closed_trade.size_units,
                                "direction":   pos.direction.value,
                            })
                            logger.warning(
                                f"[pre_event_exit] {pos.symbol}: event {_event_date_str} "
                                + (f"resolved against nowcast ({_nowcast_dir})"
                                   if _adverse else
                                   f"was {_elapsed} trading day(s) ago (> {_POST_EVENT_MAX_TRADING_DAYS})")
                                + f", {_cur_pct:+.1f}% unrealised — exited @ ₹{price:.2f} ({_reason})"
                            )
                        except Exception as exc:
                            logger.warning(f"update_positions: {pos.symbol} post-event close failed: {exc}")
                        continue

        # ── DIRECT_NEWS post-entry re-confirmation exit ─────────────────────────
        # DIRECT_NEWS has no LLM/technical step (see engine/direct_news_strategy.py
        # docstring) -- its only entry gates run ONCE, at entry
        # (engine.entry_confirmation.check_price_volume_confirmation +
        # check_day_range_stability). CARTRADE.NS (2026-07-29) showed why that's
        # not enough on its own: news hit ~9:29 IST, price spiked to ₹3066
        # (genuinely passing the entry gate), then reversed and kept falling to
        # ₹2800 -- nothing re-checked whether that early move was still holding.
        # This re-runs the SAME entry-time price/volume check periodically during
        # the early post-entry window, before Target 1 (the trailing-stop/
        # T1-reversal logic below only starts protecting AFTER T1 is touched --
        # this covers the gap before that).
        if (
            is_buy
            and pos.trade
            and pos.trade.strategy_name == "DIRECT_NEWS"
            and pos.opened_at
            and (now - pos.opened_at) <= _DIRECT_NEWS_RECHECK_WINDOW
            and (now - pos.opened_at) > _DIRECT_NEWS_RECHECK_GRACE_PERIOD
        ):
            # Atomic, cross-process throttle (2026-07-31) -- SET NX EX is a single
            # Redis round-trip that both checks AND records "checked" in one step,
            # so it can't race the way a separate get-then-set could. TTL=900s
            # (15 min) needs no manual cleanup -- the key just expires. See the
            # constants block above for why this can't be a process-local dict.
            import redis.asyncio as _aioredis
            _recheck_redis = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            try:
                _should_check = await _recheck_redis.set(
                    f"direct_news_recheck:{pos.trade_id}", "1", nx=True, ex=900,
                )
            finally:
                await _recheck_redis.aclose()
            if _should_check:
                try:
                    from crawler.market_snapshot import get_market_snapshot
                    from engine.entry_confirmation import check_price_volume_confirmation
                    _snap = await get_market_snapshot(pos.symbol)
                    _confirmed, _reason = check_price_volume_confirmation(_snap, pos.direction.value)
                except Exception as exc:
                    logger.debug(f"update_positions: {pos.symbol} re-confirmation check failed: {exc}")
                    _confirmed = True   # fail open -- this is a supplementary exit, not the primary SL
                if not _confirmed:
                    try:
                        async with session.begin_nested():
                            closed_trade = await close_paper_trade(pos, price, "CONFIRMATION_LOST", session)
                        auto_closed.append({
                            "trade_id":    closed_trade.id,
                            "symbol":      closed_trade.symbol,
                            "reason":      "CONFIRMATION_LOST",
                            "exit_price":  price,
                            "pnl":         closed_trade.pnl,
                            "entry_price": closed_trade.entry_price,
                            "size_units":  closed_trade.size_units,
                            "direction":   pos.direction.value,
                        })
                        logger.warning(
                            f"[confirmation_lost] {pos.symbol}: {_reason} — exited @ ₹{price:.2f} "
                            f"before Target 1 (DIRECT_NEWS re-check)"
                        )
                    except Exception as exc:
                        logger.warning(f"update_positions: {pos.symbol} confirmation-lost close failed: {exc}")
                    continue

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
                    # Fresh re-analysis right at the T1 tick (2026-07-22,
                    # user-requested) -- decide CONTINUE (existing mechanical
                    # partial+trail below) vs EXIT (reversal risk -> close
                    # the WHOLE remaining position now, watch for re-entry).
                    from engine.agent.t1_reanalysis import analyze_t1_hit
                    t1_analysis = await analyze_t1_hit(
                        symbol=pos.symbol, direction="BUY", entry_price=pos.entry_price,
                        price=price, t1=t1, t2=tm.get("target_2") or t1,
                        unrealised_pct=pos.unrealised_pct, session=session,
                    )
                    if t1_analysis["decision"] == "EXIT":
                        closed = await _t1_reversal_exit(pos, price, t1_analysis, session)
                        auto_closed.append(closed)
                        continue
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
                        if hasattr(pos, "trade") and pos.trade:
                            pos.trade.stop_loss = pos.stop_loss
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
                        if hasattr(pos, "trade") and pos.trade:
                            pos.trade.stop_loss = pos.stop_loss
                        trailed = True
            else:  # SELL
                peak = min(peak, price)
                if not trailing and t1 and price <= t1:
                    from engine.agent.t1_reanalysis import analyze_t1_hit
                    t1_analysis = await analyze_t1_hit(
                        symbol=pos.symbol, direction="SELL", entry_price=pos.entry_price,
                        price=price, t1=t1, t2=tm.get("target_2") or t1,
                        unrealised_pct=pos.unrealised_pct, session=session,
                    )
                    if t1_analysis["decision"] == "EXIT":
                        closed = await _t1_reversal_exit(pos, price, t1_analysis, session)
                        auto_closed.append(closed)
                        continue
                    trailing = True
                if trailing and trail_dist > 0:
                    new_stop = peak + trail_dist
                    if new_stop < pos.stop_loss:
                        pos.stop_loss = round(new_stop, 4)
                        if hasattr(pos, "trade") and pos.trade:
                            pos.trade.stop_loss = pos.stop_loss
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
            # The old `post_event_handled` branch was dropped 2026-08-17: the
            # post-event rule now closes the position itself (above) instead of
            # tightening a stop for this check to later trip, so that flag is
            # never set and the branch was unreachable.
            is_trailing = bool(tm.get("trailing")) if tm else False
            reason = (
                "TRAIL_STOP" if hit_sl and is_trailing
                else "STOP_LOSS" if hit_sl else "TAKE_PROFIT"
            )
            try:
                # SAVEPOINT: isolate this close so a deadlock/DB error here can't
                # poison the shared session and silently break SL/TP monitoring
                # for every other open position in this cycle (this is the exact
                # bug class that let 29 MIS shorts run unmonitored for 3 days on
                # 2026-07-03 — that failure was in intraday_squareoff, but this
                # loop is the actual per-minute SL/TP watcher and had zero
                # per-position isolation at all).
                async with session.begin_nested():
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
            except Exception as exc:
                logger.warning(f"update_positions: {pos.symbol} SL/TP close failed: {exc}")
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
                    try:
                        async with session.begin_nested():   # see SAVEPOINT note above
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
                    except Exception as exc:
                        logger.warning(f"update_positions: {pos.symbol} stale-exit close failed: {exc}")
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
