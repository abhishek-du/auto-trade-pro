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

import asyncio
import datetime
import math
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.zerodha_client import clear_kite_token, get_kite_client, update_kite_token
import crawler.zerodha_market as _zm
from crawler.zerodha_market import get_kite_historical, get_live_prices, get_market_depth
from crawler.zerodha_websocket import LIVE_PRICES
from db.database import get_db
from engine.indicators import compute_indicators
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
        # Best-effort ticker status check
        ticker_running = False
        try:
            from crawler.zerodha_ticker import is_ticker_running as _tr
            ticker_running = bool(_tr())
        except Exception:
            ticker_running = False
        return {
            "connected":                True,
            "api_key_configured":       True,
            "access_token_present":     True,
            "user_name":                profile.get("user_name"),
            "user_id":                  profile.get("user_id"),
            "email":                    profile.get("email"),
            "available_margins_inr":    available_cash,
            "token_expires_at":         "6:00 AM tomorrow",
            "expires_datetime_ist":     exp_ist.strftime("%Y-%m-%d %H:%M IST"),
            "last_connected":           datetime.datetime.utcnow().isoformat(),
            "kite_historical_available": _zm._kite_historical_available,
            "paper_mode":               bool(settings.ZERODHA_PAPER_MODE or settings.PAPER_MODE),
            "ticker_running":           ticker_running,
            "redirect_url":             settings.ZERODHA_REDIRECT_URL,
            "error":                    None,
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

    # When no ?symbols= filter is given, fall back to the configured
    # watchlist (large + mid caps + extras). Returning all 9,800 hydrated
    # NSE_TOKENS makes the URL blow Kite's 8KB query cap AND wastes a
    # large chunk of the LTP rate budget on symbols nobody asked for.
    from crawler.zerodha_market import NSE_TOKENS
    if symbols:
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    else:
        watchlist = (
            list(getattr(settings, "WATCHLIST_NSE_LARGE_CAP", []))
            + list(getattr(settings, "WATCHLIST_NSE_MID_CAP", []))
            + _EXTRA_NSE
        )
        # Dedup but preserve order
        seen: set[str] = set()
        sym_list = []
        for s in watchlist:
            bare = s.replace(".NS", "").strip()
            if bare and bare not in seen:
                seen.add(bare)
                sym_list.append(f"{bare}.NS")
        # Defensive cap — if the watchlist ever balloons, don't drag the
        # whole NSE_TOKENS map into the request.
        sym_list = sym_list[:500]
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


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist analysis — technical signals for any NSE symbols
# ─────────────────────────────────────────────────────────────────────────────

def _score_to_signal(score: float) -> str:
    if score >= 60:  return "STRONG_BUY"
    if score >= 25:  return "BUY"
    if score >= -25: return "NEUTRAL"
    if score >= -60: return "SELL"
    return "STRONG_SELL"


# Kite's historical-data API caps at 3 req/sec. Both watchlist-analysis and
# auto-scan fan out asyncio.gather([_analyse_symbol(s) for s in universe])
# over 50+ symbols at once. Without a gate, every call past the first 3
# returns 429.
#
# Semaphore: 3 in-flight calls match Kite's burst budget so the universe
# scan completes as fast as the upstream allows. Throttle: 0.35s minimum
# between request *starts* keeps the observed rate at ~2.8 req/sec even
# when responses come back quickly. Both gates are module-level so they
# serialize across both routes — an auto-scan can't blow through the
# budget mid-watchlist.
_KITE_HISTORICAL_SEMAPHORE = asyncio.Semaphore(3)
_KITE_HISTORICAL_MIN_INTERVAL = 0.35   # seconds between request starts
_kite_historical_last_call: float = 0.0
_kite_historical_lock = asyncio.Lock()


async def _kite_throttle() -> None:
    """Block until enough time has passed since the last Kite historical call."""
    global _kite_historical_last_call
    async with _kite_historical_lock:
        import time as _t
        now = _t.monotonic()
        delta = now - _kite_historical_last_call
        if delta < _KITE_HISTORICAL_MIN_INTERVAL:
            await asyncio.sleep(_KITE_HISTORICAL_MIN_INTERVAL - delta)
        _kite_historical_last_call = _t.monotonic()


async def _candles_from_db(sym_ns: str, from_date: str) -> list[dict]:
    """Return cached daily candles for ``sym_ns`` from the candles table.

    Empty list when the cache is stale (latest bar > 2 days behind today's
    NSE close) or absent. Lets the watchlist/auto-scan endpoints skip Kite
    entirely when the daily kite_sync_candles task has already populated
    the bar — a single SELECT is dramatically cheaper than ~80 Kite calls.
    """
    from db.database import AsyncSessionLocal
    from db.models import Candle as _Candle
    from sqlalchemy import select as _select
    cutoff = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            _select(_Candle)
            .where(
                _Candle.symbol == sym_ns,
                _Candle.timeframe == "1d",
                _Candle.timestamp >= datetime.datetime.fromisoformat(from_date),
            )
            .order_by(_Candle.timestamp.asc())
        )).scalars().all()
    if not rows:
        return []
    # Staleness check: the most recent cached bar must be from within the
    # last 2 calendar days. NSE is closed on weekends so this won't fire
    # spuriously across a normal Monday morning.
    latest = rows[-1].timestamp.date().isoformat()
    if latest < cutoff:
        return []
    return [
        {
            "timestamp": r.timestamp,
            "open":      float(r.open),
            "high":      float(r.high),
            "low":       float(r.low),
            "close":     float(r.close),
            "volume":    float(r.volume),
        }
        for r in rows
    ]


