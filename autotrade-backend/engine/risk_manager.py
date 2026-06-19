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
from utils.runtime_config import RuntimeConfig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_start() -> datetime:
    """Return midnight UTC of the current calendar day."""
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


# ── Dynamic trade-level computation ───────────────────────────────────────────

def compute_trade_levels(action: str, entry: float, sig=None) -> dict:
    """Resolve stop-loss + two targets + ATR for a trade, in priority order.

    1. **Dynamic** — from engine.deep_analysis.build_trade_setup() using the full
       IndicatorSignals object (Supertrend / Bollinger / structure). Preferred:
       these are the same levels shown on the /s/:symbol page.
    2. **ATR-based** — stop = entry ± 2×ATR, T1 = entry ± 2×ATR, T2 = entry ± 4×ATR
       (1:1 to the first checkpoint, 2:1 to the final target). Used when dynamic
       levels are missing or invalid but ATR is available.
    3. **Static** — stop = ∓5%, T1 = ±10% — last resort only.

    Parameters
    ----------
    action : 'BUY' or 'SELL' (direction sets which side stop/targets sit on).
    entry  : entry price.
    sig    : optional IndicatorSignals (from compute_indicators) for paths 1 & 2.

    Returns
    -------
    dict with keys: stop_loss, target_1, target_2, atr, source
    """
    import math
    is_buy = action.upper() in ("BUY", "STRONG_BUY")
    atr = 0.0
    if sig is not None:
        a = getattr(sig, "atr", None)
        if a is not None and not (isinstance(a, float) and math.isnan(a)) and a > 0:
            atr = float(a)

    # ── 1. Dynamic from build_trade_setup ────────────────────────────────────
    if sig is not None and entry > 0:
        try:
            from engine.deep_analysis import build_trade_setup
            label = "BUY" if is_buy else "SELL"
            setup = build_trade_setup(sig, entry, label)
            sl, t1, t2 = setup.get("stop_loss"), setup.get("target_1"), setup.get("target_2")
            valid = all(v is not None and not (isinstance(v, float) and math.isnan(v)) and v > 0
                        for v in (sl, t1, t2))
            # Sanity: stop on the correct side, targets beyond entry in trade direction
            if valid:
                if is_buy and sl < entry and t1 > entry and t2 > t1:
                    return {"stop_loss": round(sl, 2), "target_1": round(t1, 2),
                            "target_2": round(t2, 2), "atr": round(atr, 2), "source": "dynamic"}
                if (not is_buy) and sl > entry and t1 < entry and t2 < t1:
                    return {"stop_loss": round(sl, 2), "target_1": round(t1, 2),
                            "target_2": round(t2, 2), "atr": round(atr, 2), "source": "dynamic"}
        except Exception:
            pass  # fall through to ATR

    # ── 2. ATR-based ─────────────────────────────────────────────────────────
    if atr > 0 and entry > 0:
        if is_buy:
            sl, t1, t2 = entry - 2 * atr, entry + 2 * atr, entry + 4 * atr
        else:
            sl, t1, t2 = entry + 2 * atr, entry - 2 * atr, entry - 4 * atr
        return {"stop_loss": round(sl, 2), "target_1": round(t1, 2),
                "target_2": round(t2, 2), "atr": round(atr, 2), "source": "atr"}

    # ── 3. Static last resort ────────────────────────────────────────────────
    if is_buy:
        sl, t1, t2 = entry * 0.95, entry * 1.10, entry * 1.15
    else:
        sl, t1, t2 = entry * 1.05, entry * 0.90, entry * 0.85
    return {"stop_loss": round(sl, 2), "target_1": round(t1, 2),
            "target_2": round(t2, 2), "atr": 0.0, "source": "static"}


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

    # ── Check 0: Block non-equity symbols in the equity risk pipeline ────────
    # FUT/CE/PE symbols belong to the F&O pipeline with its own margin model.
    # If they leak into validate_signal, reject immediately.
    _sym_upper = signal.symbol.upper()
    if any(tag in _sym_upper for tag in ("FUT", "NIFTY26", "BANKNIFTY26", "FINNIFTY26")):
        _is_equity_sym = not any(
            _sym_upper.endswith(sfx) for sfx in ("FUT", "CE", "PE")
        )
        if not _is_equity_sym:
            reason = f"Derivative symbol {signal.symbol} blocked — use F&O pipeline"
            _log_rejection(signal.symbol, reason)
            return False, reason

    # Load live settings once (falls back to .env defaults if key not in DB)
    cfg = await RuntimeConfig.load(session)
    max_pos       = cfg.max_open_positions          # absolute safety ceiling
    max_dl        = cfg.max_daily_loss
    min_rr        = cfg.min_risk_reward

    # Capital-utilization parameters — now DB-overridable via /api/v1/settings
    max_port_risk   = cfg.max_portfolio_risk
    min_cash_buffer = cfg.min_cash_buffer

    # Reconstruct current capital state from open positions (full-equity model).
    deployed_capital  = sum(p.size_usd for p in open_positions)
    unrealised        = sum(getattr(p, "unrealised_pnl", 0.0) or 0.0 for p in open_positions)
    equity            = wallet_balance + deployed_capital + unrealised
    current_open_risk = sum(
        abs(p.entry_price - p.stop_loss) * p.size_units for p in open_positions
    )

    # ── Check 1a: Absolute safety ceiling ────────────────────────────────────
    if len(open_positions) >= max_pos:
        reason = f"Safety ceiling reached ({len(open_positions)}/{max_pos} positions)"
        _log_rejection(signal.symbol, reason)
        return False, reason

    this_pos  = calculate_position_size(signal, wallet_balance, cfg=cfg)
    this_risk = this_pos["risk_amount"]

    # ── Check 1b: Portfolio risk budget ──────────────────────────────────────
    if equity > 0 and (current_open_risk + this_risk) > max_port_risk * equity:
        reason = (
            f"Portfolio risk budget full: open {current_open_risk/equity*100:.1f}% "
            f"+ this {this_risk/equity*100:.1f}% > {max_port_risk*100:.0f}% of equity"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 1c: Cash buffer — applies in both paper and live ───────────────
    # Keeps MIN_CASH_BUFFER fraction of equity as dry powder at all times.
    this_notional = this_pos["usd_value"]
    if equity > 0 and (deployed_capital + this_notional) > (1 - min_cash_buffer) * equity:
        reason = (
            f"Cash buffer: deploying ₹{this_notional:.0f} would breach "
            f"the {min_cash_buffer*100:.0f}% cash reserve "
            f"(deployed ₹{deployed_capital:.0f} / equity ₹{equity:.0f})"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 2: Daily loss circuit-breaker ───────────────────────────────────
    today_pnl = await _today_closed_pnl(session)
    if today_pnl < 0:
        limit = wallet_balance * max_dl
        if abs(today_pnl) >= limit:
            reason = (
                f"Daily loss limit reached "
                f"(lost ${abs(today_pnl):.2f} today, "
                f"limit is {max_dl * 100:.0f}% of balance "
                f"= ${limit:.2f})"
            )
            _log_rejection(signal.symbol, reason)
            return False, reason

    # ── Check 3: Minimum signal confidence ───────────────────────────────────
    # Single source of truth: PAPER_CONFIDENCE_THRESHOLD (.env / runtime config).
    # Calibrated to the active scoring scale — the 7-factor Hub blend compresses
    # the range vs. pure technical, so this floor moves with it. Must match the
    # pre-filter in tasks/india_tasks._india_trade_loop so the two gates agree.
    _MIN_CONFIDENCE = float(getattr(settings, "PAPER_CONFIDENCE_THRESHOLD", 40.0))
    if signal.confidence < _MIN_CONFIDENCE:
        reason = (
            f"Confidence too low: {signal.confidence:.0f}% "
            f"(minimum {_MIN_CONFIDENCE:.0f}%)"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 4: Risk:Reward ratio ────────────────────────────────────────────
    # Measure reward to the FINAL target (target_2) the position actually rides
    # to, not target_1 (which is just the trailing-stop trigger). With ATR levels
    # T1 = 2×ATR (1:1) but T2 = 4×ATR (2:1) — so checking T1 would wrongly reject
    # every dynamically-managed trade. Fall back to take_profit (T1) for legacy
    # signals that don't carry a target_2.
    final_target = getattr(signal, "target_2", 0.0) or signal.take_profit
    risk   = abs(signal.entry_price - signal.stop_loss)
    reward = abs(final_target - signal.entry_price)

    if risk <= 0:
        reason = "Stop-loss is equal to entry price — cannot calculate R:R ratio"
        _log_rejection(signal.symbol, reason)
        return False, reason

    rr = reward / risk
    if rr < min_rr - 1e-6:   # epsilon: 2×ATR/4×ATR can land at 1.9999… == 2.0
        reason = (
            f"R:R ratio {rr:.2f} below minimum {min_rr:.1f} "
            f"(risk=${risk:.5f}  reward=${reward:.5f})"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 5: Hard per-position notional cap (AGENT_MAX_POSITION_WEIGHT) ─────
    # Belt-and-suspenders: even if calculate_position_size somehow exceeds the cap,
    # reject the trade here so no single position ever exceeds 5% of equity.
    pos = this_pos
    _max_pos_weight = float(getattr(settings, "AGENT_MAX_POSITION_WEIGHT", 0.05))
    _effective_equity = wallet_balance + sum(getattr(p, "size_usd", 0.0) or 0.0 for p in open_positions)
    _max_single_notional = _effective_equity * _max_pos_weight
    if pos["usd_value"] > _max_single_notional * 1.01:   # 1% tolerance
        reason = (
            f"Position cap: ₹{pos['usd_value']:.0f} exceeds "
            f"{_max_pos_weight*100:.0f}% of equity ₹{_effective_equity:.0f} "
            f"(max ₹{_max_single_notional:.0f})"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    # ── Check 6: No duplicate open position for this symbol ───────────────────
    # Normalize .NS/.BO suffixes to catch SYMBOL vs SYMBOL.NS mismatch.
    _bare_sig = signal.symbol.replace(".NS", "").replace(".BO", "").upper()
    open_symbols = {p.symbol for p in open_positions}
    _dup = any(
        s == signal.symbol or s.replace(".NS", "").replace(".BO", "").upper() == _bare_sig
        for s in open_symbols
    )
    if _dup:
        reason = f"Already have an open position for {signal.symbol}"
        _log_rejection(signal.symbol, reason)
        return False, reason

    logger.info(
        f"RISK OK    │ {signal.symbol:<12} │ "
        f"conf={signal.confidence:.0f}%  RR={rr:.2f}  "
        f"risk={pos['risk_percent']:.1f}%  size=₹{pos['usd_value']:.0f}  "
        f"open={len(open_positions)}/{max_pos}  "
        f"port_risk={(current_open_risk + this_risk)/equity*100:.1f}%/{max_port_risk*100:.0f}%"
    )
    return True, "OK"


def _log_rejection(symbol: str, reason: str) -> None:
    logger.warning(f"RISK REJECTED │ {symbol:<12} │ Reason: {reason}")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Position sizing
# ═══════════════════════════════════════════════════════════════════════════════

def _conviction_risk_pct() -> tuple[float, float, float]:
    """(min_risk, max_risk, high_conf) for conviction-scaled sizing."""
    return (
        float(getattr(settings, "RISK_PER_TRADE_MIN", 0.015)),
        float(getattr(settings, "RISK_PER_TRADE_MAX", 0.030)),
        float(getattr(settings, "CONVICTION_HIGH", 70.0)),
    )


def calculate_position_size(signal: TradingSignal, balance: float, cfg=None) -> dict:
    """Compute virtual position size, risking a CONVICTION-SCALED fraction.

    Instead of a flat fraction, the agent commits more capital to higher-
    conviction setups: risk scales linearly from RISK_PER_TRADE_MIN at the
    confidence floor up to RISK_PER_TRADE_MAX at CONVICTION_HIGH. So the agent
    "analyses" how much to deploy per trade rather than sizing everything equally.

    Returns
    -------
    dict: units, usd_value (notional), risk_amount (₹ at risk), risk_percent.
    """
    min_risk, max_risk, high_conf = _conviction_risk_pct()
    floor = float(getattr(settings, "PAPER_CONFIDENCE_THRESHOLD", 30.0))
    conf  = float(getattr(signal, "confidence", 0.0) or 0.0)

    # Linear interpolate risk% by where confidence sits in [floor, high_conf].
    span = max(high_conf - floor, 1e-6)
    t    = max(0.0, min(1.0, (conf - floor) / span))
    risk_frac = min_risk + (max_risk - min_risk) * t

    risk_amount   = balance * risk_frac
    # Shorts carry squeeze risk — half size vs longs.
    _is_short = getattr(signal, "action", "BUY") == "SELL"
    if _is_short:
        risk_amount *= 0.5
    risk_per_unit = abs(signal.entry_price - signal.stop_loss)

    units     = risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0
    usd_value = units * signal.entry_price

    # Hard cap at AGENT_MAX_POSITION_WEIGHT (default 5%) — one position can never
    # exceed this fraction of balance regardless of stop distance or confidence.
    # Shorts capped at half that (2.5%).
    _max_weight = float(getattr(settings, "AGENT_MAX_POSITION_WEIGHT", 0.05))
    if _is_short:
        _max_weight *= 0.5
    max_notional = balance * _max_weight
    if usd_value > max_notional:
        usd_value = max_notional
        units     = usd_value / signal.entry_price if signal.entry_price > 0 else 0.0

    result = {
        "units":        round(units, 6),
        "usd_value":    round(usd_value, 4),
        "risk_amount":  round(risk_amount, 4),
        "risk_percent": round(risk_frac * 100, 2),
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
