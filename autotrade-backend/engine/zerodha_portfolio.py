"""Zerodha KiteConnect v3 — real portfolio sync.

Syncs actual Demat holdings and open positions from Zerodha into the
AutoTrade Pro DB for display alongside paper trades.

PAPER TRADING ONLY — no orders are placed here.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.zerodha_client import get_kite_client
from db.models import PortfolioHolding, ZerodhaPosition
from utils.logger import logger


# ── Holdings sync ─────────────────────────────────────────────────────────────

async def sync_zerodha_holdings(session: AsyncSession) -> dict:
    """Fetch actual Demat holdings from Zerodha and upsert into portfolio_holdings.

    Uses portfolio_name marker in `sector` field = "Zerodha Demat" so we can
    distinguish these from manually-added holdings.

    Returns summary with total_value, total_pnl, holdings_count.
    """
    kite = get_kite_client()
    if not kite.access_token:
        raise RuntimeError("No active Zerodha session")

    raw: list[dict] = await kite.get_holdings()
    logger.info(f"[zerodha_portfolio] Fetched {len(raw)} holdings from Zerodha")

    synced_at = datetime.datetime.utcnow()
    total_value  = 0.0
    total_invested = 0.0
    total_pnl    = 0.0

    for h in raw:
        sym  = str(h.get("tradingsymbol", ""))
        exch = str(h.get("exchange", "NSE"))

        result = await session.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.tradingsymbol == sym,
                PortfolioHolding.exchange == exch,
            )
        )
        holding = result.scalar_one_or_none()

        qty      = int(h.get("quantity", 0))
        avg_prc  = float(h.get("average_price", 0.0))
        ltp      = float(h.get("last_price", 0.0))
        cur_val  = qty * ltp
        cost     = qty * avg_prc
        pnl      = float(h.get("pnl", cur_val - cost))
        pnl_pct  = ((ltp - avg_prc) / avg_prc * 100) if avg_prc else 0.0
        day_chg  = float(h.get("day_change", 0.0))
        day_pct  = float(h.get("day_change_percentage", 0.0))

        if holding is None:
            holding = PortfolioHolding(
                tradingsymbol = sym,
                exchange      = exch,
                isin          = h.get("isin"),
                sector        = "Zerodha Demat",
            )
            session.add(holding)

        holding.quantity      = qty
        holding.avg_price     = avg_prc
        holding.last_price    = ltp
        holding.current_value = cur_val
        holding.pnl           = pnl
        holding.pnl_pct       = round(pnl_pct, 4)
        holding.day_change    = day_chg
        holding.day_change_pct = round(day_pct, 4)
        holding.synced_at     = synced_at

        total_value    += cur_val
        total_invested += cost
        total_pnl      += pnl

    await session.flush()

    return {
        "holdings_count": len(raw),
        "total_value":    round(total_value, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl":      round(total_pnl, 2),
        "total_pnl_pct":  round(total_pnl / total_invested * 100, 4) if total_invested else 0.0,
        "synced_at":      synced_at.isoformat(),
    }


# ── Positions sync ────────────────────────────────────────────────────────────

async def sync_zerodha_positions(session: AsyncSession) -> dict:
    """Fetch intraday + overnight positions from Zerodha and upsert into zerodha_positions.

    Kite returns two lists: 'day' (intraday M2M) and 'net' (overnight).
    """
    kite = get_kite_client()
    if not kite.access_token:
        raise RuntimeError("No active Zerodha session")

    raw = await kite.get_positions()
    day_positions = raw.get("day", [])
    net_positions = raw.get("net", [])
    logger.info(
        f"[zerodha_portfolio] Positions: day={len(day_positions)} net={len(net_positions)}"
    )

    synced_at = datetime.datetime.utcnow()

    async def _upsert(positions: list[dict], position_type: str) -> None:
        for p in positions:
            sym     = str(p.get("tradingsymbol", ""))
            exch    = str(p.get("exchange", "NSE"))
            product = str(p.get("product", "CNC"))

            result = await session.execute(
                select(ZerodhaPosition).where(
                    ZerodhaPosition.tradingsymbol  == sym,
                    ZerodhaPosition.exchange       == exch,
                    ZerodhaPosition.product        == product,
                    ZerodhaPosition.position_type  == position_type,
                )
            )
            pos = result.scalar_one_or_none()
            if pos is None:
                pos = ZerodhaPosition(
                    tradingsymbol = sym,
                    exchange      = exch,
                    product       = product,
                    position_type = position_type,
                )
                session.add(pos)

            pos.quantity      = int(p.get("quantity", 0))
            pos.buy_quantity  = int(p.get("buy_quantity", 0))
            pos.sell_quantity = int(p.get("sell_quantity", 0))
            pos.average_price = float(p.get("average_price", 0.0))
            pos.last_price    = float(p.get("last_price", 0.0))
            pos.pnl           = float(p.get("pnl", 0.0))
            pos.m2m           = float(p.get("m2m", 0.0))
            pos.value         = float(p.get("value", 0.0))
            pos.multiplier    = float(p.get("multiplier", 1.0))
            pos.synced_at     = synced_at

    await _upsert(day_positions, "day")
    await _upsert(net_positions, "net")
    await session.flush()

    day_pnl = sum(float(p.get("pnl", 0)) for p in day_positions)
    net_pnl = sum(float(p.get("pnl", 0)) for p in net_positions)

    return {
        "day_positions_count": len(day_positions),
        "net_positions_count": len(net_positions),
        "day_pnl":             round(day_pnl, 2),
        "net_pnl":             round(net_pnl, 2),
        "synced_at":           synced_at.isoformat(),
    }


# ── P&L summary ───────────────────────────────────────────────────────────────

async def get_zerodha_pnl_summary(session: AsyncSession) -> dict:
    """Combine holdings P&L + today's positions P&L + available cash."""
    kite = get_kite_client()
    if not kite.access_token:
        raise RuntimeError("No active Zerodha session")

    # Holdings totals from DB
    result = await session.execute(
        select(PortfolioHolding).where(PortfolioHolding.quantity > 0)
    )
    holdings = result.scalars().all()
    demat_value    = sum(h.current_value for h in holdings)
    demat_invested = sum(h.avg_price * h.quantity for h in holdings)
    demat_pnl      = demat_value - demat_invested
    demat_pnl_pct  = (demat_pnl / demat_invested * 100) if demat_invested else 0.0

    # Today's traded P&L from zerodha_positions (day type)
    pos_result = await session.execute(
        select(ZerodhaPosition).where(ZerodhaPosition.position_type == "day")
    )
    day_positions = pos_result.scalars().all()
    today_pnl = sum(p.pnl for p in day_positions)

    # Available cash from margins API
    available_cash = 0.0
    try:
        margins = await kite.get_margins("equity")
        available_cash = float(margins.get("available", {}).get("live_balance", 0.0))
    except Exception as exc:
        logger.warning(f"[zerodha_portfolio] Margins fetch failed: {exc}")

    return {
        "demat_value":    round(demat_value,    2),
        "demat_invested": round(demat_invested, 2),
        "demat_pnl":      round(demat_pnl,      2),
        "demat_pnl_pct":  round(demat_pnl_pct,  4),
        "today_pnl":      round(today_pnl,       2),
        "available_cash": round(available_cash,  2),
        "total_equity":   round(demat_value + available_cash, 2),
    }


