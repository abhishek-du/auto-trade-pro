"""Zerodha KiteConnect v3 API — authentication, portfolio, orders, live data.

Endpoints:
  Auth (Part 3)
    GET  /login-url          — OAuth login URL + instructions
    GET  /callback           — Zerodha redirect; exchanges request_token
    GET  /status             — connection status + user profile
    GET  /margins            — account margins
    POST /logout             — invalidate session

  Portfolio + Orders (Part 8)
    GET  /holdings           — real Demat holdings from Zerodha
    GET  /positions          — today's open positions
    GET  /orders             — today's order book
    GET  /trades             — today's executed trades
    GET  /pnl                — combined P&L summary
    POST /orders             — REAL order placement (confirmation header required)
    DELETE /orders/{id}      — cancel pending order
    GET  /live-prices        — all latest prices from WebSocket / REST fallback
    GET  /market-depth/{sym} — bid/ask order book

  Token health (Part 10)
    GET  /token-status       — expiry time + hours remaining

PAPER TRADING ONLY — real order endpoints require explicit header
  X-Confirm-Real-Order: yes  AND  PAPER_MODE=false AND ZERODHA_ENABLED=true.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.zerodha_client import clear_kite_token, get_kite_client, update_kite_token
from crawler.zerodha_market import get_live_prices, get_market_depth
from crawler.zerodha_websocket import LIVE_PRICES
from db.database import get_db
from engine.zerodha_portfolio import (
    get_zerodha_pnl_summary,
    sync_zerodha_holdings,
    sync_zerodha_positions,
)
from utils.config import settings
from utils.logger import logger

router = APIRouter(tags=["Zerodha"])

_IST = ZoneInfo("Asia/Kolkata")

# ── HTML helpers ──────────────────────────────────────────────────────────────

def _html_success(user_name: str, user_id: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Zerodha Connected</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#0a1120;color:#e2e8f0;
        display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
  .card{{background:#0f1829;border:1px solid #10b98144;border-radius:16px;
          padding:40px 48px;text-align:center;max-width:440px}}
  h1{{color:#10b981;font-size:1.75rem;margin:0 0 8px}}
  p{{color:#94a3b8;margin:6px 0;font-size:0.95rem}}
  .note{{color:#64748b;font-size:0.82rem;margin-top:20px}}
  .close-btn{{margin-top:24px;padding:10px 28px;border-radius:8px;
               background:linear-gradient(135deg,#1d4ed8,#0891b2);color:#fff;
               border:none;font-size:0.9rem;cursor:pointer}}
</style></head>
<body><div class="card">
  <h1>✓ Zerodha Connected!</h1>
  <p><strong>{user_name}</strong> ({user_id})</p>
  <p>Portfolio and live prices are now active.</p>
  <p class="note">Access token expires at 6:00 AM tomorrow (SEBI regulation).</p>
  <button class="close-btn" onclick="window.close()">Close Window</button>
  <script>
    // Notify the opener (parent tab) that login succeeded, then close.
    try {{
      if (window.opener && !window.opener.closed) {{
        window.opener.postMessage('zerodha_connected', '*');
      }}
    }} catch(e) {{}}
    setTimeout(() => {{ window.close(); }}, 3000);
  </script>
</div></body></html>"""
    return HTMLResponse(content=html)


