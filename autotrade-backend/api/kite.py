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


# ── Zerodha v3 fallback helpers ──────────────────────────────────────────────
# If only ZERODHA_API_KEY is configured (newer integration), treat the user
# as connected via that path. Legacy /kite/* endpoints transparently delegate.

def _zerodha_v3_active() -> bool:
    """True when the new Zerodha v3 integration has a valid token in env."""
    return bool(
        settings.zerodha_available
        and settings.ZERODHA_ACCESS_TOKEN
        and settings.ZERODHA_ENABLED
    )


async def _zerodha_v3_status() -> dict | None:
    """Probe Zerodha v3 client; return status dict or None if not configured."""
    if not _zerodha_v3_active():
        return None
    try:
        from crawler.zerodha_client import get_kite_client
        kite = get_kite_client()
        # Cheap call to verify the token is live
        await kite.get_profile()

        # Token expires at 06:00 IST next day — compute approximate expiry
        now_utc = datetime.datetime.utcnow()
        # 06:00 IST = 00:30 UTC
        next_expiry = (now_utc + datetime.timedelta(days=1)).replace(
            hour=0, minute=30, second=0, microsecond=0,
        )
        if now_utc.hour < 1:
            next_expiry = now_utc.replace(hour=0, minute=30, second=0, microsecond=0)

        return {
            "connected":              True,
            "credentials_configured": True,
            "via":                    "zerodha_v3",
            "login_time":             None,
            "expires_at":             next_expiry.isoformat(),
        }
    except Exception as exc:
        logger.debug(f"[kite] v3 token probe failed: {exc}")
        return {
            "connected":              False,
            "credentials_configured": True,
            "via":                    "zerodha_v3",
            "error":                  str(exc)[:120],
        }


# ── 1. Login URL ──────────────────────────────────────────────────────────────

@router.get("/login-url")
async def get_login_url():
    """Return the Zerodha login URL.  Frontend opens this in a new tab.

    Prefers legacy KITE_API_KEY, falls back to ZERODHA_API_KEY (v3).
    """
    if settings.kite_available:
        return {"login_url": KiteService.get_login_url()}
    if settings.zerodha_available:
        from crawler.zerodha_client import get_kite_client
        return {"login_url": get_kite_client().get_login_url()}
    raise HTTPException(
        status_code=503,
        detail="Zerodha credentials not configured — set ZERODHA_API_KEY (or KITE_API_KEY) in .env",
    )


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
    """Return whether a valid Kite session exists and when it expires.

    Resolution order:
      1. Legacy KiteSession DB row (KITE_API_KEY integration)
      2. Zerodha v3 access token in .env (ZERODHA_API_KEY integration)
    """
    # 1. Check legacy KiteSession first
    result = await db.execute(
        select(KiteSession)
        .where(KiteSession.user_id == "default")
        .order_by(KiteSession.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()

    holdings_count = 0
    cnt_result = await db.execute(
        select(PortfolioHolding).where(PortfolioHolding.quantity > 0)
    )
    holdings_count = len(cnt_result.scalars().all())

    now_utc = datetime.datetime.utcnow()
    legacy_active = bool(row and row.is_active and row.expires_at > now_utc)

    if legacy_active:
        return {
            "connected":              True,
            "credentials_configured": settings.kite_available,
            "via":                    "legacy_kite",
            "login_time":             row.login_time.isoformat() if row.login_time else None,
            "expires_at":             row.expires_at.isoformat() if row.expires_at else None,
            "holdings_count":         holdings_count,
            "login_url":              None,
        }

    # 2. Fall back to Zerodha v3
    v3 = await _zerodha_v3_status()
    if v3 and v3.get("connected"):
        return {
            **v3,
            "holdings_count": holdings_count,
            "login_url":      None,
        }

    # 3. Nothing valid — return the appropriate "connect" prompt
    has_any_creds = settings.kite_available or settings.zerodha_available
    login_url = None
    if settings.kite_available:
        login_url = KiteService.get_login_url()
    elif settings.zerodha_available:
        from crawler.zerodha_client import get_kite_client
        try:
            login_url = get_kite_client().get_login_url()
        except Exception:
            login_url = None

    return {
        "connected":              False,
        "credentials_configured": has_any_creds,
        "holdings_count":         holdings_count,
        "login_url":              login_url,
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
    """Immediately re-fetch holdings from Kite and update the DB.

    Tries legacy KiteSession first; falls back to Zerodha v3 client.
    """
    # 1. Legacy path
    token = await KiteService.get_access_token(db)
    if token:
        try:
            raw = await KiteService.sync_holdings(db)
            await KiteService.update_xirr_for_all(db)
            return {"status": "ok", "holdings_synced": len(raw), "via": "legacy_kite"}
        except Exception as exc:
            logger.warning(f"[Kite] Legacy sync failed, trying v3: {exc}")

    # 2. Zerodha v3 path
    if _zerodha_v3_active():
        try:
            from engine.zerodha_portfolio import sync_real_holdings
            holdings = await sync_real_holdings(db)
            return {"status": "ok", "holdings_synced": len(holdings or []), "via": "zerodha_v3"}
        except Exception as exc:
            logger.error(f"[Kite] v3 sync error: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    raise HTTPException(
        status_code=401,
        detail="No active Zerodha session — please connect via /login-url",
    )


# ── 7. Disconnect ─────────────────────────────────────────────────────────────

@router.post("/disconnect")
async def disconnect(db: AsyncSession = Depends(get_db)):
    """Deactivate the current Kite session."""
    await KiteService.disconnect(db)
    return {"status": "disconnected"}
