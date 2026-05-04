"""Risk management layer for AutoTrade Pro paper trading.

All checks operate on VIRTUAL money only — the same logic that would apply
to a real account but applied to a paper-trading simulation.

Public API
----------
validate_signal(signal, wallet_balance, open_positions, session) -> (bool, str)
calculate_position_size(signal, balance) -> dict
get_daily_stats(session) -> dict
"""

from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OpenPosition, PaperTrade, TradeStatus
from engine.signal_generator import TradingSignal
from utils.config import settings
from utils.logger import logger


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_start() -> datetime:
    """Return midnight UTC of the current calendar day."""
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


async def _today_closed_pnl(session: AsyncSession) -> float:
    """Sum all PnL from trades closed today (UTC). Returns 0.0 if none."""
    result = await session.execute(
        select(func.coalesce(func.sum(PaperTrade.pnl), 0.0)).where(
            and_(
                PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
                PaperTrade.closed_at >= _today_start(),
                PaperTrade.pnl.isnot(None),
            )
        )
    )
    return float(result.scalar_one())


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Signal validator
# ═══════════════════════════════════════════════════════════════════════════════

async def validate_signal(
    signal:          TradingSignal,
    wallet_balance:  float,
    open_positions:  list[OpenPosition],
    session:         AsyncSession,
) -> tuple[bool, str]:
    """Run all pre-trade risk checks against a TradingSignal.

    Checks are evaluated in order of severity — cheapest DB-free checks first,
    heavier DB queries deferred to later steps.

    Parameters
    ----------
    signal          : The candidate TradingSignal to evaluate.
    wallet_balance  : Current virtual cash balance (not equity).
    open_positions  : List of currently open OpenPosition ORM objects.
    session         : Async SQLAlchemy session (used for daily loss query).

    Returns
    -------
    (True, 'OK')                  — all checks passed, trade is approved.
    (False, '<reason string>')    — check failed; reason is human-readable.
    """

    # ── Check 1: Maximum concurrent open positions ────────────────────────────
    if len(open_positions) >= settings.MAX_OPEN_POSITIONS:
        reason = (
            f"Max {settings.MAX_OPEN_POSITIONS} positions limit reached "
            f"({len(open_positions)} currently open)"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 2: Daily loss circuit-breaker ───────────────────────────────────
    today_pnl = await _today_closed_pnl(session)
    if today_pnl < 0:
        limit = wallet_balance * settings.MAX_DAILY_LOSS
        if abs(today_pnl) >= limit:
            reason = (
                f"Daily loss limit reached "
                f"(lost ${abs(today_pnl):.2f} today, "
                f"limit is {settings.MAX_DAILY_LOSS * 100:.0f}% of balance "
                f"= ${limit:.2f})"
            )
            _log_rejection(signal.symbol, reason)
            return False, reason

    # ── Check 3: Minimum signal confidence ───────────────────────────────────
    _MIN_CONFIDENCE = 40.0
    if signal.confidence < _MIN_CONFIDENCE:
        reason = (
            f"Confidence too low: {signal.confidence:.0f}% "
            f"(minimum {_MIN_CONFIDENCE:.0f}%)"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 4: Risk:Reward ratio ────────────────────────────────────────────
    risk   = abs(signal.entry_price - signal.stop_loss)
    reward = abs(signal.take_profit  - signal.entry_price)

    if risk <= 0:
        reason = "Stop-loss is equal to entry price — cannot calculate R:R ratio"
        _log_rejection(signal.symbol, reason)
        return False, reason

    rr = reward / risk
    if rr < settings.MIN_RISK_REWARD:
        reason = (
            f"R:R ratio {rr:.2f} below minimum {settings.MIN_RISK_REWARD:.1f} "
            f"(risk=${risk:.5f}  reward=${reward:.5f})"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 5: Sufficient virtual balance ───────────────────────────────────
    pos = calculate_position_size(signal, wallet_balance)
    # Require that the 10% margin for this position doesn't exceed
    # 50% of the available balance (leaves room for other positions).
    required_margin = pos["usd_value"] * 0.1
    if required_margin > wallet_balance * 0.5:
        reason = (
            f"Insufficient virtual balance for this position "
            f"(need ${required_margin:.2f} margin, "
            f"50% of balance = ${wallet_balance * 0.5:.2f})"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 6: No duplicate open position for this symbol ───────────────────
    open_symbols = {p.symbol for p in open_positions}
    if signal.symbol in open_symbols:
        reason = f"Already have an open position for {signal.symbol}"
        _log_rejection(signal.symbol, reason)
        return False, reason

    logger.info(
        f"RISK OK    │ {signal.symbol:<12} │ "
        f"conf={signal.confidence:.0f}%  "
        f"RR={rr:.2f}  "
        f"size=${pos['usd_value']:.2f}  "
        f"open={len(open_positions)}/{settings.MAX_OPEN_POSITIONS}"
    )
    return True, "OK"


def _log_rejection(symbol: str, reason: str) -> None:
    logger.warning(f"RISK REJECTED │ {symbol:<12} │ Reason: {reason}")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Position sizing
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_position_size(signal: TradingSignal, balance: float) -> dict:
    """Compute virtual position size using fixed fractional risk.

    Sizes the trade so that a stop-loss hit costs exactly
    ``MAX_RISK_PER_TRADE × balance`` in virtual dollars.

    Parameters
    ----------
    signal  : TradingSignal with entry_price and stop_loss populated.
    balance : Current virtual wallet balance.

    Returns
    -------
    dict with keys:
        units        — number of units to trade
        usd_value    — total notional value of the position (units × entry)
        risk_amount  — virtual dollars at risk on this trade
        risk_percent — risk as a percentage of balance (e.g. 2.0 for 2%)
    """
    risk_amount   = balance * settings.MAX_RISK_PER_TRADE
    risk_per_unit = abs(signal.entry_price - signal.stop_loss)

    units     = risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0
    usd_value = units * signal.entry_price

    result = {
        "units":        round(units, 6),
        "usd_value":    round(usd_value, 4),
        "risk_amount":  round(risk_amount, 4),
        "risk_percent": round(settings.MAX_RISK_PER_TRADE * 100, 2),
    }

    logger.debug(
        f"Position size  {signal.symbol}  "
        f"units={result['units']}  "
        f"usd=${result['usd_value']:.2f}  "
        f"risk=${result['risk_amount']:.2f} ({result['risk_percent']}%)"
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Daily performance stats
# ═══════════════════════════════════════════════════════════════════════════════

async def get_daily_stats(session: AsyncSession) -> dict:
    """Return today's (UTC) trading statistics from closed paper trades.

    Queries the paper_trades table for all trades closed since midnight UTC.

    Returns
    -------
    dict with keys:
        trades_today   — total trades closed today
        wins_today     — trades where pnl > 0
        losses_today   — trades where pnl <= 0
        pnl_today      — net PnL for the day (can be negative)
        win_rate_today — wins / trades_today as a percentage (0.0 if no trades)
    """
    today = _today_start()

    rows_result = await session.execute(
        select(PaperTrade.pnl).where(
            and_(
                PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
                PaperTrade.closed_at >= today,
                PaperTrade.pnl.isnot(None),
            )
        )
    )
    pnl_values = [float(v) for v in rows_result.scalars().all()]

    trades_today = len(pnl_values)
    wins_today   = sum(1 for p in pnl_values if p > 0)
    losses_today = trades_today - wins_today
    pnl_today    = sum(pnl_values)
    win_rate     = (wins_today / trades_today * 100.0) if trades_today else 0.0

    stats = {
        "trades_today":   trades_today,
        "wins_today":     wins_today,
        "losses_today":   losses_today,
        "pnl_today":      round(pnl_today, 4),
        "win_rate_today": round(win_rate, 2),
    }

    logger.debug(
        f"Daily stats  trades={trades_today}  "
        f"W/L={wins_today}/{losses_today}  "
        f"pnl=${pnl_today:+.2f}  "
        f"win_rate={win_rate:.1f}%"
    )
    return stats
