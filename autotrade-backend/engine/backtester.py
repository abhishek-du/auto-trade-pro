"""Backtesting engine — replay historical signals against OHLCV data.

Runs the India signal generator over a rolling window of historical candles
and simulates entries/exits with ATR-based stops.  All trades are virtual;
no real money is involved.

Public API
----------
BacktestConfig                                          dataclass
BacktestTrade                                           dataclass
BacktestResult                                          dataclass
run_backtest(symbol, df, config)                        -> BacktestResult  (async)
run_backtest_all(symbols, timeframe, config, session)   -> list[BacktestResult]  (async)
"""

from __future__ import annotations

import asyncio
import datetime
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


# ── Config and result types ───────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    """Tunable parameters for a single backtest run."""
    atr_multiplier:    float = 2.0     # stop-loss = entry ± ATR × this
    risk_reward:       float = 2.0     # take-profit = entry + risk × this
    commission_pct:    float = 0.001   # 0.10 % per side (buy + sell)
    slippage_pct:      float = 0.0005  # 0.05 % per fill
    max_open:          int   = 1       # max concurrent positions per symbol
    initial_capital:   float = 100_000.0
    lookback_candles:  int   = 200     # candles fed to signal generator
    min_candles:       int   = 50      # skip if fewer candles available


@dataclass
class BacktestTrade:
    symbol:       str
    direction:    str         # 'BUY' | 'SELL'
    entry_bar:    int         # bar index
    entry_price:  float
    exit_bar:     Optional[int]
    exit_price:   Optional[float]
    stop_loss:    float
    take_profit:  float
    pnl:          float       # realised P&L in currency units
    pnl_pct:      float       # realised return %
    exit_reason:  str         # 'TP' | 'SL' | 'END'
    entry_time:   Optional[datetime.datetime] = None
    exit_time:    Optional[datetime.datetime] = None


@dataclass
class BacktestResult:
    symbol:            str
    timeframe:         str
    total_trades:      int
    winning_trades:    int
    losing_trades:     int
    win_rate:          float          # 0–100 %
    total_pnl:         float
    total_return_pct:  float
    max_drawdown_pct:  float
    sharpe_ratio:      Optional[float]
    avg_win_pct:       float
    avg_loss_pct:      float
    profit_factor:     Optional[float]
    trades:            list[BacktestTrade] = field(default_factory=list)
    equity_curve:      list[float] = field(default_factory=list)
    config:            Optional[BacktestConfig] = None


# ── ATR helper ────────────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range-based ATR (Wilder's EWM approximation)."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# ── Core backtest loop ────────────────────────────────────────────────────────

