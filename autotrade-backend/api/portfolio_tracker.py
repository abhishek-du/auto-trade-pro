"""Personal Portfolio Tracker API — /api/v1/portfolios endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import TrackerHolding, TrackerPortfolio, TrackerTransaction
from engine.portfolio_service import (
    NSE_STOCK_LOOKUP,
    _holding_to_dict,
    _portfolio_to_dict,
    _tx_to_dict,
    add_or_update_holding,
    calculate_portfolio_summary,
    calculate_tax_liability,
    search_stocks,
    sell_holding,
)

router = APIRouter(tags=["Portfolio Tracker"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreatePortfolioRequest(BaseModel):
    name: str
    description: Optional[str] = None


class UpdatePortfolioRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class AddHoldingRequest(BaseModel):
    symbol: str
    quantity: float
    price: float
    trade_date: date
    notes: Optional[str] = ""


class SellHoldingRequest(BaseModel):
    quantity: float
    price: float
    trade_date: date
    notes: Optional[str] = ""


# ── Portfolio CRUD ────────────────────────────────────────────────────────────

@router.get("/")
async def list_portfolios(db: AsyncSession = Depends(get_db)):
    """List all portfolios with basic P&L summary."""
    res = await db.execute(
        select(TrackerPortfolio).order_by(TrackerPortfolio.created_at)
    )
    portfolios = list(res.scalars().all())
    out = []
    for p in portfolios:
        summary = await calculate_portfolio_summary(p.id, db)
        out.append({
            **_portfolio_to_dict(p),
            "summary": summary["summary"] if summary else {},
        })
    return out


@router.post("/", status_code=201)
async def create_portfolio(body: CreatePortfolioRequest, db: AsyncSession = Depends(get_db)):
    portfolio = TrackerPortfolio(name=body.name, description=body.description)
    db.add(portfolio)
    await db.commit()
    await db.refresh(portfolio)
    return _portfolio_to_dict(portfolio)


@router.get("/{portfolio_id}")
async def get_portfolio(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    """Full portfolio detail: holdings with live P&L, allocation, tax."""
    data = await calculate_portfolio_summary(portfolio_id, db)
    if data is None:
        raise HTTPException(404, "Portfolio not found")
    return data


@router.put("/{portfolio_id}")
async def update_portfolio(
    portfolio_id: str,
    body: UpdatePortfolioRequest,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(TrackerPortfolio).where(TrackerPortfolio.id == portfolio_id)
    )
    portfolio = res.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")
    if body.name is not None:
        portfolio.name = body.name
    if body.description is not None:
        portfolio.description = body.description
    await db.commit()
    await db.refresh(portfolio)
    return _portfolio_to_dict(portfolio)


@router.delete("/{portfolio_id}", status_code=204)
async def delete_portfolio(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(TrackerPortfolio).where(TrackerPortfolio.id == portfolio_id)
    )
    portfolio = res.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")
    await db.delete(portfolio)
    await db.commit()


# ── Holdings ──────────────────────────────────────────────────────────────────

@router.get("/{portfolio_id}/holdings")
async def list_holdings(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(TrackerHolding)
        .where(TrackerHolding.portfolio_id == portfolio_id)
        .order_by(TrackerHolding.created_at)
    )
    return [_holding_to_dict(h) for h in res.scalars().all()]


@router.post("/{portfolio_id}/holdings", status_code=201)
async def add_holding(
    portfolio_id: str,
    body: AddHoldingRequest,
    db: AsyncSession = Depends(get_db),
):
    # Verify portfolio exists
    res = await db.execute(
        select(TrackerPortfolio).where(TrackerPortfolio.id == portfolio_id)
    )
    if not res.scalar_one_or_none():
        raise HTTPException(404, "Portfolio not found")
    if body.quantity <= 0 or body.price <= 0:
        raise HTTPException(400, "Quantity and price must be positive")
    return await add_or_update_holding(
        portfolio_id, body.symbol, body.quantity, body.price,
        body.trade_date, body.notes or "", db,
    )


@router.delete("/{portfolio_id}/holdings/{holding_id}", status_code=204)
async def delete_holding(
    portfolio_id: str,
    holding_id: str,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(TrackerHolding).where(
            TrackerHolding.id == holding_id,
            TrackerHolding.portfolio_id == portfolio_id,
        )
    )
    holding = res.scalar_one_or_none()
    if not holding:
        raise HTTPException(404, "Holding not found")
    await db.delete(holding)
    await db.commit()


@router.post("/{portfolio_id}/holdings/{holding_id}/sell")
async def sell(
    portfolio_id: str,
    holding_id: str,
    body: SellHoldingRequest,
    db: AsyncSession = Depends(get_db),
):
    if body.quantity <= 0 or body.price <= 0:
        raise HTTPException(400, "Quantity and price must be positive")
    try:
        return await sell_holding(holding_id, body.quantity, body.price, body.trade_date, body.notes or "", db)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Transactions ──────────────────────────────────────────────────────────────

@router.get("/{portfolio_id}/transactions")
async def list_transactions(
    portfolio_id: str,
    symbol: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(TrackerTransaction)
        .where(TrackerTransaction.portfolio_id == portfolio_id)
        .order_by(TrackerTransaction.trade_date.desc())
    )
    if symbol:
        q = q.where(TrackerTransaction.symbol == symbol)
    res = await db.execute(q)
    return [_tx_to_dict(tx) for tx in res.scalars().all()]


@router.delete("/{portfolio_id}/transactions/{tx_id}", status_code=204)
async def delete_transaction(
    portfolio_id: str,
    tx_id: str,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(TrackerTransaction).where(
            TrackerTransaction.id == tx_id,
            TrackerTransaction.portfolio_id == portfolio_id,
        )
    )
    tx = res.scalar_one_or_none()
    if not tx:
        raise HTTPException(404, "Transaction not found")
    await db.delete(tx)
    await db.commit()


# ── Tax summary ───────────────────────────────────────────────────────────────

@router.get("/{portfolio_id}/tax")
async def get_tax_summary(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(TrackerTransaction)
        .where(TrackerTransaction.portfolio_id == portfolio_id)
        .order_by(TrackerTransaction.trade_date)
    )
    txns = list(res.scalars().all())
    return calculate_tax_liability(txns, date.today())


# ── Stock search ──────────────────────────────────────────────────────────────

@router.get("/search/stocks")
async def search_stocks_api(q: str = Query(..., min_length=1)):
    return search_stocks(q)