async def _analyse_symbol(sym: str, has_token: bool, ltp_map: dict, from_date: str, to_date: str) -> dict:
    """Fetch 120d daily candles + compute indicators for one NSE symbol.

    Resolution order: DB cache → Kite historical (rate-limited) → yfinance.
    The DB cache short-circuit means watchlist-analysis and auto-scan only
    hit Kite when the daily candle-sync task hasn't run for the symbol.
    """
    from crawler.india_price_feed import fetch_nse_candles

    try:
        # Fast path: serve from the daily candles cache when fresh.
        candles = await _candles_from_db(f"{sym}.NS", from_date)

        if not candles and has_token:
            async with _KITE_HISTORICAL_SEMAPHORE:
                await _kite_throttle()
                candles = await get_kite_historical(f"{sym}.NS", from_date, to_date, interval="1d")

        if not candles:
            try:
                candles = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: fetch_nse_candles(f"{sym}.NS", interval="1d", period="120d")
                    ),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                candles = []

        if not candles:
            return {"symbol": sym, "error": "No historical data"}

        df = pd.DataFrame(candles).sort_values("timestamp").reset_index(drop=True)
        if len(df) < 15:
            return {"symbol": sym, "error": "Insufficient data"}

        sig = compute_indicators(df)

        ltp       = ltp_map.get(sym) or float(df["close"].iloc[-1])
        prev      = float(df["close"].iloc[-2]) if len(df) >= 2 else ltp
        chg_pct   = ((ltp - prev) / prev * 100) if prev else 0.0

        def _n(v: float) -> float | None:
            return None if math.isnan(v) else round(v, 2)

        return {
            "symbol":           sym,
            "ltp":              round(ltp, 2),
            "change_pct":       round(chg_pct, 2),
            "signal":           _score_to_signal(sig.composite_score),
            "composite_score":  round(sig.composite_score, 1),
            "rsi":              _n(sig.rsi),
            "rsi_signal":       sig.rsi_signal,
            "macd_cross":       sig.macd_cross,
            "macd_histogram":   _n(sig.macd_histogram),
            "ema_trend":        sig.ema_trend,
            "supertrend":       sig.supertrend_direction,
            "bb_position":      sig.bb_position,
            "adx":              _n(sig.adx),
            "adx_strength":     sig.adx_trend_strength,
            "ichimoku_signal":  sig.ichimoku_signal,
            "support":          _n(sig.bb_lower),
            "resistance":       _n(sig.bb_upper),
            "vwap":             _n(sig.vwap),
            "error":            None,
        }
    except Exception as exc:
        logger.warning(f"[zerodha] Analysis failed for {sym}: {exc}")
        return {"symbol": sym, "error": str(exc)}