# ── Spec-named convenience aliases (used by tasks + new endpoints) ───────────

async def sync_zerodha_into_tracker(
    session: AsyncSession,
    portfolio_name: str = "Zerodha Demat",
) -> dict:
    """Mirror Zerodha real holdings into the user's TrackerPortfolio.

    Creates (or finds) a TrackerPortfolio named `Zerodha Demat` and upserts
    every Demat holding as a TrackerHolding with notes='source:zerodha'.
    This makes the "My Holdings" page (which reads tracker_*) the single
    place where real + manual stocks live together.

    Idempotent — running twice doesn't duplicate rows.
    """
    from db.models import TrackerPortfolio, TrackerHolding, TrackerTransaction
    import uuid

    kite = get_kite_client()
    if not kite.access_token:
        return {"synced": 0, "skipped": True, "reason": "no_zerodha_token"}

    try:
        raw = await kite.get_holdings()
    except Exception as exc:
        return {"synced": 0, "error": str(exc)}

    # Find or create the Zerodha-mirror portfolio
    res = await session.execute(
        select(TrackerPortfolio).where(TrackerPortfolio.name == portfolio_name)
    )
    portfolio = res.scalar_one_or_none()
    if portfolio is None:
        portfolio = TrackerPortfolio(
            id=str(uuid.uuid4()),
            name=portfolio_name,
            description="Auto-synced from Zerodha — DO NOT EDIT MANUALLY",
        )
        session.add(portfolio)
        await session.flush()

    today = datetime.datetime.utcnow().date()
    synced = 0

    for h in raw:
        sym_bare = str(h.get("tradingsymbol", "")).strip()
        if not sym_bare:
            continue
        # Normalize to NSE suffix for tracker convention
        exch     = str(h.get("exchange", "NSE"))
        sym_nse  = sym_bare if "." in sym_bare else f"{sym_bare}.NS"

        qty     = float(h.get("quantity", 0) or 0)
        avg_prc = float(h.get("average_price", 0) or 0)
        if qty <= 0 or avg_prc <= 0:
            continue

        # Upsert holding row
        res = await session.execute(
            select(TrackerHolding).where(
                TrackerHolding.portfolio_id == portfolio.id,
                TrackerHolding.symbol == sym_nse,
            )
        )
        holding = res.scalar_one_or_none()
        if holding is None:
            holding = TrackerHolding(
                portfolio_id=portfolio.id,
                symbol=sym_nse,
                company_name=sym_bare,
                sector=h.get("sector", "Zerodha Demat"),
                quantity=qty,
                avg_buy_price=avg_prc,
                first_buy_date=today,
                notes="source:zerodha",
            )
            session.add(holding)
        else:
            holding.quantity      = qty
            holding.avg_buy_price = avg_prc
            holding.notes         = "source:zerodha"
        synced += 1

    await session.commit()
    logger.info(
        f"[zerodha_portfolio] Synced {synced} holdings into tracker portfolio "
        f"'{portfolio_name}' (id={portfolio.id})"
    )
    return {
        "synced":       synced,
        "portfolio_id": portfolio.id,
        "portfolio_name": portfolio_name,
    }


