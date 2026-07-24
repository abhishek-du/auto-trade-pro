import asyncio
import time as _time
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import OpenPosition, NewsItem, MasterIntelligenceScore, FundamentalData
from utils.logger import logger
from utils.llm import call_llm_chat
from engine.agent.decision_engine import _parse_first_json
from paper_trading.trade_simulator import close_paper_trade
from datetime import datetime, timedelta

# ── Guardrails (2026-07-22 post-mortem) ───────────────────────────────────────
# Root cause of that day's 0%-win session: this manager ran EVERY 60s per
# position (india_trade_loop cadence) with a prompt biased toward tightening
# ("trail the SL up... if struggling, tighten the SL") and NO constraints on
# the LLM's output. Each individual update looked locally reasonable, but
# iterated every minute the SL ratcheted to (or past) the entry price within
# 5-15 minutes on ALL seven positions — TVSMOTOR SL landed exactly AT entry,
# NESTLEIND/HINDUNILVR SL ABOVE entry (long positions!), TATACHEM ended with
# SL == TP, PARAS with TP BELOW entry (a config that cannot exit at a profit).
# Normal intraday noise then stopped every position out within minutes, and
# ~₹250/round-trip fees turned even above-entry exits into net losses.
#
# The deterministic clamp below (clamp_sl_tp) now bounds every LLM proposal;
# the LLM keeps its judgment role, the arithmetic keeps the discipline.

# Fix 2: manage positions every 5 minutes, not every 60s trade-loop tick.
# SL/TP HIT detection stays at 60s in update_positions_with_current_prices()
# — this gate only slows how often levels are MOVED, not how fast they fire.
_MANAGE_INTERVAL_SEC = 300
_last_manage_ts: float = 0.0

# SL must stay at least this fraction of current price away from current price
# (the "razor-thin stop" floor). 0.75% ≈ one ordinary 5-minute swing on a
# liquid NSE large-cap — anything tighter is guaranteed noise-stop.
_MIN_SL_GAP_PCT = 0.0075
# A single update may tighten the SL by at most this fraction of current price
# — caps the per-minute ratchet compounding even if every update tightens.
_MAX_SL_STEP_PCT = 0.005
# SL may cross to the profitable side of entry ONLY once unrealised profit
# exceeds this — "breakeven stop" earned by real movement, not by 10 minutes
# of drift. 1% also clears the ~0.3% round-trip fee drag with margin.
_BREAKEVEN_MIN_PROFIT_PCT = 1.0
# TP must stay at least this far on the profitable side of entry (fee floor).
_MIN_TP_EDGE_PCT = 0.005


def clamp_sl_tp(
    direction: str, entry: float, current: float,
    cur_sl: float, cur_tp: float, new_sl: float, new_tp: float,
    unrealised_pct: float, action: str = "ADJUST",
) -> tuple[float, float, list[str]]:
    """Deterministically bound an LLM-proposed SL/TP update. Returns
    (final_sl, final_tp, clamp_notes) — notes are empty when the proposal
    passed unmodified.

    action == "EXIT" bypasses the clamps by design: the prompt's documented
    exit mechanism is "set both SL and TP to current price", which the floor
    rules would otherwise forbid. Anything else is treated as ADJUST.
    """
    if action == "EXIT":
        return new_sl, new_tp, []

    notes: list[str] = []
    min_gap = _MIN_SL_GAP_PCT * current
    step_cap = _MAX_SL_STEP_PCT * current
    profit_ok = unrealised_pct >= _BREAKEVEN_MIN_PROFIT_PCT

    if direction == "BUY":
        ceiling = current - min_gap   # SL may never be proposed closer to current than this
        cur_sl_is_safe = cur_sl <= ceiling

        candidate = min(new_sl, ceiling)
        if candidate < new_sl - 1e-9:
            notes.append(f"SL floored {min_gap:.2f} below current (was {new_sl:.2f})")

        if cur_sl_is_safe:
            step_ceiling = cur_sl + step_cap
            if candidate > step_ceiling:
                candidate = step_ceiling
                notes.append(f"SL step capped at +{step_cap:.2f}/update (was {new_sl:.2f})")
        # else: cur_sl itself already violates the min-gap floor (a stale
        # unsafe stop) -- pulling it back to `ceiling` is a correction, not
        # a ratchet, so the step cap does not apply to this one move.

        if not profit_ok and candidate >= entry:
            candidate = min(candidate, entry - min_gap)
            notes.append(
                f"breakeven SL denied (profit {unrealised_pct:.2f}% < {_BREAKEVEN_MIN_PROFIT_PCT}%)"
            )

        if cur_sl_is_safe and candidate < cur_sl:
            # Never loosen an already-safe stop; if the rules above pulled the
            # candidate below the current SL, keep the current SL as-is.
            notes.append(f"SL loosening denied (kept {cur_sl:.2f})")
            candidate = cur_sl
        final_sl = candidate

        final_tp = new_tp
        tp_floor = max(entry * (1 + _MIN_TP_EDGE_PCT), current + min_gap)
        if final_tp < tp_floor:
            notes.append(f"TP floored at {tp_floor:.2f} (was {new_tp:.2f})")
            final_tp = tp_floor
    else:  # SELL
        floor = current + min_gap   # SL may never be proposed closer to current than this
        cur_sl_is_safe = cur_sl >= floor

        candidate = max(new_sl, floor)
        if candidate > new_sl + 1e-9:
            notes.append(f"SL floored {min_gap:.2f} above current (was {new_sl:.2f})")

        if cur_sl_is_safe:
            step_floor = cur_sl - step_cap
            if candidate < step_floor:
                candidate = step_floor
                notes.append(f"SL step capped at -{step_cap:.2f}/update (was {new_sl:.2f})")

        if not profit_ok and candidate <= entry:
            candidate = max(candidate, entry + min_gap)
            notes.append(
                f"breakeven SL denied (profit {unrealised_pct:.2f}% < {_BREAKEVEN_MIN_PROFIT_PCT}%)"
            )

        if cur_sl_is_safe and candidate > cur_sl:
            notes.append(f"SL loosening denied (kept {cur_sl:.2f})")
            candidate = cur_sl
        final_sl = candidate

        final_tp = new_tp
        tp_ceiling = min(entry * (1 - _MIN_TP_EDGE_PCT), current - min_gap)
        if final_tp > tp_ceiling:
            notes.append(f"TP capped at {tp_ceiling:.2f} (was {new_tp:.2f})")
            final_tp = tp_ceiling

    return final_sl, final_tp, notes


