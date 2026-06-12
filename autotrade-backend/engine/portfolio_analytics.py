"""Portfolio capital model analytics.

Implements:
  - Position weight computation (symbol + sector)
  - Adjusted confidence score:  adjusted = confidence × (1 - current_weight / max_weight)
  - Sharpe Ratio  = (Rp - Rf) / σp          (reward-to-variability)
  - Treynor Ratio = (Rp - Rf) / β            (reward-to-systematic-risk)
  - Jensen's Alpha = Rp - [Rf + β(Rm - Rf)]  (CAPM differential)

All metrics are annualized (daily data × √252 for std-dev, × 252 for returns).
Risk-free rate defaults to India 10Y G-Sec ≈ 7.1% p.a.

Formulas sourced from:
  "Investment Analysis and Portfolio Management" TYBMS 2016-17,
  Chapter 10 — Portfolio Performance Measurement (Sharpe p.108, Treynor p.108,
  Jensen p.109, CAPM Chapter 9).
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AgentCapitalSnapshot,
    AgentPerformance,
    AgentPosition,
    Candle,
    FundamentalData,
    PortfolioPolicy,
    VirtualWallet,
)
from utils.logger import logger


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_policy(session: AsyncSession) -> PortfolioPolicy:
    row = (await session.execute(select(PortfolioPolicy).limit(1))).scalar_one_or_none()
    if row is None:
        row = PortfolioPolicy()
        session.add(row)
        await session.flush()
    return row


def _annualize_return(daily_returns: list[float]) -> float:
    if not daily_returns:
        return 0.0
    mean_daily = sum(daily_returns) / len(daily_returns)
    return mean_daily * 252


def _annualized_stddev(daily_returns: list[float]) -> float:
    n = len(daily_returns)
    if n < 2:
        return 0.0
    mean = sum(daily_returns) / n
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(252)


# ── Position weight utilities ─────────────────────────────────────────────────

async def get_position_weights(
    session: AsyncSession,
    is_paper: bool = True,
) -> dict[str, float]:
    """Return {symbol: weight_pct} for all open agent positions."""
    rows = (await session.execute(
        select(AgentPosition).where(AgentPosition.is_paper == is_paper)
    )).scalars().all()

    if not rows:
        return {}

    total_value = sum(
        (p.current_price or p.entry_price) * p.qty for p in rows
    )
    if total_value <= 0:
        return {}

    return {
        p.symbol: round(
            (p.current_price or p.entry_price) * p.qty / total_value * 100, 2
        )
        for p in rows
    }


async def get_sector_weights(
    session: AsyncSession,
    position_weights: dict[str, float] | None = None,
    is_paper: bool = True,
) -> dict[str, float]:
    """Return {sector: weight_pct} aggregated from position_weights."""
    from engine.india_specific import SECTOR_MAP

    if position_weights is None:
        position_weights = await get_position_weights(session, is_paper)

    sector_w: dict[str, float] = {}
    for symbol, w in position_weights.items():
        base = symbol.replace(".NS", "")
        sector = SECTOR_MAP.get(base, "Other")
        sector_w[sector] = round(sector_w.get(sector, 0.0) + w, 2)
    return sector_w


def compute_adjusted_score(
    confidence: float,
    current_weight_pct: float,
    max_weight_pct: float,
) -> float:
    """Scale confidence down proportional to how much of the cap is already used.

    adjusted = confidence × (1 − current_weight / max_weight)

    At 0% weight → full confidence.
    At 100% of cap → score = 0 (position fully capped).
    Above cap → negative → always skipped by trade loop.
    """
    if max_weight_pct <= 0:
        return 0.0
    factor = 1.0 - (current_weight_pct / max_weight_pct)
    return confidence * factor


# ── Portfolio beta ─────────────────────────────────────────────────────────────

async def compute_portfolio_beta(
    session: AsyncSession,
    position_weights: dict[str, float],
) -> float:
    """Weighted-average beta of the portfolio using FundamentalData.

    Beta = Σ(weight_i × beta_i).  Missing betas default to 1.0 (market-neutral).
    """
    if not position_weights:
        return 1.0

    symbols = list(position_weights.keys())
    rows = (await session.execute(
        select(FundamentalData.symbol, FundamentalData.pe_ratio)
        .where(FundamentalData.symbol.in_(symbols))
    )).all()

    # yfinance stores beta in the INFO_CACHE not in FundamentalData (which has PE/ROE).
    # Fall back to SECTOR_MAP implied beta if not available.
    # We'll try to get beta from the live price info cache.
    beta_map: dict[str, float] = {}
    try:
        from crawler.live_prices import INFO_CACHE
        for sym in symbols:
            info = INFO_CACHE.get(sym) or INFO_CACHE.get(sym.replace(".NS", ""))
            if info and isinstance(info, dict):
                b = info.get("beta")
                if b and isinstance(b, (int, float)) and 0.1 < b < 5.0:
                    beta_map[sym] = float(b)
    except Exception:
        pass

    total_w = sum(position_weights.values())
    if total_w <= 0:
        return 1.0

    portfolio_beta = 0.0
    for sym, w in position_weights.items():
        beta = beta_map.get(sym, 1.0)
        portfolio_beta += (w / total_w) * beta

    return round(portfolio_beta, 4)


# ── NIFTY benchmark return ────────────────────────────────────────────────────

async def get_nifty_return(
    session: AsyncSession,
    days: int = 252,
) -> float | None:
    """Annualized NIFTY 50 return over the last `days` trading days from candles."""
    try:
        rows = (await session.execute(
            select(Candle.close, Candle.timestamp)
            .where(
                Candle.symbol.in_(["^NSEI", "NIFTY50.NS", "NIFTY_50"]),
                Candle.timeframe == "1d",
            )
            .order_by(Candle.timestamp.desc())
            .limit(days + 1)
        )).all()

        if len(rows) < 2:
            return None

        closes = [r.close for r in reversed(rows)]
        daily_returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
        ]
        return _annualize_return(daily_returns)
    except Exception as exc:
        logger.debug(f"[portfolio_analytics] nifty return failed: {exc}")
        return None


# ── Core performance metrics ──────────────────────────────────────────────────

async def compute_performance_metrics(
    session: AsyncSession,
    days: int = 90,
    risk_free_rate_pct: float | None = None,
) -> dict:
    """Compute Sharpe, Treynor, and Jensen's Alpha for the paper portfolio.

    Returns dict with keys:
      portfolio_return, benchmark_return, portfolio_beta, portfolio_stddev,
      sharpe_ratio, treynor_ratio, jensens_alpha, daily_returns (list)
    """
    policy = await _get_policy(session)
    rf_pct = risk_free_rate_pct if risk_free_rate_pct is not None else policy.risk_free_rate
    rf = rf_pct / 100.0  # convert to decimal

    # ── Daily returns from AgentPerformance ──────────────────────────────────
    since = date.today() - timedelta(days=days)
    perf_rows = (await session.execute(
        select(AgentPerformance)
        .where(
            AgentPerformance.date >= since,
            AgentPerformance.is_paper == True,
        )
        .order_by(AgentPerformance.date)
    )).scalars().all()

    if len(perf_rows) < 5:
        # Not enough history yet
        return {
            "portfolio_return": None,
            "benchmark_return": None,
            "portfolio_beta": None,
            "portfolio_stddev": None,
            "sharpe_ratio": None,
            "treynor_ratio": None,
            "jensens_alpha": None,
            "daily_returns": [],
            "risk_free_rate": rf_pct,
            "days_analyzed": len(perf_rows),
        }

    # Use net_pnl / equity_end as daily return
    daily_returns = []
    for row in perf_rows:
        if row.equity_end and row.equity_end > 0:
            daily_r = row.net_pnl / row.equity_end
            daily_returns.append(daily_r)

    if not daily_returns:
        return {
            "portfolio_return": None,
            "benchmark_return": None,
            "portfolio_beta": None,
            "portfolio_stddev": None,
            "sharpe_ratio": None,
            "treynor_ratio": None,
            "jensens_alpha": None,
            "daily_returns": [],
            "risk_free_rate": rf_pct,
            "days_analyzed": len(perf_rows),
        }

    rp = _annualize_return(daily_returns)           # portfolio annualized return
    sigma = _annualized_stddev(daily_returns)        # annualized std dev

    # ── Portfolio beta (from current open positions) ──────────────────────────
    pos_weights = await get_position_weights(session)
    beta = await compute_portfolio_beta(session, pos_weights)

    # ── NIFTY benchmark ───────────────────────────────────────────────────────
    rm = await get_nifty_return(session, days=days)
    if rm is None:
        rm = 0.12  # fallback: assume 12% p.a. for NIFTY

    # ── Sharpe Ratio: (Rp - Rf) / σp ─────────────────────────────────────────
    sharpe = (rp - rf) / sigma if sigma > 0 else None

    # ── Treynor Ratio: (Rp - Rf) / β ─────────────────────────────────────────
    treynor = (rp - rf) / beta if beta and beta > 0 else None

    # ── Jensen's Alpha: Rp - [Rf + β(Rm - Rf)] ───────────────────────────────
    capm_expected = rf + beta * (rm - rf)
    jensens = rp - capm_expected

    return {
        "portfolio_return":  round(rp * 100, 4),
        "benchmark_return":  round(rm * 100, 4),
        "portfolio_beta":    round(beta, 4),
        "portfolio_stddev":  round(sigma * 100, 4),
        "sharpe_ratio":      round(sharpe, 4) if sharpe is not None else None,
        "treynor_ratio":     round(treynor, 4) if treynor is not None else None,
        "jensens_alpha":     round(jensens * 100, 4),
        "daily_returns":     daily_returns,
        "risk_free_rate":    rf_pct,
        "days_analyzed":     len(daily_returns),
        "capm_expected_return": round(capm_expected * 100, 4),
    }


# ── Daily capital snapshot ────────────────────────────────────────────────────

async def save_capital_snapshot(session: AsyncSession) -> AgentCapitalSnapshot | None:
    """Compute and upsert today's capital model snapshot.

    Called nightly by the performance task.
    """
    today = date.today()

    try:
        metrics = await compute_performance_metrics(session)
        wallet = await VirtualWallet.get_summary(session)
        pos_weights = await get_position_weights(session)
        sector_weights = await get_sector_weights(session, pos_weights)
        policy = await _get_policy(session)

        equity = wallet.get("equity", 0.0)
        cash = wallet.get("balance", 0.0)
        cash_pct = (cash / equity * 100) if equity > 0 else 100.0

        # Check if any sector exceeds its threshold (rebalance trigger)
        rebalance_needed = any(
            w > policy.max_sector_weight for w in sector_weights.values()
        ) or any(
            w > policy.max_single_stock_weight for w in pos_weights.values()
        )

        # Upsert
        existing = (await session.execute(
            select(AgentCapitalSnapshot)
            .where(AgentCapitalSnapshot.snapshot_date == today)
        )).scalar_one_or_none()

        if existing:
            snap = existing
        else:
            snap = AgentCapitalSnapshot(
                id=str(uuid.uuid4()),
                snapshot_date=today,
            )
            session.add(snap)

        snap.equity           = equity
        snap.cash             = cash
        snap.cash_pct         = round(cash_pct, 2)
        snap.num_positions    = len(pos_weights)
        snap.portfolio_return = metrics.get("portfolio_return")
        snap.benchmark_return = metrics.get("benchmark_return")
        snap.portfolio_beta   = metrics.get("portfolio_beta")
        snap.portfolio_stddev = metrics.get("portfolio_stddev")
        snap.sharpe_ratio     = metrics.get("sharpe_ratio")
        snap.treynor_ratio    = metrics.get("treynor_ratio")
        snap.jensens_alpha    = metrics.get("jensens_alpha")
        snap.sector_weights   = sector_weights
        snap.position_weights = pos_weights
        snap.rebalance_needed = rebalance_needed

        await session.flush()
        logger.info(
            f"[portfolio_analytics] snapshot {today}  "
            f"sharpe={snap.sharpe_ratio}  "
            f"treynor={snap.treynor_ratio}  "
            f"alpha={snap.jensens_alpha}  "
            f"rebalance={rebalance_needed}"
        )
        return snap

    except Exception as exc:
        logger.warning(f"[portfolio_analytics] snapshot failed: {exc}")
        return None


# ── Rebalancing logic ─────────────────────────────────────────────────────────

async def compute_rebalance_trades(
    session: AsyncSession,
    top_n: int = 10,
) -> list[dict]:
    """Generate rebalancing signals: equal-weight top-10 Hub BUY signals.

    Returns list of {"symbol", "action", "reason", "current_weight", "target_weight"}.
    BUY  → underweight vs target.
    SELL → overweight vs target.
    """
    from db.models import MasterIntelligenceScore
    from sqlalchemy import func as _func

    policy = await _get_policy(session)
    pos_weights = await get_position_weights(session)

    # Get top-N current Hub BUY signals
    _latest_subq = (
        select(
            MasterIntelligenceScore.symbol.label("sym"),
            _func.max(MasterIntelligenceScore.scored_at).label("max_at"),
        )
        .where(MasterIntelligenceScore.symbol.like("%.NS"))
        .group_by(MasterIntelligenceScore.symbol)
    ).subquery()

    top_rows = (await session.execute(
        select(
            MasterIntelligenceScore.symbol,
            MasterIntelligenceScore.master_score,
        )
        .join(
            _latest_subq,
            (MasterIntelligenceScore.symbol == _latest_subq.c.sym)
            & (MasterIntelligenceScore.scored_at == _latest_subq.c.max_at),
        )
        .where(
            MasterIntelligenceScore.is_blocked == False,
            MasterIntelligenceScore.signal.in_(["BUY", "STRONG_BUY"]),
        )
        .order_by(MasterIntelligenceScore.master_score.desc())
        .limit(top_n)
    )).all()

    if not top_rows:
        return []

    target_weight = round(100.0 / len(top_rows), 2)
    target_symbols = {r.symbol for r in top_rows}

    trades = []

    # Symbols to sell: currently held but NOT in top-N Hub BUY
    for sym, current_w in pos_weights.items():
        if sym not in target_symbols:
            trades.append({
                "symbol": sym,
                "action": "SELL",
                "reason": "Not in top Hub BUY universe — exit to rebalance",
                "current_weight": current_w,
                "target_weight": 0.0,
                "drift": round(current_w, 2),
            })

    # Symbols to buy: in top-N Hub BUY, underweight vs target
    for row in top_rows:
        current_w = pos_weights.get(row.symbol, 0.0)
        drift = abs(current_w - target_weight)
        if drift >= policy.rebalance_threshold:
            action = "BUY" if current_w < target_weight else "SELL"
            trades.append({
                "symbol": row.symbol,
                "action": action,
                "reason": (
                    f"Rebalance to equal-weight {target_weight:.1f}% "
                    f"(current {current_w:.1f}%, drift {drift:.1f}%)"
                ),
                "current_weight": current_w,
                "target_weight": target_weight,
                "drift": round(drift, 2),
                "hub_score": float(row.master_score),
            })

    return sorted(trades, key=lambda t: t["drift"], reverse=True)
