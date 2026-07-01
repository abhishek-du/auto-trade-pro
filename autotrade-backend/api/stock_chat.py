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
from sqlalchemy import text
from utils.llm import call_llm_chat
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


# ── GET /predict-chart/{symbol} ──────────────────────────────────────────────

@router.get("/predict-chart/{symbol:path}")
async def predict_chart(
    symbol: str,
    session: AsyncSession = Depends(get_db),
):
    """Predict next candle and price movement using LLM based on recent candles."""
    resolved = resolve_symbol(symbol)
    if not resolved:
        resolved = symbol if symbol.endswith(".NS") or symbol.endswith(".BO") else f"{symbol}.NS"

    # Fetch last 30 daily candles
    q = text(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM candles WHERE symbol = :sym AND timeframe = '1d' "
        "ORDER BY timestamp DESC LIMIT 30"
    )
    res = await session.execute(q, {"sym": resolved})
    rows = res.fetchall()

    if not rows:
        return {"prediction": "Not enough historical candle data available for prediction."}

    # Format data for LLM (chronological order)
    rows.reverse()
    
    # Deduplicate by date (fixes issues where yfinance and upstox sync the same day with different time horizons e.g. 00:00 vs 15:30)
    unique_candles = {}
    for r in rows:
        date_str = r.timestamp.strftime("%Y-%m-%d")
        unique_candles[date_str] = r

    data_str = "Date | Open | High | Low | Close | Volume\n"
    for date_str, r in unique_candles.items():
        data_str += f"{date_str} | {r.open:.2f} | {r.high:.2f} | {r.low:.2f} | {r.close:.2f} | {r.volume}\n"

    system_prompt = (
        "You are an expert technical analyst specializing in candlestick patterns and market psychology. "
        "Your task is to analyze the following recent daily OHLCV data for a stock and predict the NEXT candle. "
        "Use standard technical analysis (support/resistance, candlestick formations, volume analysis). "
        "Predict whether the next candle will be bullish or bearish, and explain your reasoning step-by-step in professional English. "
        "CRITICAL: Do NOT repeat the OHLCV data table in your response. Only provide your analysis and prediction. Format nicely with markdown."
    )

    user_prompt = f"Stock Symbol: {resolved}\nHere is the recent daily candle data:\n{data_str}\n\nPredict the next candle."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        llm_response = await call_llm_chat(messages, max_tokens=4000)
    except Exception as e:
        llm_response = f"Failed to generate AI prediction: {str(e)}"

    return {"prediction": llm_response}
