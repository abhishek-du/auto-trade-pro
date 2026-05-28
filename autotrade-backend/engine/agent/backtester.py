"""Vectorized event-bar backtester with realistic Indian cost model.

Reference: trading_agent/backtest.py
Varsity Module 7: correct cost model (brokerage + STT + GST + exchange).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.agent.analyzer import MarketAnalyzerAgent
from engine.agent.selector import StrategySelectorAgent
from engine.agent.risk_manager import position_size
from utils.config import settings
from utils.logger import logger


def estimate_trade_cost(qty: int, price: float, side: str = "BUY") -> float:
    """Varsity M7: realistic Indian equity delivery cost."""
    notional  = qty * price
    brokerage = min(20.0, 0.0003 * notional)
    stt       = notional * 0.001           # 0.1% STT on delivery buy
    exchange  = notional * 0.0000345       # NSE turnover charges
    sebi      = notional * 0.000001
    stamp     = notional * 0.00015 if side == "BUY" else 0.0
    gst       = (brokerage + exchange + sebi) * 0.18
    return round(brokerage + stt + exchange + sebi + stamp + gst, 2)


class AgentBacktester:

    def __init__(self):
        self.analyzer = MarketAnalyzerAgent()
        self.selector = StrategySelectorAgent()

    def run(
        self,
        df: pd.DataFrame,
        symbol: str = "TEST",
        equity: float = 500_000.0,
        fund_grade: str = "WATCHLIST",
        macro_bias: int = 0,
    ) -> dict:
        warmup = settings.AGENT_WARMUP_BARS
        if len(df) < warmup + 10:
            return {"error": f"Need at least {warmup + 10} bars, got {len(df)}"}

        equity_curve = [equity]
        open_pos     = None
        trades: list = []

        for i in range(warmup, len(df)):
            window = df.iloc[: i + 1]
            bar    = window.iloc[-1]

            try:
                f = self.analyzer.compute_features(window)
            except Exception:
                equity_curve.append(equity)
                continue

            # Manage existing position
            if open_pos:
                exit_price = None
                if open_pos["side"] == "BUY":
                    if bar["low"] <= open_pos["stop"]:
                        exit_price = open_pos["stop"]
                    elif bar["high"] >= open_pos["target"]:
                        exit_price = open_pos["target"]
                else:
                    if bar["high"] >= open_pos["stop"]:
                        exit_price = open_pos["stop"]
                    elif bar["low"] <= open_pos["target"]:
                        exit_price = open_pos["target"]

                if exit_price is not None:
                    pnl = (exit_price - open_pos["entry"]) * open_pos["qty"]
                    if open_pos["side"] == "SELL":
                        pnl = -pnl
                    cost = (estimate_trade_cost(open_pos["qty"], open_pos["entry"], open_pos["side"]) +
                            estimate_trade_cost(open_pos["qty"], exit_price, "SELL"))
                    pnl -= cost
                    equity += pnl
                    open_pos.update({"exit": exit_price, "pnl": pnl, "ts_exit": str(bar.name)})
                    trades.append(open_pos)
                    open_pos = None

            # Look for entry when flat
            if open_pos is None:
                candidate = self.selector.propose(symbol, window, f, macro_bias, fund_grade)
                if candidate and candidate.confidence >= settings.AGENT_CONFIDENCE_THRESHOLD:
                    qty = position_size(equity, settings.AGENT_MAX_RISK_PER_TRADE,
                                       candidate.entry, candidate.stop)
                    if qty > 0:
                        open_pos = {
                            "side":     candidate.side,
                            "entry":    candidate.entry,
                            "stop":     candidate.stop,
                            "target":   candidate.target,
                            "qty":      qty,
                            "strategy": candidate.strategy,
                            "regime":   f.regime,
                            "ts":       str(bar.name),
                        }

            equity_curve.append(equity)

        return self._stats(np.array(equity_curve), trades, equity)

    @staticmethod
    def _stats(curve: np.ndarray, trades: list, final_equity: float) -> dict:
        if len(curve) < 2:
            return {"trades": 0, "error": "insufficient_data"}

        rets  = np.diff(curve) / (curve[:-1] + 1e-9)
        peak  = np.maximum.accumulate(curve)
        dd    = (curve - peak) / (peak + 1e-9)

        wins   = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        gross_win  = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses)) or 1e-9

        win_pct    = len(wins) / len(trades) if trades else 0.0
        avg_win    = gross_win  / len(wins)   if wins   else 0.0
        avg_loss   = gross_loss / len(losses) if losses else 0.0
        expectancy = win_pct * avg_win - (1 - win_pct) * avg_loss

        return {
            "start_equity":         float(curve[0]),
            "final_equity":         round(final_equity, 2),
            "total_return_pct":     round((final_equity / curve[0] - 1) * 100, 2),
            "total_trades":         len(trades),
            "win_rate_pct":         round(win_pct * 100, 2),
            "avg_win_inr":          round(avg_win,  2),
            "avg_loss_inr":         round(avg_loss, 2),
            "profit_factor":        round(gross_win / gross_loss, 2),
            "expectancy_per_trade": round(expectancy, 2),
            "max_drawdown_pct":     round(float(dd.min()) * 100, 2),
            "sharpe_annual":        round(float(np.sqrt(252) * rets.mean() / (rets.std() + 1e-9)), 2),
            "trades":               trades,
        }