async def sync_real_holdings(session: AsyncSession) -> dict:
    """Alias for sync_zerodha_holdings — used by Celery task and /sync endpoint."""
    return await sync_zerodha_holdings(session)


async def get_real_positions(session: AsyncSession) -> dict:
    """Refresh + return day/net positions with P&L sums."""
    summary = await sync_zerodha_positions(session)
    # Read fresh rows back to return structured data
    from db.models import ZerodhaPosition
    day_rows = (await session.execute(
        select(ZerodhaPosition).where(ZerodhaPosition.position_type == "day")
    )).scalars().all()
    net_rows = (await session.execute(
        select(ZerodhaPosition).where(ZerodhaPosition.position_type == "net")
    )).scalars().all()
    return {
        "day": [
            {
                "tradingsymbol": p.tradingsymbol, "exchange": p.exchange,
                "product": p.product, "quantity": p.quantity,
                "average_price": p.average_price, "last_price": p.last_price,
                "pnl": p.pnl, "m2m": p.m2m, "value": p.value,
            } for p in day_rows
        ],
        "net": [
            {
                "tradingsymbol": p.tradingsymbol, "exchange": p.exchange,
                "product": p.product, "quantity": p.quantity,
                "average_price": p.average_price, "last_price": p.last_price,
                "pnl": p.pnl, "m2m": p.m2m, "value": p.value,
            } for p in net_rows
        ],
        "day_pnl_total": summary.get("day_pnl"),
        "net_pnl_total": summary.get("net_pnl"),
        "synced_at":     summary.get("synced_at"),
    }


async def get_full_pnl_summary(session: AsyncSession) -> dict:
    """Combined holdings + positions + margins summary."""
    summary = await get_zerodha_pnl_summary(session)
    try:
        positions = await get_real_positions(session)
        summary["positions"] = positions
    except Exception as exc:
        logger.warning(f"[zerodha_portfolio] positions skipped: {exc}")
        summary["positions"] = {"day": [], "net": []}
    return summary


async def get_or_create_zerodha_portfolio(session: AsyncSession):
    """Find or create a TrackerPortfolio named 'Zerodha Demat'."""
    from db.models import TrackerPortfolio
    result = await session.execute(
        select(TrackerPortfolio).where(TrackerPortfolio.name == "Zerodha Demat")
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        portfolio = TrackerPortfolio(
            name="Zerodha Demat",
            description="Auto-synced from Zerodha Kite",
            currency="INR",
        )
        session.add(portfolio)
        await session.flush()
    return portfolio