async def llm_dynamic_sl_tp(session: AsyncSession) -> None:
    """
    Dynamically analyze open positions and update Stop Loss & Take Profit based on
    LLM reasoning. This makes the agent manage trades actively like a human expert,
    keeping a "peni nazar" (sharp eye) on price action and news.

    Every LLM proposal is passed through clamp_sl_tp() before being applied —
    see the guardrail block at the top of this module for the 2026-07-22
    post-mortem that made this non-negotiable.
    """
    global _last_manage_ts
    now = _time.monotonic()
    if now - _last_manage_ts < _MANAGE_INTERVAL_SEC:
        return
    _last_manage_ts = now
    from sqlalchemy.orm import selectinload
    positions = (await session.execute(select(OpenPosition).options(selectinload(OpenPosition.trade)))).scalars().all()
    if not positions:
        return
        
    # Get latest news for context
    since = datetime.utcnow() - timedelta(minutes=60)
    news = (await session.execute(
        select(NewsItem.headline).where(NewsItem.crawled_at > since).order_by(NewsItem.crawled_at.desc()).limit(15)
    )).scalars().all()
    
    news_text = "\\n".join([f"- {n}" for n in news])
    
    async def _manage_pos(pos: OpenPosition):
        # Fetch the 7-Hub Master Score for this symbol
        hub_row = (await session.execute(
            select(MasterIntelligenceScore).where(MasterIntelligenceScore.symbol == pos.symbol).order_by(MasterIntelligenceScore.id.desc()).limit(1)
        )).scalar_one_or_none()
        
        # Fetch deep Fundamental Data
        fund_row = (await session.execute(
            select(FundamentalData).where(FundamentalData.symbol.in_([pos.symbol, pos.symbol.replace(".NS", "")])).limit(1)
        )).scalar_one_or_none()
        
        hub_data = "No Hub Data Available"
        if hub_row:
            hub_data = (f"Master Score: {hub_row.master_score}\\n"
                        f"Technical: {hub_row.technical_score} | Fundamental: {hub_row.fundamental_score}\\n"
                        f"Sector: {hub_row.sector_score} | News: {hub_row.news_score}\\n"
                        f"Options: {hub_row.options_score} | Macro: {hub_row.macro_score} | Earnings: {hub_row.earnings_score}")

        fund_data = "No Deep Fundamental Data Available"
        if fund_row:
            fund_data = (f"PE: {fund_row.pe_ratio} | PB: {fund_row.pb_ratio} | ROE: {fund_row.roe}% | ROCE: {fund_row.roce}%\\n"
                         f"D/E: {fund_row.debt_to_equity} | Mkt Cap: {fund_row.market_cap_cr} Cr\\n"
                         f"FII: {fund_row.fii_holding}% | Promoter: {fund_row.promoter_holding}%\\n"
                         f"Rev Growth 3y: {fund_row.revenue_growth_3yr}% | Profit Growth 3y: {fund_row.profit_growth_3yr}%")

        # Get Live Macro (Nifty/BankNifty) from PRICE_CACHE
        from crawler.live_prices import PRICE_CACHE
        nifty = PRICE_CACHE.get("^NSEI", {})
        banknifty = PRICE_CACHE.get("^NSEBANK", {})
        macro_text = f"NIFTY 50: {nifty.get('change_pct', 0.0)}% | BANKNIFTY: {banknifty.get('change_pct', 0.0)}%"

        prompt = f"""You are an elite, expert human trader managing an open position.
Review the current performance, 7-factor Hub scores, deep fundamental metrics, and recent news.
Decide if we should trail the Stop Loss (SL) up to lock in profits, or adjust the Take Profit (TP) target higher/lower based on momentum.

Stock: {pos.symbol}
Direction: {pos.direction.value}
Entry Price: {pos.entry_price}
Current Price: {pos.current_price}
Current SL: {pos.stop_loss}
Current TP: {pos.take_profit}
Unrealised PnL: {pos.unrealised_pct:.2f}%

[LIVE MACRO CONTEXT]:
{macro_text}

[7-FACTOR HUB SCORE ANALYSIS]:
{hub_data}

[DEEP FUNDAMENTALS (Screener/Financials)]:
{fund_data}

[RECENT GLOBAL/INDIAN NEWS]:
{news_text}

Instructions:
1. Act smartly and cleverly. Do deep reasoning.
2. Combine the 7-Hub scores and News into ONE holistic decision.
3. A stop-loss needs ROOM TO BREATHE — normal intraday noise on a liquid NSE
   stock is ±0.5-1%. Do NOT move the SL within 0.75% of the current price, and
   do NOT move the SL to breakeven unless the position is up at least 1%.
   Most of the time the correct decision is HOLD — no change at all.
4. Only trail the SL up (BUY) / down (SELL) after a REAL move in your favour,
   and never by more than ~0.5% in one update.
5. If a genuine reversal signal or bad news demands getting out NOW, use
   "action": "EXIT" and set both SL and TP to the Current Price.

Respond ONLY with valid JSON:
{{
    "action": "HOLD" or "ADJUST" or "EXIT",
    "new_stop_loss": 105.50,
    "new_take_profit": 115.00,
    "reasoning": "Detailed explanation citing specific 7-Hub scores (e.g. Technical=40, News=50), specific recent news events, and current PnL to justify the dynamic update."
}}
"""
        try:
            resp = await call_llm_chat(
                [{"role": "system", "content": "You are an aggressive portfolio manager maintaining dynamic SL/TP using holistic data."},
                 {"role": "user", "content": prompt}],
                max_tokens=1000, temperature=0.2
            )
            data = _parse_first_json(resp)
            if data and data.get("new_stop_loss") and data.get("new_take_profit"):
                action = str(data.get("action") or "ADJUST").upper()
                if action == "HOLD":
                    return
                if action == "EXIT":
                    # Close directly instead of the old "pin SL/TP to current
                    # price and hope the SL/TP loop notices" trick — that hack
                    # is exactly what let TP go BELOW entry on a BUY (2026-07-22
                    # PARAS incident: clamp_sl_tp's EXIT bypass, by design,
                    # skips the TP floor since the prompt tells the LLM to set
                    # TP = current price, which is below entry once a position
                    # is underwater). Closing here also gives the trade an
                    # honest exit_reason instead of a later SL/TP hit
                    # mislabeling an LLM-driven reversal call as STOP_LOSS/TAKE_PROFIT.
                    try:
                        await close_paper_trade(pos, pos.current_price, "LLM_DYNAMIC_EXIT", session)
                        logger.warning(
                            f"[dynamic_management] {pos.symbol} LLM EXIT signal — "
                            f"closed @ {pos.current_price} | Reason: {data.get('reasoning')}"
                        )
                    except Exception as exc:
                        logger.warning(f"[dynamic_management] {pos.symbol} LLM EXIT close failed: {exc}")
                    return
                new_sl, new_tp, clamp_notes = clamp_sl_tp(
                    direction=pos.direction.value, entry=pos.entry_price,
                    current=pos.current_price, cur_sl=pos.stop_loss, cur_tp=pos.take_profit,
                    new_sl=float(data["new_stop_loss"]), new_tp=float(data["new_take_profit"]),
                    unrealised_pct=float(pos.unrealised_pct or 0.0), action=action,
                )
                if clamp_notes:
                    logger.info(f"[dynamic_management] {pos.symbol} guardrails clamped LLM proposal: {'; '.join(clamp_notes)}")

                # Only update if it actually changed
                if abs(new_sl - pos.stop_loss) > 0.01 or abs(new_tp - pos.take_profit) > 0.01:
                    logger.info(f"[dynamic_management] {pos.symbol} LLM updating SL: {pos.stop_loss}->{new_sl}, TP: {pos.take_profit}->{new_tp} | Reason: {data.get('reasoning')}")
                    pos.stop_loss = new_sl
                    pos.take_profit = new_tp
                    if hasattr(pos, "trade") and pos.trade:
                        pos.trade.stop_loss = new_sl
                        pos.trade.take_profit = new_tp
        except Exception as e:
            logger.debug(f"[dynamic_management] Failed for {pos.symbol}: {e}")

    # Run in parallel for speed
    await asyncio.gather(*[_manage_pos(p) for p in positions])
    await session.commit()
