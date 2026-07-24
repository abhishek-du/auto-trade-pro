import json
from loguru import logger
from typing import List, Dict

# ── Relationship quality bar (2026-07-22 post-mortem) ─────────────────────────
# Root cause of that day's TATACHEM/LT cascade trades: get_second_order_trades()
# validated only that {ticker, action, reason} existed syntactically -- it
# never checked whether the claimed relationship was real or strong. "Paras
# Defence wins a Madhya Pradesh investment commitment" produced a trade in
# Tata Chemicals with no coherent causal link at all. Separately,
# decision_router.py's authorize_trade_intent() has ALWAYS required
# relationship_type/relationship_strength/company_exposure/market_confirmation
# for any SECOND_ORDER intent (routing to WATCHLIST_ONLY if any are missing --
# see its Phase 2.3 comment), but this function never produced them, so every
# 2nd-order candidate should already have been watchlist-only. It wasn't,
# because the live news_discovery_engine.py process had been running since
# before that gate code existed and never picked it up (fixed separately by
# restarting the process) -- this quality bar is the second, independent
# layer: even once the gate is active, a real relationship_type/strength/
# exposure is what lets a genuinely strong 2nd-order case ever clear
# WATCHLIST_ONLY, and a properly closed-set/thresholded check is what keeps
# a TATACHEM-style non-relationship from being emitted as a candidate at all.
_VALID_RELATIONSHIP_TYPES = frozenset({"SUPPLIER", "CUSTOMER", "COMPETITOR", "SECTOR_PROXY"})
_MIN_RELATIONSHIP_STRENGTH = 0.6
_MIN_COMPANY_EXPOSURE = 0.3


async def get_second_order_trades(primary_ticker: str, headline: str, summary: str, event_sentiment: str) -> List[Dict[str, str]]:
    """
    Dynamically infers 2nd-order beneficiaries or victims across the entire Indian stock market
    (Large, Mid, and Small Cap) using the 120B LLM.

    Returns: [{"ticker": "SONACOMS.NS", "action": "BUY", "reason": "...",
               "relationship_type": "SUPPLIER", "relationship_strength": 0.8,
               "company_exposure": 0.4}]
    Only candidates that pass the closed-set relationship_type check AND both
    quality thresholds are returned -- a vague or weak link is dropped here,
    not left for a downstream gate to catch.
    """
    from utils.llm import call_llm_chat

    ticker = primary_ticker.upper()
    if not ticker.endswith(".NS") and not ticker.endswith(".BO") and not ticker.startswith("CRUDE"):
        ticker += ".NS"

    logger.info(f"🕸️ Dynamically analyzing 2nd-order market effects for {ticker}...")

    system_prompt = (
        "You are an expert Indian equities prop-desk analyst. "
        "Your job is to identify 2nd-order effect trades based on a primary news event. "
        "If a primary company has a major event (e.g., massive order win, FDA approval, crude drop, management fraud), "
        "identify 0 to 3 OTHER Indian stocks (NSE symbols ending in .NS, can be small/mid/large cap) that will be heavily impacted as a DIRECT, CONCRETE consequence. "
        "Be conservative: a shared sector or vague thematic link ('both are Indian companies', 'both benefit from government spending') is NOT enough. "
        "Only propose a link with a specific mechanical relationship: "
        "SUPPLIER (primary company buys a material/component from this stock, or vice versa), "
        "CUSTOMER (primary company sells directly to this stock), "
        "COMPETITOR (this stock directly competes for the same customers/market share as the primary company), or "
        "SECTOR_PROXY (this stock's revenue is dominated by the exact same narrow business line as the primary company, not just the same broad sector). "
        "If you cannot name the concrete mechanism, do not include the stock. "
        "Return ONLY a raw JSON array. DO NOT wrap it in markdown block quotes. If there are no obvious 2nd-order trades, return []. "
        "Format: [{\"ticker\": \"SYMBOL.NS\", \"action\": \"BUY\" or \"SELL\", \"reason\": \"Short explanation naming the concrete mechanism\", "
        "\"relationship_type\": \"SUPPLIER\" or \"CUSTOMER\" or \"COMPETITOR\" or \"SECTOR_PROXY\", "
        "\"relationship_strength\": <0.0-1.0, how directly/certainly this relationship transmits the event's impact>, "
        "\"company_exposure\": <0.0-1.0, what fraction of THIS stock's own business is exposed to that relationship>}]"
    )

    user_prompt = (
        f"Primary Stock: {ticker}\n"
        f"Event Sentiment: {event_sentiment}\n"
        f"Headline: {headline}\n"
        f"Summary: {summary}\n\n"
        f"Output JSON array of 2nd-order trades:"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        response = await call_llm_chat(messages, max_tokens=1000, temperature=0.2)
        # Tolerant extraction (2026-07-24, Nova Pro switch): a strict
        # json.loads() after only trimming markdown fences raises on any
        # stray prose Nova adds despite "ONLY a raw JSON array" in the
        # prompt, silently dropping an otherwise-good candidate via the
        # except-fallback below. See utils/llm.py::extract_json_from_response.
        from utils.llm import extract_json_from_response
        trades = extract_json_from_response(response)
        if trades is None:
            raise ValueError(f"no JSON array found in LLM response: {(response or '')[:200]!r}")

        # Validation: syntactic shape + closed-set relationship_type + quality thresholds.
        valid_trades = []
        if isinstance(trades, list):
            for t in trades:
                if not (isinstance(t, dict) and "ticker" in t and "action" in t and "reason" in t):
                    continue
                if t["ticker"] == ticker or t["action"] not in ("BUY", "SELL"):
                    continue
                rel_type = str(t.get("relationship_type") or "").strip().upper()
                if rel_type not in _VALID_RELATIONSHIP_TYPES:
                    logger.info(f"[sector_graph] {t['ticker']}: dropped -- relationship_type "
                                f"{rel_type!r} not in {sorted(_VALID_RELATIONSHIP_TYPES)}")
                    continue
                try:
                    rel_strength = float(t.get("relationship_strength", 0.0))
                    exposure = float(t.get("company_exposure", 0.0))
                except (TypeError, ValueError):
                    rel_strength = exposure = 0.0
                if rel_strength < _MIN_RELATIONSHIP_STRENGTH or exposure < _MIN_COMPANY_EXPOSURE:
                    logger.info(
                        f"[sector_graph] {t['ticker']}: dropped -- relationship_strength="
                        f"{rel_strength:.2f} (min {_MIN_RELATIONSHIP_STRENGTH}) or company_exposure="
                        f"{exposure:.2f} (min {_MIN_COMPANY_EXPOSURE}) below quality bar"
                    )
                    continue
                t["relationship_type"] = rel_type
                t["relationship_strength"] = rel_strength
                t["company_exposure"] = exposure
                valid_trades.append(t)

        return valid_trades

    except Exception as exc:
        logger.error(f"[sector_graph] Failed to generate dynamic 2nd-order trades for {ticker}: {exc}")
        return []