async def run_backtest(
    symbol: str,
    df: pd.DataFrame,
    config: Optional[BacktestConfig] = None,
    timeframe: str = "1d",
) -> BacktestResult:
    """Replay the India signal generator over *df* bar by bar.

    For each bar at index *i*:
      1. Feed df.iloc[:i] (max *lookback_candles* rows) to generate_india_signal().
      2. If signal == BUY or SELL and no open position: open a virtual trade.
      3. On subsequent bars: check if SL or TP was hit using the bar's H/L.
    """
    from engine.india_signal_generator import generate_india_signal

    if config is None:
        config = BacktestConfig(
            atr_multiplier=settings.ATR_MULTIPLIER,
            risk_reward=settings.MIN_RISK_REWARD,
        )

    n = len(df)
    if n < config.min_candles:
        logger.warning(f"run_backtest {symbol}: only {n} bars — need {config.min_candles}")
        return BacktestResult(
            symbol=symbol, timeframe=timeframe,
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0.0, total_pnl=0.0, total_return_pct=0.0,
            max_drawdown_pct=0.0, sharpe_ratio=None,
            avg_win_pct=0.0, avg_loss_pct=0.0, profit_factor=None,
            config=config,
        )

    atr_series = _atr(df)
    trades: list[BacktestTrade] = []
    equity  = config.initial_capital
    equity_curve: list[float] = [equity]
    peak_equity   = equity
    max_dd_pct    = 0.0
    open_position: Optional[BacktestTrade] = None

    for i in range(config.lookback_candles, n):
        bar = df.iloc[i]
        bar_high  = float(bar["high"])
        bar_low   = float(bar["low"])
        bar_close = float(bar["close"])

        # ── Check open position exit first ────────────────────────────────────
        if open_position is not None:
            pos = open_position
            exit_price: Optional[float] = None
            exit_reason = "END"

            if pos.direction == "BUY":
                if bar_low <= pos.stop_loss:
                    exit_price  = pos.stop_loss
                    exit_reason = "SL"
                elif bar_high >= pos.take_profit:
                    exit_price  = pos.take_profit
                    exit_reason = "TP"
            else:  # SELL
                if bar_high >= pos.stop_loss:
                    exit_price  = pos.stop_loss
                    exit_reason = "SL"
                elif bar_low <= pos.take_profit:
                    exit_price  = pos.take_profit
                    exit_reason = "TP"

            if exit_price is not None:
                # Apply slippage and commission
                if pos.direction == "BUY":
                    actual_exit = exit_price * (1 - config.slippage_pct)
                else:
                    actual_exit = exit_price * (1 + config.slippage_pct)

                cost = equity * (config.commission_pct * 2 + config.slippage_pct)
                if pos.direction == "BUY":
                    raw_pnl = (actual_exit - pos.entry_price) / pos.entry_price
                else:
                    raw_pnl = (pos.entry_price - actual_exit) / pos.entry_price

                pnl_pct = raw_pnl * 100
                pnl     = equity * raw_pnl - cost

                pos.exit_bar    = i
                pos.exit_price  = actual_exit
                pos.exit_reason = exit_reason
                pos.pnl         = round(pnl, 2)
                pos.pnl_pct     = round(pnl_pct, 2)
                pos.exit_time   = (
                    df.index[i].to_pydatetime() if hasattr(df.index[i], "to_pydatetime")
                    else None
                )

                equity     += pnl
                open_position = None

        # ── Update equity curve and drawdown ──────────────────────────────────
        equity_curve.append(round(equity, 2))
        if equity > peak_equity:
            peak_equity = equity
        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity * 100
            max_dd_pct = max(max_dd_pct, dd)

        # ── Skip signal generation if position is open ────────────────────────
        if open_position is not None:
            continue

        # ── Generate signal for this bar using preceding candles ──────────────
        window = df.iloc[max(0, i - config.lookback_candles) : i].copy()
        if len(window) < 10:
            continue

        try:
            signal = await generate_india_signal(
                symbol=symbol,
                timeframe=timeframe,
                candles_df=window,
                session=None,   # no DB writes during backtest
            )
        except Exception as exc:
            logger.debug(f"run_backtest {symbol} bar {i}: signal error — {exc}")
            continue

        if signal.action not in ("BUY", "SELL"):
            continue

        # ── Open new position ─────────────────────────────────────────────────
        entry_raw = bar_close
        entry     = entry_raw * (1 + config.slippage_pct if signal.action == "BUY"
                                 else -config.slippage_pct)

        atr_val = float(atr_series.iloc[i])
        if math.isnan(atr_val) or atr_val <= 0:
            continue

        risk = atr_val * config.atr_multiplier

        if signal.action == "BUY":
            sl = entry - risk
            tp = entry + risk * config.risk_reward
        else:
            sl = entry + risk
            tp = entry - risk * config.risk_reward

        entry_time = (
            df.index[i].to_pydatetime() if hasattr(df.index[i], "to_pydatetime")
            else None
        )
        trade = BacktestTrade(
            symbol=symbol,
            direction=signal.action,
            entry_bar=i,
            entry_price=round(entry, 4),
            exit_bar=None,
            exit_price=None,
            stop_loss=round(sl, 4),
            take_profit=round(tp, 4),
            pnl=0.0,
            pnl_pct=0.0,
            exit_reason="OPEN",
            entry_time=entry_time,
        )
        open_position = trade
        trades.append(trade)

    # ── Force-close any position still open at end ────────────────────────────
    if open_position is not None:
        last_close = float(df["close"].iloc[-1])
        open_position.exit_bar    = n - 1
        open_position.exit_price  = last_close
        open_position.exit_reason = "END"
        if open_position.direction == "BUY":
            raw_pnl = (last_close - open_position.entry_price) / open_position.entry_price
        else:
            raw_pnl = (open_position.entry_price - last_close) / open_position.entry_price
        open_position.pnl     = round(equity * raw_pnl, 2)
        open_position.pnl_pct = round(raw_pnl * 100, 2)
        equity += open_position.pnl

    # ── Compute summary statistics ─────────────────────────────────────────────
    closed = [t for t in trades if t.exit_reason != "OPEN"]
    winners = [t for t in closed if t.pnl > 0]
    losers  = [t for t in closed if t.pnl <= 0]

    total_pnl = sum(t.pnl for t in closed)
    total_return_pct = (equity - config.initial_capital) / config.initial_capital * 100

    win_rate   = len(winners) / len(closed) * 100 if closed else 0.0
    avg_win    = sum(t.pnl_pct for t in winners) / len(winners) if winners else 0.0
    avg_loss   = sum(t.pnl_pct for t in losers)  / len(losers)  if losers  else 0.0

    gross_profit = sum(t.pnl for t in winners)
    gross_loss   = abs(sum(t.pnl for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    # Sharpe: annualise daily equity-curve returns (assumes daily bars)
    sharpe: Optional[float] = None
    if len(equity_curve) > 30:
        eq = np.array(equity_curve)
        returns = np.diff(eq) / eq[:-1]
        returns = returns[np.isfinite(returns)]
        if returns.std() > 0:
            daily_rf = (1 + 0.065) ** (1 / 252) - 1
            sharpe = round(
                float((returns.mean() - daily_rf) / returns.std() * np.sqrt(252)), 2
            )

    logger.info(
        f"Backtest {symbol}/{timeframe}  trades={len(closed)}  "
        f"win={win_rate:.1f}%  pnl={total_pnl:+,.2f}  "
        f"return={total_return_pct:+.2f}%  maxDD={max_dd_pct:.2f}%  "
        f"sharpe={sharpe}"
    )

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        total_trades=len(closed),
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=round(win_rate, 2),
        total_pnl=round(total_pnl, 2),
        total_return_pct=round(total_return_pct, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        sharpe_ratio=sharpe,
        avg_win_pct=round(avg_win, 2),
        avg_loss_pct=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2) if profit_factor else None,
        trades=trades,
        equity_curve=equity_curve,
        config=config,
    )


async def run_backtest_all(
    symbols: list[str] | None = None,
    timeframe: str = "1d",
    config: Optional[BacktestConfig] = None,
    session: Optional[AsyncSession] = None,
) -> list[BacktestResult]:
    """Run backtests for all Indian watchlist symbols.

    Candles are fetched from the DB when a session is provided, or pulled
    from yfinance directly when session=None.  Results are sorted by
    total_return_pct descending.
    """
    if symbols is None:
        symbols = settings.nse_symbols + settings.nse_mid_symbols

    results: list[BacktestResult] = []

    for sym in symbols:
        df: Optional[pd.DataFrame] = None

        if session is not None:
            # Fetch from DB
            from crawler.price_feed import get_latest_candles
            rows = await get_latest_candles(sym, timeframe, 500, session)
            if rows and len(rows) >= 50:
                df = pd.DataFrame(
                    [{"open": r.open, "high": r.high, "low": r.low,
                      "close": r.close, "volume": r.volume, "timestamp": r.timestamp}
                     for r in reversed(rows)]
                ).set_index("timestamp")
        else:
            # Fetch via yfinance
            try:
                import yfinance as yf
                ticker = yf.Ticker(sym)
                df = ticker.history(period="2y", interval="1d")
                df.columns = [c.lower() for c in df.columns]
            except Exception as exc:
                logger.warning(f"run_backtest_all {sym}: yfinance fetch failed — {exc}")

        if df is None or len(df) < 50:
            logger.warning(f"run_backtest_all {sym}: insufficient data — skipping")
            continue

        result = await run_backtest(sym, df, config, timeframe)
        results.append(result)

    results.sort(key=lambda r: r.total_return_pct, reverse=True)
    return results
