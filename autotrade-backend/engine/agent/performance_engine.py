"""Agent performance & risk engine (Tier 1).

Grounds the agent in portfolio-management theory:
  - Beta (regression):  β_i = Cov(r_i, r_m) / Var(r_m)        — systematic risk
  - Portfolio beta:     β_p = Σ w_i · β_i
  - Sharpe:             (R_p − R_f) / σ_p                       — reward / total risk
  - Treynor:            (R_p − R_f) / β_p                       — reward / systematic risk
  - Jensen's α:         R_p − [R_f + β_p(R_m − R_f)]            — CAPM skill
  - Max drawdown, win rate, profit factor, expectancy          — trade quality

Formulas: "Investment Analysis and Portfolio Management" (TYBMS, Unit 4 —
Sharpe/Treynor/Jensen, CAPM) and CFA Research Foundation "Fundamentals of
Futures and Options" (regression hedge ratio / beta).

Data sources are the REAL tables the agent writes to: paper_trades (closed P&L),
open_positions (live book), candles (returns). No dependency on the empty
agent_trades / agent_performance tables.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PaperTrade, OpenPosition, Candle
from utils.config import settings
from utils.logger import logger

_TRADING_DAYS = 252
_BENCH = "^NSEI"   # NIFTY 50


# ── Returns helpers ───────────────────────────────────────────────────────────

def _daily_returns(closes: list[float]) -> list[float]:
    return [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def _annualized_return(daily: list[float]) -> float:
    return (sum(daily) / len(daily) * _TRADING_DAYS) if daily else 0.0


def _annualized_stddev(daily: list[float]) -> float:
    n = len(daily)
    if n < 2:
        return 0.0
    mean = sum(daily) / n
    var = sum((r - mean) ** 2 for r in daily) / (n - 1)
    return math.sqrt(var) * math.sqrt(_TRADING_DAYS)


# ── Regression beta ───────────────────────────────────────────────────────────

def _trading_date(ts: datetime) -> date:
    """Normalise a candle timestamp to its true trading date.

    The daily-candle pipeline applies an intraday IST→UTC shift that pushes some
    bars onto the weekend (e.g. a Friday bar stored as Sunday 18:30). Snap any
    weekend timestamp back to the preceding Friday so index and equity series —
    stored via different paths — align on the same trading days.
    """
    d = ts.date()
    wd = d.weekday()            # Mon=0 … Sun=6
    if wd == 5:                 # Saturday → Friday
        return d - timedelta(days=1)
    if wd == 6:                 # Sunday → Friday
        return d - timedelta(days=2)
    return d


async def _aligned_closes(symbol: str, days: int, session: AsyncSession) -> dict[date, float]:
    rows = (await session.execute(
        select(Candle.timestamp, Candle.close)
        .where(Candle.symbol == symbol, Candle.timeframe == "1d")
        .order_by(Candle.timestamp.desc()).limit(days + 10)
    )).all()
    # Newest-first; normalise to trading date and keep the first (latest) per date.
    out: dict[date, float] = {}
    for r in rows:
        td = _trading_date(r.timestamp)
        if td not in out:
            out[td] = float(r.close)
    return out


async def compute_symbol_beta(symbol: str, session: AsyncSession, days: int = 180) -> float | None:
    """Regression beta of a symbol vs NIFTY: Cov(r_i, r_m) / Var(r_m).

    Returns None when there isn't enough overlapping history.
    """
    sym = symbol if symbol.endswith((".NS", ".BO")) or symbol.startswith("^") else f"{symbol}.NS"
    stock = await _aligned_closes(sym, days, session)
    bench = await _aligned_closes(_BENCH, days, session)
    common = sorted(set(stock) & set(bench))
    if len(common) < 20:
        return None
    s = [stock[d] for d in common]
    m = [bench[d] for d in common]
    rs, rm = _daily_returns(s), _daily_returns(m)
    n = min(len(rs), len(rm))
    if n < 15:
        return None
    rs, rm = rs[-n:], rm[-n:]
    mean_s, mean_m = sum(rs) / n, sum(rm) / n
    cov = sum((rs[i] - mean_s) * (rm[i] - mean_m) for i in range(n)) / (n - 1)
    var_m = sum((rm[i] - mean_m) ** 2 for i in range(n)) / (n - 1)
    if var_m <= 0:
        return None
    return round(cov / var_m, 3)


async def compute_portfolio_beta(session: AsyncSession) -> tuple[float, dict[str, float]]:
    """Weighted-average regression beta over the live equity book.

    β_p = Σ (w_i · β_i). Missing betas default to 1.0 (market-like). Returns
    (portfolio_beta, {symbol: beta}).
    """
    positions = (await session.execute(
        select(OpenPosition).where(OpenPosition.instrument_type == "EQUITY")
    )).scalars().all()
    if not positions:
        # Fall back to any open positions (e.g. all F&O) → underlying betas
        positions = (await session.execute(select(OpenPosition))).scalars().all()
    if not positions:
        return 1.0, {}

    total = sum(float(p.size_usd or 0) for p in positions) or 1.0
    betas: dict[str, float] = {}
    bp = 0.0
    for p in positions:
        base = (p.underlying_symbol or p.symbol or "").replace(".NS", "").replace(".BO", "")
        if base not in betas:
            b = await compute_symbol_beta(base, session)
            betas[base] = b if b is not None else 1.0
        bp += (float(p.size_usd or 0) / total) * betas[base]
    return round(bp, 3), betas


# ── Benchmark return ──────────────────────────────────────────────────────────

async def benchmark_return(session: AsyncSession, days: int = 180) -> float:
    closes_map = await _aligned_closes(_BENCH, days, session)
    closes = [closes_map[d] for d in sorted(closes_map)]
    if len(closes) < 5:
        return 0.12  # fallback assumption
    return _annualized_return(_daily_returns(closes))


# ── Equity curve from closed trades ───────────────────────────────────────────

async def _equity_curve(session: AsyncSession) -> tuple[list[float], list[float]]:
    """Reconstruct a daily equity curve from realised P&L of closed paper_trades.

    Returns (equity_series, daily_returns). Starts at AGENT_EQUITY and steps by
    each day's realised P&L. Sparse but correct; grows as the agent closes trades.
    """
    rows = (await session.execute(
        select(PaperTrade.closed_at, PaperTrade.pnl)
        .where(PaperTrade.closed_at != None, PaperTrade.pnl != None)
        .order_by(PaperTrade.closed_at)
    )).all()
    start = settings.AGENT_EQUITY
    if not rows:
        return [start], []

    by_day: dict[date, float] = {}
    for r in rows:
        d = r.closed_at.date()
        by_day[d] = by_day.get(d, 0.0) + float(r.pnl)

    equity = [start]
    cur = start
    for d in sorted(by_day):
        cur += by_day[d]
        equity.append(cur)
    daily = [(equity[i] - equity[i - 1]) / equity[i - 1]
             for i in range(1, len(equity)) if equity[i - 1] > 0]
    return equity, daily


def _max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough decline as a positive percent."""
    peak = equity[0] if equity else 0.0
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return round(mdd * 100, 2)