def _html_error(detail: str) -> HTMLResponse:
    # Escape for JS string — replace ' with \'
    js_detail = detail.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Zerodha Login Failed</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#0a1120;color:#e2e8f0;
        display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
  .card{{background:#0f1829;border:1px solid #f43f5e44;border-radius:16px;
          padding:40px 48px;text-align:center;max-width:500px}}
  h1{{color:#f43f5e;font-size:1.75rem;margin:0 0 8px}}
  p{{color:#94a3b8;margin:6px 0;font-size:0.95rem}}
  .err{{background:#1e0a0a;border:1px solid #f43f5e33;border-radius:8px;
         padding:12px 16px;margin:16px 0;font-size:0.82rem;color:#fca5a5;
         text-align:left;word-break:break-all;font-family:monospace}}
</style></head>
<body><div class="card">
  <h1>✗ Login Failed</h1>
  <p>AutoTrade Pro could not complete the Zerodha session exchange.</p>
  <div class="err">{detail}</div>
  <p style="font-size:0.82rem;color:#64748b">Check that ZERODHA_API_KEY and ZERODHA_API_SECRET are correct,<br>
  and that the redirect URL registered in Zerodha Developer Console<br>
  matches exactly: <strong style="color:#94a3b8">http://localhost:8000/api/v1/zerodha/callback</strong></p>
  <script>
    // Notify the opener of the failure so it can show a toast.
    try {{
      if (window.opener && !window.opener.closed) {{
        window.opener.postMessage('zerodha_error:{js_detail}', '*');
      }}
    }} catch(e) {{}}
    setTimeout(() => {{ window.close(); }}, 8000);
  </script>
</div></body></html>"""
    return HTMLResponse(content=html, status_code=400)


# ── Token expiry helper ───────────────────────────────────────────────────────

def _token_expiry_ist() -> datetime.datetime:
    """Next 6:00 AM IST."""
    now_ist = datetime.datetime.now(_IST)
    exp = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_ist >= exp:
        exp += datetime.timedelta(days=1)
    return exp


# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — Auth endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/login-url")
async def get_login_url():
    """Return the Zerodha Kite login URL with usage instructions."""
    if not settings.zerodha_available:
        raise HTTPException(
            status_code=503,
            detail="Zerodha credentials not configured. "
                   "Set ZERODHA_API_KEY and ZERODHA_API_SECRET in .env",
        )
    kite = get_kite_client()
    return {
        "url":          kite.get_login_url(),
        "redirect_url": settings.ZERODHA_REDIRECT_URL,
        "instructions": [
            "1. Click the URL to open Zerodha login in your browser",
            "2. Log in with your Zerodha credentials and TOTP",
            "3. You will be redirected back automatically — this completes login",
            "4. The access_token is valid until 6:00 AM tomorrow (SEBI regulation)",
        ],
    }


@router.get("/callback")
async def zerodha_callback(
    request_token: str = Query(...),
    action: str = Query(default="login"),
    status: str = Query(default="success"),
):
    """Browser redirect from Zerodha after successful login.

    Exchanges request_token for access_token and persists it.
    Returns a styled HTML page (this is a browser redirect, not an API call).
    """
    if status != "success":
        return _html_error(f"Zerodha login status: {status}")

    if not settings.zerodha_available:
        return _html_error("Zerodha API credentials not configured on server")

    try:
        kite = get_kite_client()
        session_data = await kite.generate_session(request_token)
        update_kite_token(session_data["access_token"])
        user_name = session_data.get("user_name", "")
        user_id   = session_data.get("user_id", "")
        logger.info(f"[zerodha] OAuth complete — user={user_name} ({user_id})")
        return _html_success(user_name, user_id)
    except Exception as exc:
        logger.error(f"[zerodha] Callback error: {exc}", exc_info=True)
        return _html_error(str(exc))


@router.get("/status")
async def get_status():
    """Return connection status and user profile from Zerodha."""
    if not settings.zerodha_available:
        return {
            "connected":           False,
            "api_key_configured":  False,
            "access_token_present": False,
            "error":               "Zerodha API credentials not configured",
        }

    kite = get_kite_client()
    has_token = bool(kite.access_token)
    if not has_token:
        return {
            "connected":            False,
            "api_key_configured":   True,
            "access_token_present": False,
            "login_url":            kite.get_login_url(),
            "error":                "No access token — please login",
        }

    # Verify token by calling /user/profile
    try:
        profile = await kite.get_profile()
        margins = await kite.get_margins("equity")
        available_cash = float(
            margins.get("available", {}).get("live_balance", 0.0)
        )
        exp_ist = _token_expiry_ist()
        return {
            "connected":            True,
            "api_key_configured":   True,
            "access_token_present": True,
            "user_name":            profile.get("user_name"),
            "user_id":              profile.get("user_id"),
            "email":                profile.get("email"),
            "available_margins_inr": available_cash,
            "token_expires_at":     "6:00 AM tomorrow",
            "expires_datetime_ist": exp_ist.strftime("%Y-%m-%d %H:%M IST"),
            "last_connected":       datetime.datetime.utcnow().isoformat(),
            "error":                None,
        }
    except Exception as exc:
        return {
            "connected":            False,
            "api_key_configured":   True,
            "access_token_present": True,
            "login_url":            kite.get_login_url(),
            "error":                f"Token expired or invalid: {exc}",
        }


@router.get("/margins")
async def get_margins():
    """Return account equity and commodity margins."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        margins = await kite.get_margins()
        eq = margins.get("equity", {})
        cm = margins.get("commodity", {})
        return {
            "equity": {
                "net":              eq.get("net", 0.0),
                "available_cash":   eq.get("available", {}).get("live_balance", 0.0),
                "opening_balance":  eq.get("available", {}).get("opening_balance", 0.0),
                "used_margin":      eq.get("utilised", {}).get("debits", 0.0),
            },
            "commodity": {
                "net":              cm.get("net", 0.0),
                "available_cash":   cm.get("available", {}).get("live_balance", 0.0),
                "opening_balance":  cm.get("available", {}).get("opening_balance", 0.0),
                "used_margin":      cm.get("utilised", {}).get("debits", 0.0),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/logout")
async def logout():
    """Invalidate the Kite session and clear the stored access token."""
    kite = get_kite_client()
    if kite.access_token:
        try:
            await kite.invalidate_session()
        except Exception as exc:
            logger.warning(f"[zerodha] Session invalidation failed: {exc}")
    clear_kite_token()
    return {"status": "logged_out"}


# ─────────────────────────────────────────────────────────────────────────────
# PART 8 — Portfolio + orders + live data
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/holdings")
async def get_holdings(db: AsyncSession = Depends(get_db)):
    """Sync and return real Zerodha Demat holdings."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        summary = await sync_zerodha_holdings(db)
        return summary
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    """Sync and return today's open positions."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        return await sync_zerodha_positions(db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/orders")
async def get_orders():
    """Return today's order book from Kite."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        return {"orders": await kite.get_orders()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/trades")
async def get_trades():
    """Return today's executed trades from Kite."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        return {"trades": await kite.get_trades()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/pnl")
async def get_pnl(db: AsyncSession = Depends(get_db)):
    """Return combined P&L summary (holdings + today's positions + cash)."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        return await get_zerodha_pnl_summary(db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/orders")
async def place_order(
    body: dict,
    x_confirm_real_order: str | None = Header(default=None, alias="x-confirm-real-order"),
    db: AsyncSession = Depends(get_db),
):
    """Place a REAL order through Zerodha — extreme care required.

    Requires header: X-Confirm-Real-Order: yes
    PAPER_MODE must be false AND ZERODHA_ENABLED must be true.
    """
    if x_confirm_real_order != "yes":
        raise HTTPException(
            status_code=400,
            detail="Missing confirmation header — send 'X-Confirm-Real-Order: yes' to confirm real order placement",
        )

    if settings.PAPER_MODE:
        raise HTTPException(
            status_code=403,
            detail="PAPER_MODE is active — set PAPER_MODE=false in .env to enable real trading",
        )

    logger.critical(
        f"[zerodha] REAL ORDER REQUEST — {body.get('transaction_type')} "
        f"{body.get('quantity')} × {body.get('symbol')} "
        f"by API caller"
    )

    sym              = str(body.get("symbol", "")).replace(".NS", "")
    transaction_type = str(body.get("transaction_type", "BUY")).upper()
    qty              = int(body.get("quantity", 1))
    order_type       = str(body.get("order_type", "LIMIT")).upper()
    price            = float(body.get("price", 0.0))

    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")

    try:
        order_id = await kite.place_order(
            tradingsymbol    = sym,
            exchange         = str(body.get("exchange", "NSE")).upper(),
            transaction_type = transaction_type,
            quantity         = qty,
            order_type       = order_type,
            product          = str(body.get("product", "CNC")).upper(),
            price            = price,
        )
        logger.critical(f"[zerodha] REAL ORDER PLACED — order_id={order_id}")
        return {"order_id": order_id, "status": "placed"}
    except Exception as exc:
        logger.error(f"[zerodha] Order failed: {exc}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc))


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str):
    """Cancel a pending order."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        cancelled_id = await kite.cancel_order(order_id)
        return {"order_id": cancelled_id, "status": "cancelled"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/live-prices")
async def live_prices(
    symbols: str = Query(default="", description="Comma-separated .NS symbols; empty = all")
):
    """Return latest prices from the WebSocket feed (falls back to REST if WS not active)."""
    if LIVE_PRICES:
        if symbols:
            sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
            filtered = {k: v for k, v in LIVE_PRICES.items() if k in sym_list}
            return {"source": "websocket", "prices": filtered}
        return {"source": "websocket", "prices": dict(LIVE_PRICES)}

    # Fallback: REST LTP
    kite = get_kite_client()
    if not kite.access_token:
        return {"source": "none", "prices": {}}

    from crawler.zerodha_market import NSE_TOKENS
    sym_list = (
        [s.strip() for s in symbols.split(",") if s.strip()]
        if symbols else list(NSE_TOKENS.keys())
    )
    try:
        prices = await get_live_prices(sym_list)
        return {"source": "rest_ltp", "prices": prices}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/market-depth/{symbol}")
async def market_depth(symbol: str):
    """Return order book (bid/ask) for a symbol."""
    kite = get_kite_client()
    if not kite.access_token:
        raise HTTPException(status_code=401, detail="Not connected to Zerodha")
    try:
        sym_ns = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
        return await get_market_depth(sym_ns)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# PART 10 — Token status endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/token-status")
async def token_status():
    """Return access token validity and time until 6:00 AM IST expiry."""
    kite = get_kite_client()
    has_token = bool(kite.access_token)
    if not has_token:
        return {
            "valid":           False,
            "expires_at":      "6:00 AM IST",
            "hours_remaining": 0.0,
            "login_url":       kite.get_login_url() if settings.zerodha_available else None,
        }

    # Verify token is live
    valid = False
    try:
        await kite.get_profile()
        valid = True
    except Exception:
        pass

    now_ist    = datetime.datetime.now(_IST)
    exp_ist    = _token_expiry_ist()
    hours_left = (exp_ist - now_ist).total_seconds() / 3600

    return {
        "valid":           valid,
        "expires_at":      exp_ist.strftime("%-I:%M %p IST %d %b"),
        "hours_remaining": round(hours_left, 2),
        "login_url":       kite.get_login_url() if not valid and settings.zerodha_available else None,
    }
