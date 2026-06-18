"""Risk Manager — Varsity Module 9. Unconditional veto authority.

Reference: trading_agent/risk.py (extended with all 7 gate types).
"""
from __future__ import annotations

from utils.config import settings
from utils.logger import logger


def vix_size_factor(vix: float) -> float:
    """Return a [VIX_SIZE_SCALE_MIN, 1.0] multiplier based on India VIX.

    Linear decay from 1.0 at VIX_HIGH_THRESHOLD to VIX_SIZE_SCALE_MIN at
    VIX_EXTREME_THRESHOLD. Flat at 1.0 below the high threshold; floored at
    VIX_SIZE_SCALE_MIN above the extreme threshold.
    """
    lo      = settings.VIX_HIGH_THRESHOLD     # e.g. 22.0
    hi      = settings.VIX_EXTREME_THRESHOLD  # e.g. 30.0
    sf_min  = settings.VIX_SIZE_SCALE_MIN     # e.g. 0.50
    if vix <= lo:
        return 1.0
    if vix >= hi:
        return sf_min
    return round(1.0 - (1.0 - sf_min) * (vix - lo) / max(hi - lo, 1e-9), 4)


def position_size(equity: float, risk_pct: float, entry: float, stop: float) -> int:
    """Varsity M9: shares = (equity × risk%) / (entry - stop)."""
    risk_amount = equity * risk_pct
    per_share   = abs(entry - stop)
    if per_share <= 0:
        return 0
    return int(risk_amount // per_share)


def capital_utilization_size(
    equity: float,
    conviction: float,
    entry: float,
    stop: float,
    deployed_notional: float,
    *,
    size_factor: float = 1.0,
    vix: float = 15.0,
) -> tuple[int, str]:
    """Conviction-weighted capital deployment with a hard risk guard.

    Deploys capital toward a target notional (so the book actually uses the
    equity) instead of only the tiny risk-based size. Bounded by, in order:
      1. Conviction-scaled target weight  (2% → 5% as conviction → CONVICTION_HIGH)
         × VIX scalar (1.0 → 0.5 as India VIX rises from 22 → 30)
      2. Hard per-position cap             (5% of equity = ₹1L on ₹20L)
      3. Cash-buffer room                  (total deploy ≤ 1 − MIN_CASH_BUFFER)
      4. Risk guard                        (loss at stop ≤ AGENT_MAX_RISK_PER_TRADE)

    Returns (qty, reason). reason names the binding constraint for transparency.
    """
    if entry <= 0:
        return 0, "bad_entry"

    # 1. Conviction-weighted target weight.
    conv_high = max(1.0, settings.CONVICTION_HIGH)
    conv_frac = min(1.0, max(0.0, conviction) / conv_high)
    max_w  = settings.AGENT_MAX_POSITION_WEIGHT          # e.g. 0.05 (5%)
    base_w = max_w * 0.40                                # e.g. 0.02 (2%) at zero conviction
    vix_sf   = vix_size_factor(vix)
    combined_sf = max(0.0, size_factor) * vix_sf
    target_w = (base_w + (max_w - base_w) * conv_frac) * combined_sf

    # 2. Hard per-position cap (AGENT_MAX_POSITION_WEIGHT).
    target_w = min(target_w, max_w)
    target_notional = equity * target_w

    # 3. Cash-buffer room (don't breach the min cash reserve). One setting —
    # AGENT_CASH_BUFFER_MIN (20%) — shared with the risk gate for consistency.
    max_deploy = equity * (1.0 - settings.AGENT_CASH_BUFFER_MIN)
    room = max(0.0, max_deploy - deployed_notional)
    target_notional = min(target_notional, room)
    if target_notional <= 0:
        return 0, "cash_buffer_full"

    qty_capital = int(target_notional // entry)
    binding = "capital_target"

    # 4. Risk guard — never lose more than the per-trade risk cap at the stop.
    rps = abs(entry - stop)
    if rps > 0:
        qty_risk = int((equity * settings.AGENT_MAX_RISK_PER_TRADE) // rps)
        if qty_risk < qty_capital:
            binding = "risk_guard"
        qty = min(qty_capital, qty_risk)
    else:
        qty = qty_capital

    return max(0, qty), binding


class RiskManagerAgent:

    def __init__(self, portfolio_ctx: dict):
        self.ctx = portfolio_ctx

    def can_take_trade(self, candidate, equity: float) -> tuple[bool, str]:
        ctx = self.ctx

        # ── Circuit breakers (Varsity M9.2) ──────────────────────────────────
        if ctx.get("daily_pnl_pct", 0) <= -settings.AGENT_DAILY_DD_STOP:
            return False, "DAILY_DD_STOP"
        if ctx.get("weekly_pnl_pct", 0) <= -settings.AGENT_WEEKLY_DD_STOP:
            return False, "WEEKLY_DD_STOP"
        if ctx.get("monthly_pnl_pct", 0) <= -settings.AGENT_MONTHLY_DD_STOP:
            return False, "MONTHLY_DD_STOP"

        # ── Behavioral locks (Varsity M12) — skipped in paper mode ──────────────
        # Paper trading is for learning/simulation — behavioral limits would just
        # prevent the agent from demonstrating its full scan output.
        is_paper = getattr(settings, "PAPER_MODE", True)
        if not is_paper:
            if ctx.get("consec_losses_today", 0) >= settings.AGENT_CONSEC_LOSS_LOCKOUT:
                return False, "CONSECUTIVE_LOSS_LOCKOUT"
            if ctx.get("new_entries_today", 0) >= settings.AGENT_MAX_NEW_ENTRIES_DAY:
                return False, "MAX_DAILY_ENTRIES"

        # ── Position sizing (capital-utilization, same as the executor) ──────────
        risk_per_share = abs(candidate.entry - candidate.stop)
        if risk_per_share <= 0:
            return False, "ZERO_RISK_DISTANCE"

        deployed = ctx.get("deployed_notional", max(0.0, equity - ctx.get("cash", equity)))
        conviction = abs(getattr(candidate, "master_score", None) or candidate.confidence)
        live_vix: float = 15.0
        try:
            from crawler.live_prices import PRICE_CACHE
            live_vix = float(PRICE_CACHE.get("^INDIAVIX", {}).get("price", 15) or 15)
        except Exception:
            pass
        qty, _reason = capital_utilization_size(
            equity, conviction, candidate.entry, candidate.stop,
            deployed, size_factor=getattr(candidate, "size_factor", 1.0), vix=live_vix,
        )
        if qty <= 0:
            return False, f"QTY_ZERO:{_reason}"

        # Risk guard: a single trade's stop-loss must not exceed the per-trade cap.
        trade_risk_pct = (qty * risk_per_share) / equity
        if trade_risk_pct > settings.AGENT_MAX_RISK_PER_TRADE + 1e-9:
            return False, "OVERSIZE_TRADE"

        # ── Portfolio risk cap + cash buffer ──────────────────────────────────
        if ctx.get("open_risk_pct", 0) + trade_risk_pct > settings.AGENT_MAX_OPEN_RISK:
            return False, "PORTFOLIO_RISK_CAP"
        trade_value = qty * candidate.entry
        cash        = ctx.get("cash", equity)
        if cash - trade_value < settings.AGENT_CASH_BUFFER_MIN * equity:
            return False, "CASH_BUFFER"

        # ── Diversification ───────────────────────────────────────────────────
        if candidate.symbol in ctx.get("open_symbols", []):
            return False, "ALREADY_IN_POSITION"

        # Correlation cluster guard (Varsity M16)
        for open_sym in ctx.get("open_symbols", []):
            pair = tuple(sorted([candidate.symbol, open_sym]))
            corr = ctx.get("symbol_correlations", {}).get(pair, 0.0)
            if corr > 0.70:
                return False, f"HIGH_CORRELATION:{open_sym}"

        # Sector exposure gate — no single sector may exceed 20% of equity.
        # Uses the candidate's sector tag (set by agent_loop before the risk call).
        # Sectors with no open positions always pass (gate only applies when the
        # new trade would push the sector over the limit).
        candidate_sector = getattr(candidate, "sector", None)
        if candidate_sector:
            max_sector_pct = getattr(settings, "AGENT_MAX_SECTOR_EXPOSURE", 0.20)
            sector_exp     = ctx.get("sector_exposure", {})
            current_pct    = sector_exp.get(candidate_sector, 0.0)
            new_notional   = qty * candidate.entry
            incoming_pct   = new_notional / max(equity, 1)
            if current_pct + incoming_pct > max_sector_pct + 1e-9:
                return False, (
                    f"SECTOR_EXPOSURE_CAP:{candidate_sector} "
                    f"current={current_pct*100:.1f}% "
                    f"+incoming={incoming_pct*100:.1f}% "
                    f"> limit={max_sector_pct*100:.0f}%"
                )

        # ── Confidence gate (Varsity M12 — Innerworth) ───────────────────────
        if candidate.confidence < settings.AGENT_CONFIDENCE_THRESHOLD:
            return False, f"LOW_CONFIDENCE:{candidate.confidence}"

        # ── Minimum R:R (Varsity M9) ──────────────────────────────────────────
        if candidate.risk_reward < 1.5:
            return False, f"POOR_RR:{candidate.risk_reward}"

        return True, "OK"
