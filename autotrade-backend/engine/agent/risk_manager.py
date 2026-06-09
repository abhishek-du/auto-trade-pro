"""Risk Manager — Varsity Module 9. Unconditional veto authority.

Reference: trading_agent/risk.py (extended with all 7 gate types).
"""
from __future__ import annotations

from utils.config import settings
from utils.logger import logger


def position_size(equity: float, risk_pct: float, entry: float, stop: float) -> int:
    """Varsity M9: shares = (equity × risk%) / (entry - stop)."""
    risk_amount = equity * risk_pct
    per_share   = abs(entry - stop)
    if per_share <= 0:
        return 0
    return int(risk_amount // per_share)


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

        # ── Position sizing ───────────────────────────────────────────────────
        risk_per_share = abs(candidate.entry - candidate.stop)
        if risk_per_share <= 0:
            return False, "ZERO_RISK_DISTANCE"

        qty = position_size(equity, settings.AGENT_MAX_RISK_PER_TRADE, candidate.entry, candidate.stop)
        if qty <= 0:
            return False, "QTY_ZERO"

        trade_risk_pct = (qty * risk_per_share) / equity
        if trade_risk_pct > settings.AGENT_MAX_RISK_PER_TRADE:
            return False, "OVERSIZE_TRADE"

        # ── Portfolio risk cap + cash buffer (paper AND live) ────────────────
        # These enforce the ₹5L virtual budget in paper mode and protect real
        # capital in live mode. Bypassing these caused 40× over-deployment.
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

        # ── Confidence gate (Varsity M12 — Innerworth) ───────────────────────
        if candidate.confidence < settings.AGENT_CONFIDENCE_THRESHOLD:
            return False, f"LOW_CONFIDENCE:{candidate.confidence}"

        # ── Minimum R:R (Varsity M9) ──────────────────────────────────────────
        if candidate.risk_reward < 1.5:
            return False, f"POOR_RR:{candidate.risk_reward}"

        return True, "OK"
