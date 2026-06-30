"""Upstox data API — OAuth login + all data endpoints.

Auth flow:
  GET  /api/v1/upstox/login       → returns Upstox OAuth URL (open in browser)
  GET  /api/v1/upstox/callback    → Upstox redirects here with ?code=
  GET  /api/v1/upstox/status      → token present / not present

Data endpoints (all require valid UPSTOX_ACCESS_TOKEN):
  GET  /api/v1/upstox/news/{symbol}
  GET  /api/v1/upstox/profile/{symbol}
  GET  /api/v1/upstox/financials/{symbol}          (P&L + balance sheet + cashflow)
  GET  /api/v1/upstox/ratios/{symbol}
  GET  /api/v1/upstox/shareholding/{symbol}
  GET  /api/v1/upstox/corporate-actions/{symbol}
  GET  /api/v1/upstox/competitors/{symbol}
  GET  /api/v1/upstox/overview/{symbol}            (all-in-one for stock detail page)

Cross-check (Zerodha primary, Upstox validation):
  GET  /api/v1/upstox/ltp/{symbol}
  GET  /api/v1/upstox/historical/{symbol}
  GET  /api/v1/upstox/option-chain/{symbol}
"""
import asyncio

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, HTMLResponse

from crawler.upstox_data import (
    get_auth_url, exchange_code_for_token,
    get_news, get_company_profile,
    get_income_statement, get_balance_sheet, get_cash_flow,
    get_key_ratios, get_shareholding, get_corporate_actions, get_competitors,
    get_ltp, get_historical, get_option_chain, get_market_intel,
)
from utils.config import settings
from utils.logger import logger

router = APIRouter(tags=["upstox"])


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.get("/login", summary="Get Upstox OAuth URL — open in browser to authenticate")
async def upstox_login():
    url = get_auth_url()
    return {
        "auth_url": url,
        "instructions": "Open auth_url in your browser, log in to Upstox, and you'll be redirected back automatically.",
    }


@router.get("/callback", summary="Upstox OAuth callback — exchanges code for access token")
async def upstox_callback(code: str = ""):
    if not code:
        return HTMLResponse("<h2>Error: no code received from Upstox</h2>", status_code=400)
    try:
        token = await exchange_code_for_token(code)
        return HTMLResponse(
            f"<h2>Upstox authenticated!</h2>"
            f"<p>Access token saved. You can close this tab.</p>"
            f"<p><small>Token: {token[:12]}…</small></p>"
        )
    except Exception as e:
        logger.error(f"[upstox/callback] {e}")
        return HTMLResponse(f"<h2>Auth failed: {e}</h2>", status_code=500)


@router.get("/status", summary="Check Upstox connection status")
async def upstox_status():
    return {
        "api_key_set":    settings.upstox_available,
        "authenticated":  settings.upstox_authenticated,
        "token_preview":  settings.UPSTOX_ACCESS_TOKEN[:12] + "…" if settings.UPSTOX_ACCESS_TOKEN else None,
        "login_url":      "/api/v1/upstox/login",
    }


# ── News ──────────────────────────────────────────────────────────────────────

@router.get("/news/{symbol}", summary="Stock-specific news via Upstox News API")
async def upstox_news(symbol: str, limit: int = 15):
    articles = await get_news(symbol.upper(), limit)
    return {"symbol": symbol.upper(), "count": len(articles), "articles": articles}


# ── Fundamentals ──────────────────────────────────────────────────────────────

@router.get("/profile/{symbol}", summary="Company overview, sector, description")
async def upstox_profile(symbol: str):
    return await get_company_profile(symbol.upper())


@router.get("/financials/{symbol}", summary="P&L, Balance Sheet, Cash Flow (combined)")
async def upstox_financials(symbol: str, period: str = "annual"):
    sym = symbol.upper()
    pl, bs, cf = await asyncio.gather(
        get_income_statement(sym, period),
        get_balance_sheet(sym, period),
        get_cash_flow(sym, period),
    )
    return {"symbol": sym, "period": period, "income_statement": pl, "balance_sheet": bs, "cash_flow": cf}


@router.get("/ratios/{symbol}", summary="Key financial ratios: PE, ROE, ROCE, D/E …")
async def upstox_ratios(symbol: str):
    return await get_key_ratios(symbol.upper())


@router.get("/shareholding/{symbol}", summary="Promoter %, FII %, DII %, Public %")
async def upstox_shareholding(symbol: str):
    return await get_shareholding(symbol.upper())


@router.get("/corporate-actions/{symbol}", summary="Dividends, splits, bonuses, buybacks")
async def upstox_corporate_actions(symbol: str):
    return await get_corporate_actions(symbol.upper())


@router.get("/competitors/{symbol}", summary="Peer / competitor comparison")
async def upstox_competitors(symbol: str):
    return await get_competitors(symbol.upper())


# ── All-in-one overview (for Stock Detail page) ───────────────────────────────

@router.get("/overview/{symbol}", summary="All Upstox data in one call — for Stock Detail page")
async def upstox_overview(symbol: str):
    """Single endpoint that powers the Stock Detail page.
    Fires all Upstox data calls in parallel and returns a unified payload.
    On any partial failure the field is null (never raises 500).
    """
    sym = symbol.upper()

    import asyncio as _asyncio

    async def safe(coro):
        try:
            return await coro
        except Exception as e:
            logger.debug(f"[upstox/overview] {sym} partial failure: {e}")
            return None

    (
        profile, news, income, balance, cashflow,
        ratios, shareholding, corp_actions, competitors, intel,
    ) = await _asyncio.gather(
        safe(get_company_profile(sym)),
        safe(get_news(sym, 10)),
        safe(get_income_statement(sym)),
        safe(get_balance_sheet(sym)),
        safe(get_cash_flow(sym)),
        safe(get_key_ratios(sym)),
        safe(get_shareholding(sym)),
        safe(get_corporate_actions(sym)),
        safe(get_competitors(sym)),
        safe(get_market_intel(sym)),
    )

    return {
        "symbol":            sym,
        "source":            "upstox",
        "profile":           profile,
        "news":              news,
        "income_statement":  income,
        "balance_sheet":     balance,
        "cash_flow":         cashflow,
        "key_ratios":        ratios,
        "shareholding":      shareholding,
        "corporate_actions": corp_actions,
        "competitors":       competitors,
        "market_intel":      intel,
    }


# ── Cross-check endpoints ─────────────────────────────────────────────────────

@router.get("/ltp/{symbol}", summary="Live price from Upstox — cross-check vs Zerodha")
async def upstox_ltp(symbol: str):
    ltp = await get_ltp(symbol.upper())
    return {"symbol": symbol.upper(), "upstox_ltp": ltp, "source": "upstox"}


@router.get("/historical/{symbol}", summary="OHLCV candles from Upstox — Zerodha gap-fill")
async def upstox_historical(
    symbol: str,
    interval: str = "day",
    from_date: str | None = None,
    to_date: str | None = None,
):
    candles = await get_historical(symbol.upper(), interval, from_date, to_date)
    return {"symbol": symbol.upper(), "interval": interval, "candles": candles, "source": "upstox"}


@router.get("/option-chain/{symbol}", summary="Option chain OI from Upstox — cross-check Zerodha")
async def upstox_option_chain(symbol: str, expiry: str | None = None):
    data = await get_option_chain(symbol.upper(), expiry)
    return {"symbol": symbol.upper(), "expiry": expiry, "data": data, "source": "upstox"}
