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

# Minimum acceptable distance between entry and stop-loss, as a fraction of
# entry price. Root-caused 2026-07-27 (AUBANK.NS): when indicators were
# computed off 1-MINUTE candles instead of daily ones, ATR came out ~0.1% of
# price instead of a normal 1.5-3% daily ATR, producing a 2x-ATR stop only
# 0.21% from entry — clipped by ordinary tick/spread noise regardless of
# whether the trade thesis played out (the stock kept climbing AFTER the
# stop-out). This floor is deliberately timeframe-agnostic and applies to
# EVERY tier (dynamic/ATR/static) and every caller (news, hub, pre-event,
# intraday) as a backstop — a tier's stop is only accepted if it clears this
# floor; otherwise the next tier is tried, and the static % fallback (which
# is always >= this floor) is the final guarantee.
MIN_STOP_DISTANCE_PCT: float = 0.015   # 1.5% minimum SL distance from entry


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
            # Sanity: stop on the correct side, targets beyond entry in trade
            # direction, AND far enough away to survive ordinary noise (see
            # MIN_STOP_DISTANCE_PCT docstring above).
            if valid and abs(entry - sl) / entry < MIN_STOP_DISTANCE_PCT:
                valid = False
            # target_1 must be at least as far from entry as the stop-loss
            # (R:R >= 1:1 at the trailing-stop trigger) -- matches the ATR
            # tier's own T1 = 2xATR = SL-distance relationship below. Without
            # this, a T1 sourced from a stray pivot/resistance level with no
            # relation to risk can sit a fraction of a percent from entry,
            # triggering an economically meaningless scale-out + breakeven
            # stop almost immediately after entry (INOXGREEN + PFC,
            # 2026-08-04: T1 only 0.24%/0.65% away vs 5.1%/3.24% real stops
            # and 15.3%/10% T2 targets -- neither trade got a real chance to
            # work before being cut to breakeven).
            if valid and abs(t1 - entry) < abs(entry - sl):
                valid = False
            if valid:
                # Carry S&R levels so validate_signal() can apply Varsity's 4% gate.
                _sup = setup.get("support", 0.0) or 0.0
                _res = setup.get("resistance", 0.0) or 0.0
                if is_buy and sl < entry and t1 > entry and t2 > t1:
                    return {"stop_loss": round(sl, 2), "target_1": round(t1, 2),
                            "target_2": round(t2, 2), "atr": round(atr, 2),
                            "source": "dynamic", "support": _sup, "resistance": _res}
                if (not is_buy) and sl > entry and t1 < entry and t2 < t1:
                    return {"stop_loss": round(sl, 2), "target_1": round(t1, 2),
                            "target_2": round(t2, 2), "atr": round(atr, 2),
                            "source": "dynamic", "support": _sup, "resistance": _res}
        except Exception:
            pass  # fall through to ATR

    # ── 2. ATR-based ─────────────────────────────────────────────────────────
    # 2x/4x ATR distance, but only if ATR itself implies a stop that clears
    # MIN_STOP_DISTANCE_PCT -- a tiny ATR (e.g. computed off 1-minute candles
    # by a caller upstream) must not silently produce a whipsaw-prone stop;
    # fall through to the static % tier instead.
    if atr > 0 and entry > 0 and (2 * atr) / entry >= MIN_STOP_DISTANCE_PCT:
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

    # ── Check 1a-ii: Concurrency cap (diversification, 2026-08-17) ───────────
    # Distinct from max_pos above: that is a runaway-loop guard (500), this
    # bounds how many correlated names the book can hold at once. The 3-17 Aug
    # post-mortem measured a peak of 101 concurrent positions (~69% of equity)
    # with 99% long exposure — capital limits alone never constrained breadth
    # because each individual position was small (median 0.68% of equity).
    max_concurrent = cfg.max_concurrent_positions
    if max_concurrent > 0 and len(open_positions) >= max_concurrent:
        reason = (
            f"Concurrency cap reached ({len(open_positions)}/{max_concurrent} positions) — "
            f"book already at its diversification limit"
        )
        _log_rejection(signal.symbol, reason)
        return False, reason

    this_pos  = calculate_position_size(signal, wallet_balance, cfg=cfg)
    this_risk = this_pos["risk_amount"]
    this_notional = this_pos["usd_value"]

    # ── Checks 1b/1c: portfolio risk budget + cash buffer ────────────────────
    # Both are capital-based, count-independent (Check 1a above is the only
    # count-based one). If either fails, try ONE thesis-based reallocation
    # (2026-07-29, user request) before rejecting outright: if some open
    # position's own strategy no longer endorses it, free it and re-check
    # once. A position whose thesis still holds is never touched, even if
    # it's down -- this is a capital-reallocation opportunity, not a general
    # loss-cutting rule (see engine/portfolio_reallocation.py docstring).
    _reallocated = False
    for _attempt in range(2):
        port_risk_fail   = equity > 0 and (current_open_risk + this_risk) > max_port_risk * equity
        cash_buffer_fail = equity > 0 and (deployed_capital + this_notional) > (1 - min_cash_buffer) * equity
        if not port_risk_fail and not cash_buffer_fail:
            break
        if _reallocated or _attempt == 1:
            if port_risk_fail:
                reason = (
                    f"Portfolio risk budget full: open {current_open_risk/equity*100:.1f}% "
                    f"+ this {this_risk/equity*100:.1f}% > {max_port_risk*100:.0f}% of equity"
                )
            else:
                reason = (
                    f"Cash buffer: deploying ₹{this_notional:.0f} would breach "
                    f"the {min_cash_buffer*100:.0f}% cash reserve "
                    f"(deployed ₹{deployed_capital:.0f} / equity ₹{equity:.0f})"
                )
            _log_rejection(signal.symbol, reason)
            return False, reason
        try:
            from engine.portfolio_reallocation import try_reallocate_for_candidate
            _reallocated = await try_reallocate_for_candidate(open_positions, session)
        except Exception as exc:
            logger.warning(f"[risk_manager] reallocation attempt failed for {signal.symbol}: {exc}")
            _reallocated = False
        if not _reallocated:
            continue  # nothing eligible to free -- next loop iteration re-fails and returns
        # A position closed -- recompute capital state before re-checking.
        open_positions    = list((await session.execute(select(OpenPosition))).scalars().all())
        deployed_capital  = sum(p.size_usd for p in open_positions)
        unrealised        = sum(getattr(p, "unrealised_pnl", 0.0) or 0.0 for p in open_positions)
        equity            = wallet_balance + deployed_capital + unrealised
        current_open_risk = sum(
            abs(p.entry_price - p.stop_loss) * p.size_units for p in open_positions
        )

    # ── Check 1d: Per-sector concentration (diversification, 2026-08-17) ─────
    # Two bounds on one sector: how many NAMES it may hold, and how much
    # CAPITAL it may absorb. Both are needed — 8 tiny positions and 2 large
    # ones are different risks, and the post-mortem saw sector outcomes
    # dominate selection outcomes (IT/Infra/Energy -39,322 vs Pharma/Metals
    # +34,472 over the same fortnight).
    #
    # Deliberately fail-OPEN on an unresolvable sector: _get_sector_for_symbol
    # reads a cached map that legitimately misses newly-listed/illiquid names,
    # and silently blocking every unmapped symbol would quietly shrink the
    # tradable universe in a way that looks like "no signals" rather than a
    # rejection. Unmapped symbols still face every other check.
    max_per_sector  = cfg.max_positions_per_sector
    max_sector_pct  = cfg.max_sector_capital_pct
    if (max_per_sector > 0 or max_sector_pct > 0) and open_positions:
        try:
            from engine.intelligence_hub import _get_sector_for_symbol
            _cand_sector = _get_sector_for_symbol(signal.symbol)
        except Exception as exc:
            logger.debug(f"[risk_manager] sector lookup failed for {signal.symbol}: {exc}")
            _cand_sector = None

        if _cand_sector:
            _same_sector = []
            for p in open_positions:
                try:
                    if _get_sector_for_symbol(p.symbol) == _cand_sector:
                        _same_sector.append(p)
                except Exception:
                    continue

            if max_per_sector > 0 and len(_same_sector) >= max_per_sector:
                reason = (
                    f"Sector cap: {_cand_sector} already holds "
                    f"{len(_same_sector)}/{max_per_sector} positions"
                )
                _log_rejection(signal.symbol, reason)
                return False, reason

            if max_sector_pct > 0 and equity > 0:
                _sector_capital = sum(p.size_usd for p in _same_sector)
                if (_sector_capital + this_notional) > max_sector_pct * equity:
                    reason = (
                        f"Sector capital cap: {_cand_sector} at "
                        f"₹{_sector_capital:.0f} + this ₹{this_notional:.0f} "
                        f"> {max_sector_pct*100:.0f}% of ₹{equity:.0f} equity"
                    )
                    _log_rejection(signal.symbol, reason)
                    return False, reason

    # ── Check 1e: Per-strategy allocation cap (P2-2, 2026-08-17) ─────────────
    # No single strategy should be able to become the whole book while its edge
    # is unproven. PRE_EVENT_EXPECTATION_GAP was 243 of 266 trades (91%) at a
    # profit factor of 1.069 -- statistically indistinguishable from noise --
    # and no component of its score correlated with outcome (all |r| < 0.18).
    # The sector/concurrency caps above bound WHAT the book holds; this bounds
    # how much of the book any one strategy's thesis can represent, so a single
    # broken thesis can't take the account with it.
    #
    # Enforced on deployed CAPITAL rather than trade count: a strategy holding
    # 30 tiny positions is a smaller risk than one holding 5 large ones, and
    # capital is what actually gets lost. Queried from PaperTrade because
    # OpenPosition carries no strategy attribution and lazy-loading .trade
    # inside an async session raises.
    max_strategy_pct = cfg.max_strategy_capital_pct
    _cand_strategy = getattr(signal, "strategy", getattr(signal, "strategy_name", None))
    if max_strategy_pct > 0 and _cand_strategy and equity > 0:
        _res = await session.execute(
            select(func.coalesce(func.sum(PaperTrade.size_usd), 0.0)).where(
                and_(
                    PaperTrade.status == TradeStatus.OPEN,
                    PaperTrade.strategy_name == _cand_strategy,
                )
            )
        )
        _strategy_capital = float(_res.scalar_one())
        if (_strategy_capital + this_notional) > max_strategy_pct * equity:
            reason = (
                f"Strategy allocation cap: {_cand_strategy} at "
                f"₹{_strategy_capital:.0f} + this ₹{this_notional:.0f} "
                f"> {max_strategy_pct*100:.0f}% of ₹{equity:.0f} equity"
            )
            _log_rejection(signal.symbol, reason)
            return False, reason

    # ── Check 2: Daily loss circuit-breaker (mark-to-market) ──────────────────
    # P2.11 fix: measure the day's loss as realised-closed P&L PLUS the current
    # unrealised P&L of open positions. The old check counted only closed trades,
    # so a book sitting on a large OPEN drawdown never tripped the breaker and the
    # agent kept adding risk into a losing day. `unrealised` is computed above.
    today_closed = await _today_closed_pnl(session)
    today_pnl    = today_closed + unrealised
    if today_pnl < 0:
        limit = wallet_balance * max_dl
        if abs(today_pnl) >= limit:
            reason = (
                f"Daily loss limit reached (mark-to-market ${abs(today_pnl):.2f} "
                f"= closed ${today_closed:.2f} + open ${unrealised:.2f}; "
                f"limit {max_dl * 100:.0f}% of balance = ${limit:.2f})"
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

    # ── Check 3b: S&R proximity gate (Varsity checklist item 2 — MANDATORY) ───
    # The stop-loss must sit near a genuine S&R level.  If the nearest support
    # (for BUY) or resistance (for SELL) is >4% from the SL, there is no
    # technical backstop and the trade is skipped.
    # Gate only fires when sr_support/sr_resistance were populated by the hub
    # pipeline (via compute_trade_levels → build_trade_setup).  Signals that
    # don't carry S&R data (sr_support == 0) bypass the check rather than being
    # falsely rejected.
    _is_buy_dir = signal.action in ("BUY", "STRONG_BUY")
    _sr_level   = (getattr(signal, "sr_support", 0.0) or 0.0) if _is_buy_dir \
                  else (getattr(signal, "sr_resistance", 0.0) or 0.0)
    if _sr_level > 0 and signal.stop_loss > 0:
        _sr_dist_pct = abs(signal.stop_loss - _sr_level) / signal.stop_loss * 100
        _SR_MAX_PCT  = float(getattr(settings, "SR_MAX_DIST_PCT", 4.0))
        if _sr_dist_pct > _SR_MAX_PCT:
            reason = (
                f"S&R gate: SL ₹{signal.stop_loss:.2f} is {_sr_dist_pct:.1f}% from "
                f"nearest {'support' if _is_buy_dir else 'resistance'} "
                f"₹{_sr_level:.2f} (max {_SR_MAX_PCT:.0f}%)"
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
    # Varsity Ch 14 Percentage Risk: units = max_risk / SL_distance_per_share.
    # SL distance reflects the actual pattern-specific risk (e.g. below hammer low,
    # below S&R) and is more precise than ATR for EOD swing setups. ATR (Ch 13.4
    # Percentage Volatility) is the fallback when SL is not meaningful.
    atr_val = getattr(signal, "atr", 0.0) or 0.0
    sl_dist = abs(signal.entry_price - signal.stop_loss)
    risk_per_unit = sl_dist if sl_dist > 0 else atr_val

    # Whole shares only — NSE/BSE equity trades in integer quantity; a size like
    # 1.2 shares is not a legal order. Floor the risk-derived size to an int.
    raw_units = risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0
    units     = int(raw_units)

    # Hard cap at AGENT_MAX_POSITION_WEIGHT (default 5%) — one position can never
    # exceed this fraction of balance regardless of stop distance or confidence.
    # Shorts capped at half that (2.5%).
    _max_weight = float(getattr(settings, "AGENT_MAX_POSITION_WEIGHT", 0.05))
    if _is_short:
        _max_weight *= 0.5
    max_notional = balance * _max_weight
    if signal.entry_price > 0 and units * signal.entry_price > max_notional:
        units = int(max_notional // signal.entry_price)   # floored whole shares

    # Notional reflects the actual whole-share position so wallet accounting is exact.
    usd_value = units * signal.entry_price

    result = {
        "units":        units,                  # integer share count
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

def _kelly_percent(pnl_values: list[float]) -> float:
    """Compute Kelly% from a list of closed-trade P&L values.

    Varsity Ch 14.2 formula: Kelly% = W - [(1-W) / R]
      W = winning trades / total trades
      R = average gain of winning trades / average loss of losing trades

    Returns 0.0 when there is insufficient history (< 10 trades).
    Clamped to [0, 1] — negative Kelly means no edge; do not size up.
    """
    if len(pnl_values) < 10:
        return 0.0
    wins   = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p <= 0]
    if not wins or not losses:
        return 0.0
    W = len(wins) / len(pnl_values)
    avg_gain = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss <= 0:
        return 0.0
    R = avg_gain / avg_loss
    kelly = W - (1.0 - W) / R
    return max(0.0, min(1.0, kelly))


async def get_daily_stats(session: AsyncSession) -> dict:
    """Return today's (UTC) trading statistics from closed paper trades.

    Queries the paper_trades table for all trades closed since midnight UTC,
    plus all-time Kelly% from full history (Varsity Ch 14).

    Returns
    -------
    dict with keys:
        trades_today   — total trades closed today
        wins_today     — trades where pnl > 0
        losses_today   — trades where pnl <= 0
        pnl_today      — net PnL for the day (can be negative)
        win_rate_today — wins / trades_today as a percentage (0.0 if no trades)
        kelly_pct      — Kelly% from all-time history (0.0 if < 10 trades)
        kelly_risk_pct — suggested risk% per trade = kelly_pct × max_risk cap
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

    # Kelly's Criterion — Varsity Ch 14: use all-time trade history for stable
    # win-rate and win/loss ratio estimates; today alone is never enough samples.
    all_pnl_result = await session.execute(
        select(PaperTrade.pnl).where(
            and_(
                PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
                PaperTrade.pnl.isnot(None),
            )
        )
    )
    all_pnl = [float(v) for v in all_pnl_result.scalars().all()]
    kelly    = _kelly_percent(all_pnl)
    _, max_risk, _ = _conviction_risk_pct()
    # Varsity: expose Kelly% × max_cap. E.g. Kelly=30% of 5% cap = 1.5% risk.
    kelly_risk = kelly * max_risk

    stats = {
        "trades_today":   trades_today,
        "wins_today":     wins_today,
        "losses_today":   losses_today,
        "pnl_today":      round(pnl_today, 4),
        "win_rate_today": round(win_rate, 2),
        "kelly_pct":      round(kelly * 100, 2),
        "kelly_risk_pct": round(kelly_risk * 100, 2),
    }

    logger.debug(
        f"Daily stats  trades={trades_today}  "
        f"W/L={wins_today}/{losses_today}  "
        f"pnl=${pnl_today:+.2f}  "
        f"win_rate={win_rate:.1f}%  "
        f"kelly={kelly*100:.1f}%  kelly_risk={kelly_risk*100:.2f}%"
    )
    return stats
