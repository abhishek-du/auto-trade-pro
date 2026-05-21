"""Zerodha Kite portfolio tracker API.

Endpoints:
  GET  /login-url       — returns the OAuth login URL to redirect the user to
  GET  /callback        — Zerodha redirect target; exchanges request_token
  GET  /status          — returns connection status and session metadata
  GET  /holdings        — returns all synced portfolio holdings
  POST /sync            — triggers an immediate holdings sync from Kite
  POST /disconnect      — deactivates the current Kite session

PAPER TRADING ONLY — no orders are placed through these endpoints.
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import KiteSession, PortfolioHolding
from services.kite_service import KiteService
from utils.config import settings
from utils.logger import logger

router = APIRouter(tags=["Kite Portfolio"])


# ── 1. Login URL ──────────────────────────────────────────────────────────────

@router.get("/login-url")
async def get_login_url():
    """Return the Zerodha login URL.  Frontend opens this in a new tab."""
    if not settings.kite_available:
        raise HTTPException(
            status_code=503,
            detail="Kite API credentials not configured — set KITE_API_KEY and KITE_API_SECRET in .env",
        )
    return {"login_url": KiteService.get_login_url()}


# ── 2. OAuth Callback ─────────────────────────────────────────────────────────

@router.get("/callback")
async def kite_callback(
    request_token: str = Query(..., alias="request_token"),
    db: AsyncSession = Depends(get_db),
):
    """Zerodha redirects here with ?request_token=… after successful login.

    Exchanges the token, persists the session, and redirects the user
    back to the Portfolio page.
    """
    if not settings.kite_available:
        raise HTTPException(status_code=503, detail="Kite credentials not configured")

    try:
        kite_sess = await KiteService.generate_session(db, request_token)
        logger.info(f"[Kite] OAuth callback — session id={kite_sess.id} created")
        # Kick off an immediate holdings sync
        try:
            await KiteService.sync_holdings(db)
            await KiteService.update_xirr_for_all(db)
        except Exception as exc:
            logger.warning(f"[Kite] Initial holdings sync failed: {exc}")
    except Exception as exc:
        logger.error(f"[Kite] Callback error: {exc}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}")

    # Redirect user back to the frontend Portfolio page
    return RedirectResponse(url="http://localhost:5173/portfolio?kite_connected=1")


# ── 3. Connection Status ──────────────────────────────────────────────────────

@router.get("/status")
async def get_kite_status(db: AsyncSession = Depends(get_db)):
    """Return whether a valid Kite session exists and when it expires."""
    result = await db.execute(
        select(KiteSession)
        .where(KiteSession.user_id == "default")
        .order_by(KiteSession.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {
            "connected": False,
            "credentials_configured": settings.kite_available,
            "login_url": KiteService.get_login_url() if settings.kite_available else None,
        }

    now_utc = datetime.datetime.utcnow()
    active = bool(row.is_active and row.expires_at > now_utc)

    # Count synced holdings
    cnt_result = await db.execute(
        select(PortfolioHolding).where(PortfolioHolding.quantity > 0)
    )
    holdings_count = len(cnt_result.scalars().all())

    return {
        "connected": active,
        "credentials_configured": settings.kite_available,
        "login_time": row.login_time.isoformat() if row.login_time else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "holdings_count": holdings_count,
        "login_url": KiteService.get_login_url() if settings.kite_available and not active else None,
    }


# ── 4. Holdings ───────────────────────────────────────────────────────────────

@router.get("/holdings")
async def get_holdings(db: AsyncSession = Depends(get_db)):
    """Return all synced portfolio holdings with live PnL."""
    result = await db.execute(
        select(PortfolioHolding)
        .order_by(PortfolioHolding.current_value.desc())
    )
    holdings = result.scalars().all()

    total_invested = sum(h.avg_price * h.quantity for h in holdings)
    total_current  = sum(h.current_value for h in holdings)
    total_pnl      = total_current - total_invested
    total_pnl_pct  = (total_pnl / total_invested * 100) if total_invested else 0.0

    return {
        "summary": {
            "total_holdings": len(holdings),
            "total_invested": round(total_invested, 2),
            "total_current_value": round(total_current, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 4),
        },
        "holdings": [
            {
                "id":              h.id,
                "tradingsymbol":   h.tradingsymbol,
                "exchange":        h.exchange,
                "isin":            h.isin,
                "quantity":        h.quantity,
                "avg_price":       h.avg_price,
                "last_price":      h.last_price,
                "current_value":   h.current_value,
                "pnl":             h.pnl,
                "pnl_pct":         h.pnl_pct,
                "day_change":      h.day_change,
                "day_change_pct":  h.day_change_pct,
                "sector":          h.sector,
                "buy_date":        h.buy_date.isoformat() if h.buy_date else None,
                "xirr":            h.xirr,
                "synced_at":       h.synced_at.isoformat() if h.synced_at else None,
            }
            for h in holdings
        ],
    }


# ── 5. Manual Holdings Add/Update ─────────────────────────────────────────────

@router.post("/holdings/manual")
async def add_manual_holding(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Add or update a holding manually (for accounts not on Kite)."""
    sym  = (body.get("tradingsymbol") or "").strip().upper()
    exch = (body.get("exchange") or "NSE").strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="tradingsymbol is required")

    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.tradingsymbol == sym,
            PortfolioHolding.exchange == exch,
        )
    )
    holding = result.scalar_one_or_none()
    if holding is None:
        holding = PortfolioHolding(tradingsymbol=sym, exchange=exch)
        db.add(holding)

    qty     = int(body.get("quantity", 0))
    avg_prc = float(body.get("avg_price", 0.0))
    ltp     = float(body.get("last_price", avg_prc))
    cur_val = qty * ltp
    cost    = qty * avg_prc

    holding.quantity      = qty
    holding.avg_price     = avg_prc
    holding.last_price    = ltp
    holding.current_value = cur_val
    holding.pnl           = cur_val - cost
    holding.pnl_pct       = round((ltp - avg_prc) / avg_prc * 100 if avg_prc else 0.0, 4)
    holding.sector        = body.get("sector", "")
    holding.synced_at     = datetime.datetime.utcnow()

    if body.get("buy_date"):
        try:
            holding.buy_date = datetime.date.fromisoformat(body["buy_date"])
        except ValueError:
            pass

    await db.flush()

    # Recompute XIRR
    if holding.buy_date:
        holding.xirr = KiteService.calculate_xirr(
            holding.buy_date, holding.avg_price, holding.quantity, holding.last_price
        )

    return {"status": "ok", "tradingsymbol": sym, "exchange": exch}


# ── 6. Sync ───────────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_holdings(db: AsyncSession = Depends(get_db)):
    """Immediately re-fetch holdings from Kite and update the DB."""
    token = await KiteService.get_access_token(db)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="No active Kite session — please reconnect via /login-url",
        )
    try:
        raw = await KiteService.sync_holdings(db)
        await KiteService.update_xirr_for_all(db)
        return {"status": "ok", "holdings_synced": len(raw)}
    except Exception as exc:
        logger.error(f"[Kite] Sync error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── 7. Disconnect ─────────────────────────────────────────────────────────────

@router.post("/disconnect")
async def disconnect(db: AsyncSession = Depends(get_db)):
    """Deactivate the current Kite session."""
    await KiteService.disconnect(db)
    return {"status": "disconnected"}