@router.get("/watchlist-analysis")
async def watchlist_analysis(
    symbols: str = Query(..., description="Comma-separated NSE symbols, e.g. RELIANCE,TCS,HDFCBANK"),
):
    """Compute technical analysis (RSI, MACD, EMA, Ichimoku, composite score) for watchlist symbols.

    Uses Kite historical data when connected; falls back to yfinance automatically.
    Runs all symbols in parallel — typical response time 2–5 s for 10 symbols.
    """
    sym_list = [s.strip().upper().replace(".NS", "") for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {"results": [], "source": "none"}

    kite      = get_kite_client()
    has_token = bool(kite.access_token)

    to_date   = datetime.date.today().strftime("%Y-%m-%d")
    from_date = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y-%m-%d")

    # Batch-fetch LTP for all symbols in one API call
    ltp_map: dict[str, float] = {}
    if has_token:
        try:
            raw_ltp = await kite.get_ltp([f"NSE:{s}" for s in sym_list])
            for inst, data in raw_ltp.items():
                ltp_map[inst.replace("NSE:", "")] = float(data.get("last_price", 0.0))
        except Exception as exc:
            logger.warning(f"[zerodha] Batch LTP failed: {exc}")

    results = await asyncio.gather(
        *[_analyse_symbol(s, has_token, ltp_map, from_date, to_date) for s in sym_list]
    )

    return {
        "results": list(results),
        "source":  "kite" if has_token else "yfinance",
        "as_of":   datetime.datetime.utcnow().isoformat() + "Z",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Deep analysis — full breakdown for a single symbol
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/deep-analysis/{symbol}")
async def deep_analysis(symbol: str):
    """Full deep analysis for one NSE symbol.

    Returns indicator reasoning, trade setup (entry/SL/targets/R:R),
    when-to-buy/sell guidance, Finnhub news, and Groq AI commentary.
    Falls back to yfinance when Zerodha not connected.
    """
    from crawler.india_price_feed import fetch_nse_candles
    from engine.deep_analysis import (
        build_trade_setup,
        fetch_stock_news,
        generate_reasoning,
        groq_commentary,
    )

    sym       = symbol.strip().upper().replace(".NS", "")
    kite      = get_kite_client()
    has_token = bool(kite.access_token)

    to_date   = datetime.date.today().strftime("%Y-%m-%d")
    from_date = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y-%m-%d")

    # ── Fetch historical candles ──────────────────────────────────────────────
    # Try the DB cache first — same rationale as _analyse_symbol. Only call
    # Kite when the cached series is stale (>2 days behind) or missing.
    candles = await _candles_from_db(f"{sym}.NS", from_date)
    if not candles and has_token:
        # Share the global throttle with _analyse_symbol so a deep-analysis
        # request issued while watchlist-analysis is in flight can't tip the
        # combined rate over Kite's 3 req/sec budget.
        async with _KITE_HISTORICAL_SEMAPHORE:
            await _kite_throttle()
            candles = await get_kite_historical(f"{sym}.NS", from_date, to_date, interval="1d")

    if not candles:
        try:
            candles = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: fetch_nse_candles(f"{sym}.NS", interval="1d", period="120d")
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            candles = []

    if not candles:
        raise HTTPException(status_code=404, detail=f"No historical data for {sym}")

    df = pd.DataFrame(candles).sort_values("timestamp").reset_index(drop=True)
    if len(df) < 15:
        raise HTTPException(status_code=422, detail=f"Insufficient data for {sym} ({len(df)} rows)")

    # ── Compute indicators ────────────────────────────────────────────────────
    sig = compute_indicators(df)

    # ── LTP ───────────────────────────────────────────────────────────────────
    ltp = float(df["close"].iloc[-1])
    if has_token:
        try:
            raw = await kite.get_ltp([f"NSE:{sym}"])
            ltp = float(raw.get(f"NSE:{sym}", {}).get("last_price", ltp))
        except Exception:
            pass

    prev      = float(df["close"].iloc[-2]) if len(df) >= 2 else ltp
    chg_pct   = ((ltp - prev) / prev * 100) if prev else 0.0

    score = sig.composite_score
    signal_label = _score_to_signal(score)

    def _n(v: float):
        return None if math.isnan(v) else round(v, 2)

    # ── Reasoning, trade setup, news, AI — run news+AI in parallel ───────────
    reasoning = generate_reasoning(sig, ltp)
    setup     = build_trade_setup(sig, ltp, signal_label)

    news, ai_text = await asyncio.gather(
        fetch_stock_news(sym),
        groq_commentary(sym, ltp, chg_pct, sig, reasoning, setup),
    )

    return {
        "symbol":          sym,
        "ltp":             round(ltp, 2),
        "change_pct":      round(chg_pct, 2),
        "signal":          signal_label,
        "composite_score": round(score, 1),
        "data_source":     "kite" if has_token else "yfinance",
        "as_of":           datetime.datetime.utcnow().isoformat() + "Z",

        "indicators": {
            "rsi":              _n(sig.rsi),
            "rsi_signal":       sig.rsi_signal,
            "macd":             _n(sig.macd),
            "macd_signal":      _n(sig.macd_signal),
            "macd_histogram":   _n(sig.macd_histogram),
            "macd_cross":       sig.macd_cross,
            "ema_20":           _n(sig.ema_20),
            "ema_50":           _n(sig.ema_50),
            "ema_200":          _n(sig.ema_200),
            "ema_trend":        sig.ema_trend,
            "bb_upper":         _n(sig.bb_upper),
            "bb_middle":        _n(sig.bb_middle),
            "bb_lower":         _n(sig.bb_lower),
            "bb_position":      sig.bb_position,
            "supertrend":       _n(sig.supertrend),
            "supertrend_dir":   sig.supertrend_direction,
            "ichimoku_signal":  sig.ichimoku_signal,
            "adx":              _n(sig.adx),
            "adx_strength":     sig.adx_trend_strength,
            "adx_direction":    sig.adx_direction,
            "vwap":             _n(sig.vwap),
            "stoch_k":          _n(sig.stoch_k),
            "stoch_d":          _n(sig.stoch_d),
            "stoch_signal":     sig.stoch_signal,
        },

        "reasoning":  reasoning,
        "trade_setup": setup,
        "news":        news,
        "ai_summary":  ai_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Auto-scanner — scan full NSE universe, return all BUY+ signals
# ─────────────────────────────────────────────────────────────────────────────

# Extra popular NSE stocks beyond the configured watchlists.
# TATAMOTORS removed: demerged Oct 2024 → TATAMOTORS (PV) + TMLPV (CV);
# yfinance reports the legacy symbol as delisted.
# LTIM removed: merged into LTIMINDTREE post the L&T-Mindtree merger.
_EXTRA_NSE = [
    "TITAN", "BAJAJFINSV", "INDUSINDBK", "JSWSTEEL",
    "TATASTEEL", "TECHM", "HINDALCO", "DIVISLAB", "CIPLA",
    "ADANIPORTS", "BPCL", "HEROMOTOCO", "EICHERMOT", "ONGC",
    "CHOLAFIN", "MARICO", "DABUR", "LUPIN", "TORNTPHARM",
    "FEDERALBNK", "DLF", "GODREJPROP", "LTIMINDTREE", "MPHASIS",
    "TATAPOWER", "BANKBARODA", "CANBK", "AUROPHARMA", "BIOCON",
    "ESCORTS", "SUZLON", "IRCTC", "HAL", "BEL", "BHEL",
    "RECLTD", "PFC", "IRFC", "NHPC",
]


@router.get("/auto-scan")
async def auto_scan(min_score: float = Query(default=25.0, description="Minimum composite score; 25=BUY, 60=STRONG_BUY")):
    """Scan all configured + extended NSE stocks and return those with BUY or better signals.

    Runs all symbols in parallel — typical time: 4–10 s (yfinance) or 2–4 s (Kite).
    Returns buy_signals sorted by score descending.
    """
    # Build deduplicated universe
    raw_universe = (
        list(settings.WATCHLIST_NSE_LARGE_CAP)
        + list(settings.WATCHLIST_NSE_MID_CAP)
        + _EXTRA_NSE
    )
    seen: set[str] = set()
    universe: list[str] = []
    for s in raw_universe:
        if s not in seen:
            seen.add(s)
            universe.append(s)

    kite      = get_kite_client()
    has_token = bool(kite.access_token)
    to_date   = datetime.date.today().strftime("%Y-%m-%d")
    from_date = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y-%m-%d")

    # Batch LTP fetch
    ltp_map: dict[str, float] = {}
    if has_token:
        try:
            raw_ltp = await kite.get_ltp([f"NSE:{s}" for s in universe])
            for inst, data in raw_ltp.items():
                ltp_map[inst.replace("NSE:", "")] = float(data.get("last_price", 0.0))
        except Exception as exc:
            logger.warning(f"[zerodha] Auto-scan batch LTP failed: {exc}")

    all_results = await asyncio.gather(
        *[_analyse_symbol(s, has_token, ltp_map, from_date, to_date) for s in universe],
        return_exceptions=False,
    )

    valid   = [r for r in all_results if not r.get("error")]
    signals = {
        "STRONG_BUY": [],
        "BUY":        [],
        "NEUTRAL":    [],
        "SELL":       [],
        "STRONG_SELL":[],
    }
    for r in valid:
        signals.setdefault(r.get("signal", "NEUTRAL"), []).append(r)

    for k in signals:
        signals[k].sort(key=lambda r: r.get("composite_score", 0), reverse=True)

    buy_signals = signals["STRONG_BUY"] + signals["BUY"]

    return {
        "buy_signals":      buy_signals,
        "all_signals":      signals,
        "total_scanned":    len(universe),
        "valid_count":      len(valid),
        "error_count":      len(all_results) - len(valid),
        "strong_buy_count": len(signals["STRONG_BUY"]),
        "buy_count":        len(signals["BUY"]),
        "neutral_count":    len(signals["NEUTRAL"]),
        "sell_count":       len(signals["SELL"]) + len(signals["STRONG_SELL"]),
        "source":                    "kite_live+yfinance_history" if (has_token and not _zm._kite_historical_available) else ("kite" if has_token else "yfinance"),
        "kite_historical_available": _zm._kite_historical_available,
        "scanned_at":                datetime.datetime.utcnow().isoformat() + "Z",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Mutual-fund scanner — NAV trend analysis + momentum signals
# ─────────────────────────────────────────────────────────────────────────────

_MF_SCHEME_NAMES: dict[str, str] = {
    "120503": "Mirae Asset Large Cap Fund – Regular Growth",
    "119598": "Axis Bluechip Fund – Regular Growth",
    "100356": "SBI Bluechip Fund – Regular Growth",
    "120716": "HDFC Top 100 Fund – Regular Growth",
    "118989": "ICICI Pru Bluechip Fund – Regular Growth",
}


async def _analyse_mf(code: str) -> dict:
    """Fetch NAV history from MFAPI and compute momentum signal."""
    import httpx as _httpx

    try:
        async with _httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"https://api.mfapi.in/mf/{code}")
            if r.status_code != 200:
                return {"scheme_code": code, "error": f"MFAPI HTTP {r.status_code}"}
            body = r.json()

        meta     = body.get("meta", {})
        nav_raw  = body.get("data", [])  # newest-first

        entries: list[tuple[datetime.date, float]] = []
        for item in nav_raw:
            try:
                d = datetime.datetime.strptime(item["date"], "%d-%m-%Y").date()
                n = float(item["nav"])
                entries.append((d, n))
            except (KeyError, ValueError):
                continue

        if len(entries) < 30:
            return {"scheme_code": code, "error": "Insufficient NAV history"}

        entries.sort(key=lambda x: x[0])   # oldest first
        latest_date, latest_nav = entries[-1]

        def _nav_n_days_ago(n: int) -> float | None:
            target = latest_date - datetime.timedelta(days=n)
            for d, v in reversed(entries[:-1]):
                if d <= target:
                    return v
            return None

        nav_7  = _nav_n_days_ago(7)
        nav_30 = _nav_n_days_ago(30)
        nav_90 = _nav_n_days_ago(90)
        nav_365= _nav_n_days_ago(365)

        def _ret(old: float | None) -> float | None:
            return round((latest_nav - old) / old * 100, 2) if old else None

        ret_1w = _ret(nav_7)
        ret_1m = _ret(nav_30)
        ret_3m = _ret(nav_90)
        ret_1y = _ret(nav_365)

        # Simple SMA-5 vs SMA-20 trend
        sma5  = sum(v for _, v in entries[-5:])  / 5
        sma20 = sum(v for _, v in entries[-20:]) / 20
        nav_trend = "UP" if sma5 > sma20 else "DOWN"

        # Signal logic
        if ret_3m is not None and ret_1m is not None:
            if ret_3m >= 15 and ret_1m >= 4 and nav_trend == "UP":
                signal = "STRONG_BUY"
                reason = f"Exceptional momentum: +{ret_3m:.1f}% (3M) +{ret_1m:.1f}% (1M), NAV trending up"
            elif ret_3m >= 8 and nav_trend == "UP":
                signal = "BUY"
                reason = f"Good 3M returns (+{ret_3m:.1f}%) with positive NAV trend"
            elif ret_3m >= 4 or (ret_1m and ret_1m > 0):
                signal = "BUY"
                reason = f"Moderate positive momentum: +{ret_3m:.1f}% (3M)"
            elif ret_3m and ret_3m >= 0:
                signal = "HOLD"
                reason = f"Low but positive 3M returns (+{ret_3m:.1f}%)"
            else:
                signal = "REVIEW"
                reason = f"Negative 3M return ({ret_3m:.1f}%) — review before investing"
        else:
            signal = "HOLD"
            reason = "Insufficient return history"

        return {
            "scheme_code":  code,
            "scheme_name":  meta.get("scheme_name") or _MF_SCHEME_NAMES.get(code, code),
            "fund_house":   meta.get("fund_house", ""),
            "category":     meta.get("scheme_category", ""),
            "latest_nav":   round(latest_nav, 4),
            "nav_date":     latest_date.isoformat(),
            "nav_trend":    nav_trend,
            "returns": {
                "1w": ret_1w,
                "1m": ret_1m,
                "3m": ret_3m,
                "1y": ret_1y,
            },
            "signal":       signal,
            "reason":       reason,
            "error":        None,
        }
    except Exception as exc:
        # repr() so empty-string exceptions (e.g. some httpx errors) still
        # produce something useful in the log instead of a blank suffix.
        logger.warning(f"[zerodha] MF analysis failed for {code}: {exc!r}", exc_info=True)
        return {"scheme_code": code, "error": repr(exc)}


@router.get("/mf-analysis")
async def mf_analysis():
    """Analyze NAV momentum for all configured mutual fund schemes.

    Returns all funds sorted by 3-month return, with BUY/STRONG_BUY at top.
    """
    schemes = list(settings.WATCHLIST_MUTUAL_FUND_SCHEMES)
    results = await asyncio.gather(*[_analyse_mf(code) for code in schemes])

    valid = sorted(
        [r for r in results if not r.get("error")],
        key=lambda r: r.get("returns", {}).get("3m") or -999,
        reverse=True,
    )

    return {
        "funds":        list(results),
        "buy_count":    sum(1 for r in valid if r.get("signal") in ("BUY", "STRONG_BUY")),
        "scanned_at":   datetime.datetime.utcnow().isoformat() + "Z",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Extended endpoints — GTT, mutual funds, alerts, ticker control, margins
# preview, historical sync.  All endpoints catch exceptions and return
# {"error": str(e)} with appropriate HTTP status (never a bare 500).
# ═════════════════════════════════════════════════════════════════════════════

def _err(status: int, exc: Exception) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


def _need_connection():
    if not settings.ZERODHA_ACCESS_TOKEN or not settings.ZERODHA_ENABLED:
        raise HTTPException(status_code=401, detail="Zerodha not connected")


# ── Profile ──────────────────────────────────────────────────────────────────

@router.get("/profile")
async def get_profile_endpoint():
    _need_connection()
    try:
        kite = get_kite_client()
        return await kite.get_profile()
    except Exception as exc:
        raise _err(502, exc)


# ── Orders — extra (single by id, modify, trades by order) ──────────────────

@router.get("/orders/{order_id}")
async def get_order_by_id(order_id: str):
    _need_connection()
    try:
        kite = get_kite_client()
        return {"history": await kite.get_order_history(order_id)}
    except Exception as exc:
        raise _err(502, exc)


@router.put("/orders/{order_id}")
async def modify_order_endpoint(order_id: str, body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        kite = get_kite_client()
        kw = {k: body[k] for k in ("quantity", "price", "order_type", "trigger_price", "validity")
              if k in body and body[k] is not None}
        new_id = await kite.modify_order(order_id, variety=body.get("variety", "regular"), **kw)
        return {"order_id": new_id, "status": "modified"}
    except Exception as exc:
        raise _err(502, exc)


@router.get("/trades/{order_id}")
async def get_trades_for_order(order_id: str):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_order_trades
        return {"trades": await asyncio.to_thread(get_order_trades, order_id)}
    except Exception as exc:
        raise _err(502, exc)


# ── Positions convert ────────────────────────────────────────────────────────

@router.post("/positions/convert")
async def convert_position_endpoint(body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        kite = get_kite_client()
        ok = await kite.convert_position(
            tradingsymbol    = body["tradingsymbol"],
            exchange         = body.get("exchange", "NSE"),
            transaction_type = body["transaction_type"],
            position_type    = body["position_type"],
            quantity         = int(body["quantity"]),
            old_product      = body["old_product"],
            new_product      = body["new_product"],
        )
        return {"converted": bool(ok)}
    except Exception as exc:
        raise _err(502, exc)


@router.post("/sync")
async def sync_real_holdings_endpoint(db: AsyncSession = Depends(get_db)):
    """Force a holdings + positions sync from Kite into the DB."""
    _need_connection()
    try:
        from engine.zerodha_portfolio import sync_real_holdings, get_real_positions
        h = await sync_real_holdings(db)
        try:
            p = await get_real_positions(db)
        except Exception:
            p = {"day": [], "net": []}
        await db.commit()
        return {"holdings": h, "positions_count": len(p.get("day", [])) + len(p.get("net", []))}
    except Exception as exc:
        await db.rollback()
        raise _err(502, exc)


# ── GTT endpoints ────────────────────────────────────────────────────────────

@router.get("/gtt")
async def list_gtts():
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_gtts
        return {"triggers": await asyncio.to_thread(get_gtts)}
    except Exception as exc:
        raise _err(502, exc)


@router.get("/gtt/{trigger_id}")
async def get_gtt_one(trigger_id: int):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_gtt
        return await asyncio.to_thread(get_gtt, trigger_id)
    except Exception as exc:
        raise _err(502, exc)


@router.post("/gtt/single")
async def place_gtt_single_endpoint(body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        from crawler.zerodha_kite_lib import place_gtt_single
        return await asyncio.to_thread(
            place_gtt_single,
            tradingsymbol     = body["tradingsymbol"],
            exchange          = body.get("exchange", "NSE"),
            last_price        = float(body["last_price"]),
            trigger_price     = float(body["trigger_price"]),
            quantity          = int(body["quantity"]),
            order_price       = float(body["order_price"]),
            transaction_type  = body.get("transaction_type", "BUY"),
            product           = body.get("product", "CNC"),
        )
    except Exception as exc:
        raise _err(502, exc)


@router.post("/gtt/oco")
async def place_gtt_oco_endpoint(body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        from crawler.zerodha_kite_lib import place_gtt_oco
        return await asyncio.to_thread(
            place_gtt_oco,
            tradingsymbol    = body["tradingsymbol"],
            exchange         = body.get("exchange", "NSE"),
            last_price       = float(body["last_price"]),
            stoploss_trigger = float(body["stoploss_trigger"]),
            stoploss_price   = float(body["stoploss_price"]),
            target_trigger   = float(body["target_trigger"]),
            target_price     = float(body["target_price"]),
            quantity         = int(body["quantity"]),
            product          = body.get("product", "CNC"),
        )
    except Exception as exc:
        raise _err(502, exc)


@router.post("/gtt/bracket")
async def place_gtt_bracket_endpoint(body: dict, db: AsyncSession = Depends(get_db)):
    """Buy + OCO (SL + Target) bracket — full real-money flow."""
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        from engine.zerodha_executor import place_gtt_with_oco
        return await place_gtt_with_oco(
            symbol         = body["tradingsymbol"],
            quantity       = int(body["quantity"]),
            buy_price      = float(body["buy_price"]),
            stoploss_price = float(body["stoploss_price"]),
            target_price   = float(body["target_price"]),
            session        = db,
            last_price     = float(body.get("last_price", body["buy_price"])),
        )
    except Exception as exc:
        await db.rollback()
        raise _err(502, exc)


@router.put("/gtt/{trigger_id}")
async def modify_gtt_endpoint(trigger_id: int, body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        from crawler.zerodha_kite_lib import modify_gtt
        return await asyncio.to_thread(
            modify_gtt,
            trigger_id,
            trigger_type=body["trigger_type"],
            tradingsymbol=body["tradingsymbol"],
            exchange=body.get("exchange", "NSE"),
            trigger_values=body["trigger_values"],
            last_price=float(body["last_price"]),
            orders=body["orders"],
        )
    except Exception as exc:
        raise _err(502, exc)


@router.delete("/gtt/{trigger_id}")
async def delete_gtt_endpoint(trigger_id: int):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import delete_gtt
        return await asyncio.to_thread(delete_gtt, trigger_id)
    except Exception as exc:
        raise _err(502, exc)


# ── Quotes / OHLC / LTP / instruments ────────────────────────────────────────

@router.get("/quote")
async def quote_endpoint(symbols: str = Query(...)):
    _need_connection()
    try:
        kite = get_kite_client()
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        # Convert .NS → NSE:SYM
        from crawler.zerodha_instruments import symbol_to_kite
        instruments = [symbol_to_kite(s) for s in sym_list]
        return await kite.get_quote(instruments)
    except Exception as exc:
        raise _err(502, exc)


@router.get("/ohlc")
async def ohlc_endpoint(symbols: str = Query(...)):
    _need_connection()
    try:
        kite = get_kite_client()
        from crawler.zerodha_instruments import symbol_to_kite
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        return await kite.get_ohlc([symbol_to_kite(s) for s in sym_list])
    except Exception as exc:
        raise _err(502, exc)


@router.get("/ltp")
async def ltp_endpoint(symbols: str = Query(...)):
    _need_connection()
    try:
        kite = get_kite_client()
        from crawler.zerodha_instruments import symbol_to_kite
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        return await kite.get_ltp([symbol_to_kite(s) for s in sym_list])
    except Exception as exc:
        raise _err(502, exc)


@router.get("/depth/{symbol}")
async def depth_endpoint(symbol: str):
    _need_connection()
    try:
        sym_ns = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
        return await get_market_depth(sym_ns)
    except Exception as exc:
        raise _err(502, exc)


@router.get("/instruments")
async def instruments_endpoint(exchange: str = Query(default="NSE")):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_instruments
        rows = await asyncio.to_thread(get_instruments, exchange)
        # Keep payload tractable — top-1000 rows
        return {"count": len(rows), "instruments": rows[:1000]}
    except Exception as exc:
        raise _err(502, exc)


# ── Historical ───────────────────────────────────────────────────────────────

@router.get("/historical/{symbol}")
async def historical_endpoint(
    symbol: str,
    interval: str = Query(default="1d"),
    days: int = Query(default=120),
):
    _need_connection()
    try:
        from crawler.zerodha_historical import get_kite_candles_for_range, INTERVAL_MAP
        if interval not in INTERVAL_MAP:
            return {"error": f"Unsupported interval. Allowed: {list(INTERVAL_MAP)}"}
        to = datetime.date.today()
        frm = to - datetime.timedelta(days=days)
        candles = await get_kite_candles_for_range(symbol, frm, to, interval=interval)
        return {"symbol": symbol, "interval": interval, "candles": candles}
    except Exception as exc:
        raise _err(502, exc)


@router.post("/historical/sync")
async def historical_sync_endpoint(
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
):
    _need_connection()
    body = body or {}
    try:
        from crawler.zerodha_historical import sync_all_nse_candles, sync_kite_candles
        if body.get("symbol"):
            return await sync_kite_candles(
                body["symbol"],
                body.get("timeframe", "1d"),
                int(body.get("days_back", 120)),
                db,
            )
        return await sync_all_nse_candles(
            db,
            timeframe=body.get("timeframe", "1d"),
            days_back=int(body.get("days_back", 120)),
        )
    except Exception as exc:
        await db.rollback()
        raise _err(502, exc)


# ── Margins preview ──────────────────────────────────────────────────────────

@router.post("/margins/preview")
async def margins_preview(body: dict):
    _need_connection()
    try:
        from engine.zerodha_executor import calculate_order_margins_preview
        return await calculate_order_margins_preview(
            symbol           = body["symbol"],
            transaction_type = body.get("transaction_type", "BUY"),
            quantity         = int(body["quantity"]),
            price            = float(body.get("price", 0.0)),
            product          = body.get("product", "CNC"),
            exchange         = body.get("exchange", "NSE"),
        )
    except Exception as exc:
        raise _err(502, exc)


@router.post("/margins/basket")
async def margins_basket(body: dict):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_basket_margins
        return await asyncio.to_thread(
            get_basket_margins, body.get("orders", []), bool(body.get("consider_positions", True))
        )
    except Exception as exc:
        raise _err(502, exc)


@router.post("/charges/preview")
async def charges_preview(body: dict):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_virtual_contract_note
        return await asyncio.to_thread(get_virtual_contract_note, body.get("orders", []))
    except Exception as exc:
        raise _err(502, exc)


# ── Ticker control ───────────────────────────────────────────────────────────

@router.post("/ticker/start")
async def ticker_start():
    _need_connection()
    try:
        from crawler.zerodha_ticker import start_kite_ticker, is_ticker_running
        if is_ticker_running():
            return {"running": True, "started": False, "reason": "already_running"}
        started = await asyncio.to_thread(start_kite_ticker)
        return {"running": True, "started": bool(started)}
    except Exception as exc:
        raise _err(502, exc)


@router.post("/ticker/stop")
async def ticker_stop():
    try:
        from crawler.zerodha_ticker import stop_kite_ticker
        await asyncio.to_thread(stop_kite_ticker)
        return {"running": False, "stopped": True}
    except Exception as exc:
        raise _err(502, exc)


@router.get("/ticker/status")
async def ticker_status():
    try:
        from crawler.zerodha_ticker import is_ticker_running, LIVE_TICKS
        return {
            "running": is_ticker_running(),
            "subscribed_count": len(LIVE_TICKS),
        }
    except Exception as exc:
        raise _err(502, exc)


# ── Mutual funds ─────────────────────────────────────────────────────────────

@router.get("/mf/instruments")
async def mf_instruments_endpoint():
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_mf_instruments
        rows = await asyncio.to_thread(get_mf_instruments)
        return {"count": len(rows), "instruments": rows[:500]}
    except Exception as exc:
        raise _err(502, exc)


@router.get("/mf/orders")
async def mf_orders_endpoint():
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_mf_orders
        return {"orders": await asyncio.to_thread(get_mf_orders)}
    except Exception as exc:
        raise _err(502, exc)


@router.get("/mf/orders/{order_id}")
async def mf_order_one(order_id: str):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_mf_order
        return await asyncio.to_thread(get_mf_order, order_id)
    except Exception as exc:
        raise _err(502, exc)


@router.post("/mf/orders")
async def mf_place_order_endpoint(body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        from crawler.zerodha_kite_lib import place_mf_order
        order_id = await asyncio.to_thread(
            place_mf_order,
            tradingsymbol    = body["tradingsymbol"],
            transaction_type = body.get("transaction_type", "BUY"),
            amount           = body.get("amount"),
            quantity         = body.get("quantity"),
            tag              = body.get("tag"),
        )
        return {"order_id": order_id}
    except Exception as exc:
        raise _err(502, exc)


@router.delete("/mf/orders/{order_id}")
async def mf_cancel_order(order_id: str):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import cancel_mf_order
        return await asyncio.to_thread(cancel_mf_order, order_id)
    except Exception as exc:
        raise _err(502, exc)


@router.get("/mf/holdings")
async def mf_holdings_endpoint():
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_mf_holdings
        return {"holdings": await asyncio.to_thread(get_mf_holdings)}
    except Exception as exc:
        raise _err(502, exc)


@router.get("/mf/sips")
async def mf_sips_endpoint():
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_mf_sips
        return {"sips": await asyncio.to_thread(get_mf_sips)}
    except Exception as exc:
        raise _err(502, exc)


@router.get("/mf/sips/{sip_id}")
async def mf_sip_one(sip_id: str):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_mf_sip
        return await asyncio.to_thread(get_mf_sip, sip_id)
    except Exception as exc:
        raise _err(502, exc)


@router.post("/mf/sips")
async def mf_place_sip(body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        from crawler.zerodha_kite_lib import place_mf_sip
        return await asyncio.to_thread(
            place_mf_sip,
            tradingsymbol  = body["tradingsymbol"],
            amount         = float(body["amount"]),
            instalments    = int(body["instalments"]),
            frequency      = body.get("frequency", "monthly"),
            initial_amount = body.get("initial_amount"),
            instalment_day = body.get("instalment_day"),
            tag            = body.get("tag"),
        )
    except Exception as exc:
        raise _err(502, exc)


@router.put("/mf/sips/{sip_id}")
async def mf_modify_sip(sip_id: str, body: dict):
    _need_connection()
    if settings.ZERODHA_PAPER_MODE:
        raise HTTPException(status_code=403, detail="ZERODHA_PAPER_MODE is True")
    try:
        from crawler.zerodha_kite_lib import modify_mf_sip
        return await asyncio.to_thread(
            modify_mf_sip,
            sip_id,
            amount=body.get("amount"),
            status=body.get("status"),
            instalments=body.get("instalments"),
            frequency=body.get("frequency"),
            instalment_day=body.get("instalment_day"),
        )
    except Exception as exc:
        raise _err(502, exc)


@router.delete("/mf/sips/{sip_id}")
async def mf_delete_sip(sip_id: str):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import cancel_mf_sip
        return await asyncio.to_thread(cancel_mf_sip, sip_id)
    except Exception as exc:
        raise _err(502, exc)


# ── Alerts ───────────────────────────────────────────────────────────────────

@router.get("/alerts")
async def alerts_list():
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import get_alerts
        return {"alerts": await asyncio.to_thread(get_alerts)}
    except Exception as exc:
        return {"alerts": [], "error": str(exc)}


@router.post("/alerts")
async def alerts_create(body: dict):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import place_alert
        return await asyncio.to_thread(place_alert, **body)
    except Exception as exc:
        raise _err(502, exc)


@router.put("/alerts/{alert_id}")
async def alerts_modify(alert_id: str, body: dict):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import modify_alert
        return await asyncio.to_thread(modify_alert, alert_id, **body)
    except Exception as exc:
        raise _err(502, exc)


@router.delete("/alerts/{alert_id}")
async def alerts_delete(alert_id: str):
    _need_connection()
    try:
        from crawler.zerodha_kite_lib import delete_alert
        return await asyncio.to_thread(delete_alert, alert_id)
    except Exception as exc:
        raise _err(502, exc)
