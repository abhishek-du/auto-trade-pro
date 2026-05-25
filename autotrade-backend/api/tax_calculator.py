"""Indian Tax P&L Calculator API — /api/v1/tax endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import TrackerHolding, TrackerTransaction
from engine.portfolio_service import get_prices_batch
from engine.tax_engine import (
    TaxableTrade,
    build_tax_trades_from_transactions,
    calculate_tax_summary,
    classify_trade,
    find_harvesting_opportunities,
    get_slab_rate,
    _get_fy,
    _parse_fy,
    LTCG_EXEMPTION,
)
from utils.logger import logger

router = APIRouter(tags=["Tax Calculator"])

CURRENT_FY = "FY2025-26"


# ── Schemas ───────────────────────────────────────────────────────────────────

class ManualTradeInput(BaseModel):
    symbol:       str
    company_name: str = ""
    asset_type:   str = "EQUITY"
    buy_date:     date
    sell_date:    date
    buy_price:    float
    sell_price:   float
    quantity:     float


class TaxCalculatorManualRequest(BaseModel):
    trades:             list[ManualTradeInput]
    financial_year:     str   = CURRENT_FY
    annual_income:      float = 1_000_000
    already_used_ltcg:  float = 0.0


# ── Portfolio-based endpoints ─────────────────────────────────────────────────

@router.get("/summary/{portfolio_id}")
async def get_tax_summary(
    portfolio_id:      str,
    financial_year:    str   = Query(CURRENT_FY),
    annual_income:     float = Query(1_000_000),
    already_used_ltcg: float = Query(0.0),
    db: AsyncSession = Depends(get_db),
):
    """Full tax summary for a portfolio in a given FY."""
    try:
        trades  = await build_tax_trades_from_transactions(portfolio_id, financial_year, db)
        summary = calculate_tax_summary(trades, financial_year, annual_income, already_used_ltcg)
        return summary.to_dict()
    except Exception as exc:
        logger.warning(f"[tax] summary error: {exc}")
        raise HTTPException(500, str(exc))


@router.get("/breakdown/{portfolio_id}")
async def get_trade_breakdown(
    portfolio_id:   str,
    financial_year: str = Query(CURRENT_FY),
    db: AsyncSession = Depends(get_db),
):
    """Trade-by-trade breakdown for a given FY."""
    trades = await build_tax_trades_from_transactions(portfolio_id, financial_year, db)

    stcg = ltcg = debt_slab = total_gains = total_losses = 0.0
    for t in trades:
        if t.gross_gain >= 0:
            total_gains += t.gross_gain
            if t.gain_type == "STCG":   stcg += t.gross_gain
            elif t.gain_type == "LTCG": ltcg += t.gross_gain
            else:                       debt_slab += t.gross_gain
        else:
            total_losses += abs(t.gross_gain)

    return {
        "trades": [t.to_dict() for t in trades],
        "totals": {
            "stcg":         round(stcg, 2),
            "ltcg":         round(ltcg, 2),
            "debt_slab":    round(debt_slab, 2),
            "total_gains":  round(total_gains, 2),
            "total_losses": round(total_losses, 2),
        },
    }


@router.get("/harvesting/{portfolio_id}")
async def get_harvesting_opportunities(
    portfolio_id:   str,
    financial_year: str   = Query(CURRENT_FY),
    annual_income:  float = Query(1_000_000),
    db: AsyncSession = Depends(get_db),
):
    """Tax harvesting opportunities based on open holdings and current prices."""
    # Get open holdings
    res = await db.execute(
        select(TrackerHolding).where(TrackerHolding.portfolio_id == portfolio_id)
    )
    holdings = list(res.scalars().all())
    if not holdings:
        return {
            "loss_harvest": [], "gain_harvest": [], "timing_suggestions": [],
            "summary": {
                "loss_harvest_count": 0, "gain_harvest_count": 0,
                "timing_count": 0, "total_tax_saveable": 0.0,
                "ltcg_exemption_remaining": LTCG_EXEMPTION,
            },
        }

    # Current prices
    symbols = [h.symbol for h in holdings]
    prices  = get_prices_batch(symbols)

    # Realised STCG/LTCG so far this FY
    trades  = await build_tax_trades_from_transactions(portfolio_id, financial_year, db)
    summary = calculate_tax_summary(trades, financial_year, annual_income)

    holdings_dicts = [
        {
            "symbol":         h.symbol,
            "company_name":   h.company_name or h.symbol.replace(".NS", ""),
            "avg_buy_price":  h.avg_buy_price,
            "quantity":       h.quantity,
            "first_buy_date": h.first_buy_date.isoformat() if h.first_buy_date else None,
        }
        for h in holdings
    ]

    prices_clean = {sym: prices[sym] for sym in prices if prices[sym]}

    return find_harvesting_opportunities(
        open_holdings=holdings_dicts,
        current_prices=prices_clean,
        existing_stcg=max(0.0, summary.stcg_equity_net),
        existing_ltcg=summary.ltcg_equity_gains,
        ltcg_exemption_remaining=summary.ltcg_exempt_remaining,
    )


@router.get("/financial-years/{portfolio_id}")
async def get_financial_years(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return all FYs that have SELL transactions in this portfolio."""
    res = await db.execute(
        select(TrackerTransaction.trade_date)
        .where(
            TrackerTransaction.portfolio_id == portfolio_id,
            TrackerTransaction.tx_type == "SELL",
        )
        .order_by(TrackerTransaction.trade_date)
    )
    dates = [r[0] for r in res.all()]
    if not dates:
        return [CURRENT_FY]
    fys = sorted(set(_get_fy(d) for d in dates), reverse=True)
    if CURRENT_FY not in fys:
        fys.insert(0, CURRENT_FY)
    return fys


