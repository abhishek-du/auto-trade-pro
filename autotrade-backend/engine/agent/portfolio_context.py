"""Portfolio Context — tracks open positions, drawdowns, cash.

Reference: trading_agent/portfolio.py (extended for live Zerodha sync).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class AgentPortfolioContext:
    equity:              float
    cash:                float
    open_positions:      dict  = field(default_factory=dict)  # {symbol: pos_dict}
    daily_pnl_pct:       float = 0.0
    weekly_pnl_pct:      float = 0.0
    monthly_pnl_pct:     float = 0.0
    consec_losses_today: int   = 0
    new_entries_today:   int   = 0
    symbol_correlations: dict  = field(default_factory=dict)

    @property
    def open_symbols(self) -> list[str]:
        return list(self.open_positions.keys())

    @property
    def open_risk_pct(self) -> float:
        if self.equity <= 0:
            return 0.0
        total = sum(
            abs(p["entry"] - p["stop"]) * p["qty"] / self.equity
            for p in self.open_positions.values()
            if p.get("stop", 0) > 0
        )
        return round(total, 4)

    def sector_exposure(self) -> dict[str, float]:
        """Return {sector: notional_fraction} for all open positions that have a sector tag."""
        if self.equity <= 0:
            return {}
        totals: dict[str, float] = {}
        for p in self.open_positions.values():
            sec = p.get("sector")
            if not sec:
                continue
            notional = p.get("qty", 0) * p.get("entry", 0)
            totals[sec] = totals.get(sec, 0.0) + notional / self.equity
        return {k: round(v, 4) for k, v in totals.items()}

    def sector_position_counts(self) -> dict[str, int]:
        """Return {sector: count} of open positions per sector."""
        counts: dict[str, int] = {}
        for p in self.open_positions.values():
            sec = p.get("sector")
            if sec:
                counts[sec] = counts.get(sec, 0) + 1
        return counts

    def to_risk_ctx(self) -> dict:
        return {
            "daily_pnl_pct":       self.daily_pnl_pct,
            "weekly_pnl_pct":      self.weekly_pnl_pct,
            "monthly_pnl_pct":     self.monthly_pnl_pct,
            "consec_losses_today": self.consec_losses_today,
            "new_entries_today":   self.new_entries_today,
            "open_risk_pct":       self.open_risk_pct,
            "cash":                self.cash,
            "open_symbols":        self.open_symbols,
            "symbol_correlations": self.symbol_correlations,
            "sector_exposure":        self.sector_exposure(),
            "sector_position_counts": self.sector_position_counts(),
        }

    def add_position(self, decision) -> None:
        self.open_positions[decision.symbol] = {
            "side":     decision.action,
            "entry":    decision.entry,
            "stop":     decision.stop,
            "target":   decision.target,
            "qty":      decision.qty,
            "strategy": decision.strategy,
        }
        self.cash  -= decision.qty * decision.entry
        self.new_entries_today += 1

    def close_position(self, symbol: str, exit_price: float) -> float:
        if symbol not in self.open_positions:
            return 0.0
        pos = self.open_positions.pop(symbol)
        if pos["side"] == "BUY":
            pnl = (exit_price - pos["entry"]) * pos["qty"]
        else:
            pnl = (pos["entry"] - exit_price) * pos["qty"]
        self.cash += pos["qty"] * pos["entry"] + pnl
        if pnl < 0:
            self.consec_losses_today += 1
        else:
            self.consec_losses_today = 0
        self.daily_pnl_pct   += pnl / max(self.equity, 1)
        self.weekly_pnl_pct  += pnl / max(self.equity, 1)
        self.monthly_pnl_pct += pnl / max(self.equity, 1)
        return pnl

    def reset_day(self) -> None:
        self.consec_losses_today = 0
        self.new_entries_today   = 0
        self.daily_pnl_pct       = 0.0