# ── Closed-trade statistics ───────────────────────────────────────────────────

async def trade_stats(session: AsyncSession) -> dict:
    rows = (await session.execute(
        select(PaperTrade.pnl)
        .where(PaperTrade.closed_at != None, PaperTrade.pnl != None)
    )).all()
    pnls = [float(r.pnl) for r in rows]
    n = len(pnls)
    if n == 0:
        return {"closed_trades": 0, "win_rate": None, "profit_factor": None,
                "avg_win": None, "avg_loss": None, "expectancy": None}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    win_rate = len(wins) / n * 100
    avg_win = (gross_win / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else float("inf"))
    expectancy = sum(pnls) / n
    return {
        "closed_trades": n,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2) if pf not in (None, float("inf")) else pf,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
    }


# ── Full metrics bundle ───────────────────────────────────────────────────────

async def compute_metrics(session: AsyncSession) -> dict:
    """Sharpe / Treynor / Jensen + beta + drawdown + trade stats for the agent."""
    rf = settings.RISK_FREE_RATE   # decimal, e.g. 0.065

    equity, daily = await _equity_curve(session)
    rp = _annualized_return(daily)
    sigma = _annualized_stddev(daily)
    beta_p, betas = await compute_portfolio_beta(session)
    rm = await benchmark_return(session)

    sharpe = (rp - rf) / sigma if sigma > 0 else None
    treynor = (rp - rf) / beta_p if beta_p else None
    capm_expected = rf + beta_p * (rm - rf)
    jensen = rp - capm_expected
    mdd = _max_drawdown(equity)
    stats = await trade_stats(session)

    # Rate the agent so the UI/user gets a verdict, not just numbers.
    if stats["closed_trades"] < 10:
        verdict = "INSUFFICIENT_DATA"
    elif sharpe is None:
        verdict = "INSUFFICIENT_DATA"
    elif sharpe >= 1.0 and jensen > 0:
        verdict = "STRONG"          # genuine risk-adjusted skill
    elif sharpe >= 0.5:
        verdict = "DECENT"
    elif sharpe >= 0:
        verdict = "MARGINAL"
    else:
        verdict = "UNDERPERFORMING"

    return {
        "risk_free_rate":   round(rf * 100, 2),
        "portfolio_return": round(rp * 100, 2),
        "benchmark_return": round(rm * 100, 2),
        "portfolio_beta":   beta_p,
        "portfolio_stddev": round(sigma * 100, 2),
        "capm_expected":    round(capm_expected * 100, 2),
        "sharpe_ratio":     round(sharpe, 3) if sharpe is not None else None,
        "treynor_ratio":    round(treynor, 3) if treynor is not None else None,
        "jensens_alpha":    round(jensen * 100, 2),
        "max_drawdown":     mdd,
        "symbol_betas":     betas,
        "equity_points":    len(equity),
        "verdict":          verdict,
        **stats,
    }