@router.get("/current-fy-status/{portfolio_id}")
async def get_current_fy_status(
    portfolio_id: str,
    annual_income: float = Query(1_000_000),
    db: AsyncSession = Depends(get_db),
):
    """Quick status for the current FY — realized gains, tax so far, exemption."""
    today = date.today()
    fy    = _get_fy(today)
    _, fy_end = _parse_fy(fy)
    days_left = (fy_end - today).days

    trades  = await build_tax_trades_from_transactions(portfolio_id, fy, db)
    summary = calculate_tax_summary(trades, fy, annual_income)

    return {
        "financial_year":         fy,
        "realized_stcg":          summary.stcg_equity_gains,
        "realized_ltcg":          summary.ltcg_equity_gains,
        "realized_losses":        summary.stcg_equity_losses + summary.ltcg_equity_losses,
        "ltcg_exemption_used":    summary.ltcg_exempt_used,
        "ltcg_exemption_remaining": summary.ltcg_exempt_remaining,
        "estimated_tax_so_far":   summary.total_tax,
        "days_left_in_fy":        days_left,
    }


# ── Manual (standalone) calculator endpoints ──────────────────────────────────

@router.post("/calculate")
async def calculate_manual(body: TaxCalculatorManualRequest):
    """Calculate tax from manually entered trades (no portfolio needed)."""
    trades = [
        classify_trade(
            symbol=t.symbol,
            company_name=t.company_name or t.symbol,
            asset_type=t.asset_type,
            buy_date=t.buy_date,
            sell_date=t.sell_date,
            buy_price=t.buy_price,
            sell_price=t.sell_price,
            quantity=t.quantity,
        )
        for t in body.trades
    ]
    summary = calculate_tax_summary(
        trades,
        body.financial_year,
        body.annual_income,
        body.already_used_ltcg,
    )
    return summary.to_dict()


@router.post("/classify-trade")
async def classify_single_trade(body: ManualTradeInput):
    """Classify a single trade — returns gain type and rate immediately."""
    trade = classify_trade(
        symbol=body.symbol,
        company_name=body.company_name or body.symbol,
        asset_type=body.asset_type,
        buy_date=body.buy_date,
        sell_date=body.sell_date,
        buy_price=body.buy_price,
        sell_price=body.sell_price,
        quantity=body.quantity,
    )
    return trade.to_dict()


@router.get("/slab-rate")
async def get_slab_rate_api(annual_income: float = Query(...)):
    """Return the applicable slab rate for a given annual income."""
    rate = get_slab_rate(annual_income)
    return {"annual_income": annual_income, "slab_rate": rate, "slab_rate_pct": round(rate * 100, 0)}
