# AI Stock Chat API endpoints.

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from engine.stock_chat import process_chat_message
from engine.stock_context_builder import resolve_symbol, build_stock_context, SYMBOL_ALIASES
from crawler.live_prices import PRICE_CACHE

router = APIRouter(tags=["chat"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatHistoryItem(BaseModel):
    role:    str
    content: str

class ChatRequest(BaseModel):
    message:    str
    history:    list[ChatHistoryItem] = []
    session_id: str | None = None


# ── POST /message ─────────────────────────────────────────────────────────────

@router.post("/message")
async def chat_message(
    req: ChatRequest,
    session: AsyncSession = Depends(get_db),
):
    """Send a message to Avishk, the AI stock analyst."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    history = [{"role": m.role, "content": m.content} for m in req.history]

    try:
        result = await process_chat_message(req.message, history, session)
    except Exception as exc:
        # Never return 500 — always give a useful response
        return {
            "reply":     f"I encountered an error processing your request. Please try again. (Details: {exc})",
            "contexts":  {},
            "intent":    "GENERAL",
            "symbols":   [],
            "source":    "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return result


# ── GET /suggest/{partial} ────────────────────────────────────────────────────

@router.get("/suggest/{partial_symbol:path}")
async def suggest_symbols(partial_symbol: str):
    """Autocomplete stock symbol search from PRICE_CACHE + SYMBOL_ALIASES."""
    q = partial_symbol.strip().upper()
    if len(q) < 1:
        return []

    results: list[dict] = []
    seen: set[str] = set()

    # 1 — PRICE_CACHE hits (live data available)
    for sym, data in PRICE_CACHE.items():
        if q in sym.upper() and sym not in seen:
            seen.add(sym)
            name = sym.replace(".NS", "").replace(".BO", "").replace("^", "")
            results.append({
                "symbol":      sym,
                "display_name": name,
                "price":       data.get("price", 0),
                "change_pct":  data.get("change_pct", 0),
            })
            if len(results) >= 8:
                break

    # 2 — SYMBOL_ALIASES (fill up to 8 if needed)
    if len(results) < 8:
        for alias, full_sym in SYMBOL_ALIASES.items():
            if q in alias and full_sym not in seen:
                seen.add(full_sym)
                price_data = PRICE_CACHE.get(full_sym, {})
                results.append({
                    "symbol":       full_sym,
                    "display_name": alias.title(),
                    "price":        price_data.get("price", 0),
                    "change_pct":   price_data.get("change_pct", 0),
                })
                if len(results) >= 8:
                    break

    return results[:8]


# ── GET /quick-analysis/{symbol} ─────────────────────────────────────────────

@router.get("/quick-analysis/{symbol:path}")
async def quick_analysis(
    symbol: str,
    session: AsyncSession = Depends(get_db),
):
    """Structured analysis for a symbol without the chat interface."""
    resolved = resolve_symbol(symbol)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")

    try:
        ctx = await build_stock_context(resolved, session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return ctx
