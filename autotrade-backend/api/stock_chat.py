# AI Stock Chat API endpoints — streaming SSE + non-streaming.

import json as _json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from engine.stock_chat import process_chat_message
from engine.stock_context_builder import resolve_symbol, build_stock_context, SYMBOL_ALIASES
from crawler.live_prices import PRICE_CACHE
from sqlalchemy import text
from utils.llm import call_llm_chat, call_llm_chat_stream, get_last_reasoning, log_llm_reasoning

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


# ── POST /stream — SSE streaming endpoint ────────────────────────────────────

@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    session: AsyncSession = Depends(get_db),
):
    """Stream a response from Avishk via Server-Sent Events.

    Event types:
      - reasoning: model's chain-of-thought (gpt-oss reasoning tokens)
      - content:   the actual answer (streamed token-by-token)
      - meta:      contexts, intent, symbols (sent once, before done)
      - error:     error message
      - done:      stream complete
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    async def sse_generator():
        from engine.stock_chat import (
            detect_intent, _extract_symbols, build_system_prompt,
            format_context_for_llm, generate_no_ai_response,
        )
        from engine.stock_context_builder import resolve_symbol, build_stock_context

        history = [{"role": m.role, "content": m.content} for m in req.history]

        # Step 1: Intent + symbol detection
        intent_data = detect_intent(req.message)
        raw_symbols = _extract_symbols(req.message)
        resolved = []
        for s in raw_symbols[:2]:
            r = resolve_symbol(s)
            if r:
                resolved.append(r)

        # Step 2: Build context
        contexts = {}
        for sym in resolved[:2]:
            try:
                contexts[sym] = await build_stock_context(sym, session, intent_data["timeframe"])
            except Exception as exc:
                from utils.logger import logger
                logger.warning("Context build failed for %s: %s", sym, exc)

        context_text = ""
        for ctx in contexts.values():
            context_text += format_context_for_llm(ctx) + "\n"

        # Step 3: Build LLM messages
        llm_messages = [{"role": "system", "content": build_system_prompt()}]
        llm_messages.extend(history[-6:])

        if context_text.strip():
            full_user_msg = f"{context_text}\nUser Question: {req.message}"
        else:
            full_user_msg = req.message

        llm_messages.append({"role": "user", "content": full_user_msg})

        # Step 4: Stream via gpt-oss-120b
        from utils.config import settings
        _llm_up = getattr(settings, "mantle_available", False)

        if not _llm_up:
            # No LLM — send rule-based response as a single content event
            reply = generate_no_ai_response(req.message, contexts)
            yield f"event: content\ndata: {_json.dumps({'text': reply})}\n\n"
            yield f"event: meta\ndata: {_json.dumps({'contexts': _serialize_contexts(contexts), 'intent': intent_data['intent'], 'symbols': list(contexts.keys()), 'source': 'rule_based'})}\n\n"
            yield f"event: done\ndata: {{}}\n\n"
            return

        reasoning_parts = []
        content_parts = []

        async for chunk in call_llm_chat_stream(
            llm_messages, max_tokens=1200, temperature=0.4, timeout=90.0,
        ):
            chunk_type = chunk.get("type", "")
            chunk_text = chunk.get("text", "")

            if chunk_type == "reasoning":
                reasoning_parts.append(chunk_text)
                yield f"event: reasoning\ndata: {_json.dumps({'text': chunk_text})}\n\n"
            elif chunk_type == "content":
                content_parts.append(chunk_text)
                yield f"event: content\ndata: {_json.dumps({'text': chunk_text})}\n\n"
            elif chunk_type == "error":
                yield f"event: error\ndata: {_json.dumps({'text': chunk_text})}\n\n"
            elif chunk_type == "done":
                pass  # handled below

        # Persist reasoning to DB (best-effort)
        full_content = "".join(content_parts).strip()
        full_reasoning = "".join(reasoning_parts).strip()
        if full_content or full_reasoning:
            try:
                prompt_summary = req.message[:2000]
                await log_llm_reasoning(
                    source="chat_stream",
                    symbol=(resolved[0] if resolved else None),
                    prompt=prompt_summary,
                    content=full_content,
                    reasoning=full_reasoning,
                    model=settings.MANTLE_MODEL,
                )
            except Exception:
                pass

        # Send metadata + done
        yield f"event: meta\ndata: {_json.dumps({'contexts': _serialize_contexts(contexts), 'intent': intent_data['intent'], 'symbols': list(contexts.keys()), 'source': 'llm'})}\n\n"
        yield f"event: done\ndata: {{}}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _serialize_contexts(contexts: dict) -> dict:
    """Make contexts JSON-serializable (strip non-serializable objects)."""
    import copy
    try:
        safe = {}
        for sym, ctx in contexts.items():
            if isinstance(ctx, dict):
                safe[sym] = {}
                for k, v in ctx.items():
                    try:
                        _json.dumps(v)
                        safe[sym][k] = v
                    except (TypeError, ValueError):
                        safe[sym][k] = str(v)
            else:
                safe[sym] = str(ctx)
        return safe
    except Exception:
        return {}


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


# ── GET /predict-chart/{symbol} — with optional SSE streaming ────────────────

@router.get("/predict-chart/{symbol:path}")
async def predict_chart(
    symbol: str,
    stream: bool = Query(False, description="Stream the response as SSE"),
    session: AsyncSession = Depends(get_db),
):
    """Predict next candle and price movement using gpt-oss-120b with reasoning."""
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
        if stream:
            async def _empty():
                yield f"event: content\ndata: {_json.dumps({'text': 'Not enough historical candle data available for prediction.'})}\n\n"
                yield f"event: done\ndata: {{}}\n\n"
            return StreamingResponse(_empty(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        return {"prediction": "Not enough historical candle data available for prediction."}

    # Format data for LLM (chronological order)
    rows.reverse()

    # Deduplicate by date
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

    if stream:
        async def _stream_predict():
            async for chunk in call_llm_chat_stream(messages, max_tokens=4000):
                chunk_type = chunk.get("type", "")
                chunk_text = chunk.get("text", "")
                if chunk_type in ("reasoning", "content", "error"):
                    yield f"event: {chunk_type}\ndata: {_json.dumps({'text': chunk_text})}\n\n"
                elif chunk_type == "done":
                    yield f"event: done\ndata: {{}}\n\n"
        return StreamingResponse(_stream_predict(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Non-streaming path
    try:
        llm_response = await call_llm_chat(messages, max_tokens=4000)
        reasoning = get_last_reasoning()
    except Exception as e:
        llm_response = f"Failed to generate AI prediction: {str(e)}"
        reasoning = None

    if not llm_response:
        llm_response = "Prediction is currently unavailable. Please try again in a few minutes."

    return {"prediction": llm_response, "reasoning": reasoning}
