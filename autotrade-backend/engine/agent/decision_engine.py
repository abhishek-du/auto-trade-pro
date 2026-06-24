"""Decision Engine — fuses candidate + context into a structured decision.

Reference: trading_agent/decision.py (extended with bear-case check, M12).

Pipeline order:
  1. fetch_hub_candidate()  — regime restriction + conflict detection (hard skips)
  2. DecisionEngine.fuse()  — multiplicative confidence + threshold check + position sizing
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from utils.config import settings
from utils.logger import logger


# Strategies that require MIS (intraday) product per NSE/SEBI rules:
# - Short-selling is only allowed intraday; delivery short is illegal on NSE/BSE
# - MIS positions must be squared off before 3:20 PM IST (Zerodha auto-squareoff)
_MIS_STRATEGIES = {"MEAN_REVERSION_SHORT"}


@dataclass
class AgentDecisionOutput:
    symbol:             str
    action:             str
    confidence:         int
    regime:             str
    strategy:           str
    entry:              float
    stop:               float
    target:             float
    qty:                int
    risk_pct:           float
    risk_reward:        float
    product:            str   = "CNC"   # CNC=delivery positional | MIS=intraday | NRML=F&O
    reasons:            list  = field(default_factory=list)
    macro_bias:         int   = 0
    fund_score:         int   = 0
    fund_grade:         str   = "WATCHLIST"
    ts:                 str   = ""
    master_score:       float | None = None   # raw hub score before confidence calc
    confidence_factors: dict  | None = None   # breakdown for audit log
    # ── F&O fields (EQUITY for cash trades; populated for FUTURE/CE/PE) ────────
    instrument_type:    str   = "EQUITY"        # EQUITY | FUTURE | CE | PE
    underlying_symbol:  str   | None = None     # e.g. "NIFTY" for a NIFTY option
    tradingsymbol:      str   | None = None      # broker NFO symbol, e.g. NIFTY26JAN24500CE
    strike_price:       float | None = None
    option_type:        str   | None = None     # CE | PE
    expiry_date:        str   | None = None     # ISO date string
    lot_size:           int   = 1
    contract_multiplier: float = 1.0
    exchange:           str   = "NSE"           # NSE | NFO

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts or datetime.utcnow().isoformat()
        return d


async def _candidate_context(symbol: str, candidate, decision) -> str:
    """Shared, model-readable summary of a candidate + its 7-factor breakdown.
    Used by all reasoning levels. When Level-4 reflection is on, appends the most
    relevant past lessons so the model learns from the agent's own history."""
    sub = getattr(candidate, "hub_subscores", {}) or {}
    cf  = getattr(decision, "confidence_factors", {}) or {}
    base = (
        f"Symbol {symbol} | Side {decision.action} | Strategy {candidate.strategy}\n"
        f"Regime {decision.regime} | MasterScore {decision.master_score}\n"
        f"Entry {candidate.entry} Stop {candidate.stop} Target {candidate.target} "
        f"R:R {candidate.risk_reward}\n"
        f"7-factor: technical={sub.get('technical')} news={sub.get('news')} "
        f"sector={sub.get('sector')} macro={sub.get('macro')} earnings={sub.get('earnings')} "
        f"fundamental={sub.get('fundamental')} options={sub.get('options')}\n"
        f"Modifiers: news_factor={cf.get('news_factor')} earnings_tone={cf.get('earnings_tone')} "
        f"fii_bias={cf.get('fii_bias')} regime_factor={cf.get('regime_factor')}\n"
        f"Arithmetic confidence {decision.confidence}%"
    )
    # Technical / chart read (candlestick patterns, indicator states, support/
    # resistance, ML direction) so the model reasons over the CHART, not just the
    # numeric factors. Attached upstream by the trade loops; absent → skipped.
    brief = getattr(candidate, "chart_brief", None)
    if brief:
        base += "\nTechnical / chart read:\n" + str(brief)
    try:
        from engine.agent.reflection import get_relevant_lessons
        lessons = await get_relevant_lessons(candidate.strategy, decision.regime, decision.action)
        if lessons:
            base += "\nPast lessons from similar trades:\n" + \
                    "\n".join(f"- {l}" for l in lessons)
    except Exception:
        pass
    return base


def _parse_first_json(resp: str) -> dict | None:
    """Extract the FIRST JSON object from an LLM response, tolerating trailing
    text or extra objects (raw_decode stops after the first complete value)."""
    if not resp:
        return None
    import json as _json
    i = resp.find("{")
    if i < 0:
        return None
    try:
        obj, _end = _json.JSONDecoder().raw_decode(resp[i:])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def llm_reason_candidate(symbol: str, candidate, decision) -> dict | None:
    """Level-1 LLM reasoning: ask the model to reason over a qualified candidate
    (bull case / bear case / biggest risk) and return a structured verdict.

    Returns {verdict, confidence, bull, bear, key_risk} or None on any failure
    (the caller then falls back to the arithmetic decision — fail-open).
    """
    try:
        from utils.llm import call_llm_chat

        sys_prompt = (
            "You are a senior discretionary Indian-equity (NSE) swing trader. Weigh "
            "ALL the evidence given like a human expert would — the 7 fundamental/"
            "sentiment factors (technical, news, sector, macro, earnings, fundamental, "
            "options), the technical/chart read (candlestick patterns, indicator "
            "states, support/resistance, ML next-day forecast), and any past lessons. "
            "Reason briefly about the bull case, the bear case, and the single biggest "
            "risk, then decide TAKE or SKIP. Be skeptical: SKIP when the chart and the "
            "factors disagree, the edge is weak, the regime is unsupportive, or the "
            "risk/reward is poor — do not take a trade just because the score is high. "
            'Respond with ONLY compact JSON: '
            '{"verdict":"TAKE"|"SKIP","confidence":<0-100 int>,'
            '"bull":"<=20 words","bear":"<=20 words","key_risk":"<=12 words"}'
        )
        user_prompt = await _candidate_context(symbol, candidate, decision)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ]
        resp = await call_llm_chat(messages, max_tokens=300, temperature=0.2, groq_fallback=True)
        return _parse_first_json(resp)
    except Exception as exc:
        logger.debug(f"[agent/llm_reason] {symbol} reasoning failed: {exc}")
        return None


async def llm_debate_candidate(symbol: str, candidate, decision) -> dict | None:
    """Level-2 multi-agent debate: a Bull, a Bear and a Risk analyst argue
    independently (in parallel), then a Judge synthesises the verdict.

    Returns {verdict, confidence, bull, bear, key_risk, judge} (plus raw analyst
    notes) or None on failure (caller falls back to arithmetic — fail-open).
    """
    try:
        import asyncio as _aio
        from utils.llm import call_llm_chat

        context = await _candidate_context(symbol, candidate, decision)

        async def _analyst(role: str) -> str:
            msgs = [
                {"role": "system", "content": role},
                {"role": "user",   "content": context},
            ]
            return (await call_llm_chat(msgs, max_tokens=160, temperature=0.3,
                                        groq_fallback=True)) or ""

        bull_role = ("You are a BULL-side NSE equity analyst. In <=40 words, make the "
                     "strongest concrete case FOR taking this long trade — name the catalyst/edge.")
        bear_role = ("You are a BEAR-side NSE equity analyst. In <=40 words, make the "
                     "strongest case AGAINST it — name the most likely way this trade loses.")
        risk_role = ("You are a RISK manager. In <=40 words, judge the risk/reward, regime "
                     "fit and position risk; state plainly whether the risk is acceptable.")

        bull, bear, risk = await _aio.gather(
            _analyst(bull_role), _analyst(bear_role), _analyst(risk_role)
        )
        if not (bull or bear or risk):
            return None

        judge_sys = (
            "You are the head portfolio manager. Weigh the BULL, BEAR and RISK views "
            "and decide TAKE or SKIP. Be skeptical — SKIP when the bear/risk case "
            "outweighs a thin bull case or the factors conflict. "
            'Respond with ONLY compact JSON: '
            '{"verdict":"TAKE"|"SKIP","confidence":<0-100 int>,"bull":"<=20 words",'
            '"bear":"<=20 words","key_risk":"<=12 words","judge":"<=25 words rationale"}'
        )
        judge_user = f"{context}\n\nBULL: {bull}\nBEAR: {bear}\nRISK: {risk}"
        resp = await call_llm_chat(
            [{"role": "system", "content": judge_sys},
             {"role": "user",   "content": judge_user}],
            max_tokens=320, temperature=0.2, groq_fallback=True,
        )
        data = _parse_first_json(resp)
        if not data:
            return None
        data["_panel"] = {"bull": bull[:200], "bear": bear[:200], "risk": risk[:200]}
        return data
    except Exception as exc:
        logger.debug(f"[agent/llm_debate] {symbol} debate failed: {exc}")
        return None


# ── Level-3 agentic tools: the LLM pulls fresh data before deciding ──────────
async def _tool_fundamentals(symbol: str) -> str:
    try:
        from db.database import AsyncSessionLocal
        from sqlalchemy import text as _t
        bare = symbol.replace(".NS", "")
        async with AsyncSessionLocal() as s:
            r = (await s.execute(_t(
                "SELECT pe_ratio,roe,roce,debt_to_equity,revenue_growth_3yr,"
                "profit_growth_3yr,promoter_holding,fundamental_score FROM fundamental_data "
                "WHERE symbol IN (:a,:b) ORDER BY last_updated DESC LIMIT 1"),
                {"a": symbol, "b": bare + ".NS"})).fetchone()
        if not r:
            return "fundamentals: no data"
        return (f"fundamentals: PE={r[0]} ROE={r[1]} ROCE={r[2]} D/E={r[3]} "
                f"rev_growth_3y={r[4]} profit_growth_3y={r[5]} promoter={r[6]}% score={r[7]}")
    except Exception as exc:
        return f"fundamentals: error ({exc})"


async def _tool_news(symbol: str) -> str:
    try:
        from db.database import AsyncSessionLocal
        from sqlalchemy import text as _t
        bare = symbol.replace(".NS", "")
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(_t(
                "SELECT headline,sentiment,score FROM news_items "
                "WHERE headline ILIKE :pat AND published_at > now() - interval '10 days' "
                "ORDER BY published_at DESC LIMIT 3"), {"pat": f"%{bare}%"})).fetchall()
        if not rows:
            return "news: no recent headlines"
        return "news: " + " | ".join(f"[{x[1]}/{x[2]}] {x[0][:90]}" for x in rows)
    except Exception as exc:
        return f"news: error ({exc})"


async def _tool_options(symbol: str) -> str:
    try:
        from db.database import AsyncSessionLocal
        from sqlalchemy import text as _t
        bare = symbol.replace(".NS", "")
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(_t(
                "SELECT option_type, sum(oi) FROM option_contract_snapshots "
                "WHERE underlying IN (:a,:b) AND snapshot_at = "
                "(SELECT max(snapshot_at) FROM option_contract_snapshots WHERE underlying IN (:a,:b)) "
                "GROUP BY option_type"), {"a": bare, "b": symbol})).fetchall()
        if not rows:
            return "options: no chain data"
        oi = {str(k): float(v or 0) for k, v in rows}
        ce, pe = oi.get("CE", 0) or oi.get("call", 0), oi.get("PE", 0) or oi.get("put", 0)
        pcr = round(pe / ce, 2) if ce else None
        return f"options: CE_OI={ce:.0f} PE_OI={pe:.0f} PCR={pcr}"
    except Exception as exc:
        return f"options: error ({exc})"


async def _tool_price_action(symbol: str) -> str:
    try:
        from db.database import AsyncSessionLocal
        from sqlalchemy import text as _t
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(_t(
                "SELECT close FROM candles WHERE symbol=:s AND timeframe='1d' "
                "ORDER BY timestamp DESC LIMIT 20"), {"s": symbol})).fetchall()
        cl = [float(x[0]) for x in rows]
        if len(cl) < 6:
            return "price_action: insufficient history"
        last = cl[0]
        ret5 = round((last / cl[5] - 1) * 100, 2)
        hi20, lo20 = max(cl), min(cl)
        pos = round((last - lo20) / (hi20 - lo20) * 100, 0) if hi20 > lo20 else 50
        return f"price_action: last={last} 5d_return={ret5}% pos_in_20d_range={pos}%"
    except Exception as exc:
        return f"price_action: error ({exc})"


_LLM_TOOLS = {
    "fundamentals": _tool_fundamentals,
    "news":         _tool_news,
    "options":      _tool_options,
    "price_action": _tool_price_action,
}


async def llm_tooluse_candidate(symbol: str, candidate, decision) -> dict | None:
    """Level-3 agentic reasoning: give the LLM tools (news / options / fundamentals
    / price_action) and let it INVESTIGATE before deciding — a ReAct loop. The model
    chooses which tools to call; we execute and feed results back until it decides
    (or a 4-round cap). Returns {verdict,confidence,bull,bear,key_risk,tools_used}."""
    try:
        from utils.llm import call_llm_chat

        sys_prompt = (
            "You are an NSE swing-trading analyst with TOOLS. Investigate the candidate "
            "before deciding. Available tools: fundamentals, news, options, price_action "
            "(each takes the symbol). Call at most 3 tools, only those that matter.\n"
            'To call a tool, respond ONLY: {"action":"tool","tool":"<name>"}\n'
            'When ready, respond ONLY: {"action":"decide","verdict":"TAKE"|"SKIP",'
            '"confidence":<0-100 int>,"bull":"<your bull case, max 20 words>",'
            '"bear":"<your bear case, max 20 words>","key_risk":"<the single biggest risk, max 12 words>"}\n'
            "Fill bull/bear/key_risk with real analysis, not placeholders. "
            "Be skeptical — SKIP on weak/conflicting evidence."
        )
        ctx0 = await _candidate_context(symbol, candidate, decision)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": ctx0},
        ]
        used: list[str] = []
        for _ in range(4):  # max 4 LLM rounds (≤3 tool calls + a decide)
            resp = await call_llm_chat(messages, max_tokens=220, temperature=0.2, groq_fallback=True)
            step = _parse_first_json(resp)
            if not step:
                return None
            if step.get("action") == "tool" and step.get("tool") in _LLM_TOOLS and len(used) < 3:
                tool = step["tool"]
                result = await _LLM_TOOLS[tool](symbol)
                used.append(tool)
                messages.append({"role": "assistant", "content": resp})
                messages.append({"role": "user", "content": f"TOOL[{tool}] → {result}\nContinue or decide."})
                continue
            if step.get("action") == "decide" or "verdict" in step:
                step["tools_used"] = used
                return step
        return None  # ran out of rounds without deciding
    except Exception as exc:
        logger.debug(f"[agent/llm_tooluse] {symbol} tool-use failed: {exc}")
        return None


async def apply_reasoning_gate(symbol: str, candidate, decision):
    """Level-1 reasoning gate (opt-in via AGENT_LLM_REASONING_ENABLED).

    On a candidate that has already cleared the arithmetic threshold, let the LLM
    confirm/veto and blend confidence. Returns (decision_or_None, reject_reason).
    Fail-open: if the gate is disabled or the LLM is unavailable, the arithmetic
    decision passes through unchanged so trading never blocks on the LLM.
    """
    if not getattr(settings, "AGENT_LLM_REASONING_ENABLED", False):
        return decision, None

    # Dispatch by level: tool-use (L3) > debate (L2) > single-pass reasoning (L1).
    if getattr(settings, "AGENT_LLM_TOOLUSE_ENABLED", False):
        mode, data = "tooluse", await llm_tooluse_candidate(symbol, candidate, decision)
    elif getattr(settings, "AGENT_LLM_DEBATE_ENABLED", False):
        mode, data = "debate", await llm_debate_candidate(symbol, candidate, decision)
    else:
        mode, data = "reason", await llm_reason_candidate(symbol, candidate, decision)
    if not data:
        candidate.reasons.append(f"llm_{mode}:unavailable→arithmetic")
        return decision, None

    arith_conf = decision.confidence   # snapshot BEFORE any blend
    verdict  = str(data.get("verdict", "TAKE")).upper()
    llm_conf = data.get("confidence")
    key_risk = str(data.get("key_risk", ""))[:80]
    bull     = str(data.get("bull", ""))[:120]
    bear     = str(data.get("bear", ""))[:120]

    record = {
        "mode": mode, "verdict": verdict, "confidence": llm_conf,
        "bull": bull, "bear": bear, "key_risk": key_risk,
    }
    if data.get("judge"):
        record["judge"] = str(data["judge"])[:160]
    if data.get("_panel"):
        record["panel"] = data["_panel"]
    if data.get("tools_used"):
        record["tools_used"] = data["tools_used"]
    try:
        decision.confidence_factors["llm_reasoning"] = record
    except Exception:
        pass
    candidate.reasons.append(f"llm_{mode}:{verdict} conf={llm_conf} risk={key_risk}")

    # Shadow mode: log the verdict but DON'T act on it — the trade proceeds either
    # way, so would-be-SKIPs still get real outcomes for an unbiased A/B. `taken` is
    # True (the trade IS taken); llm_verdict still records what the gate WOULD do.
    shadow = bool(getattr(settings, "AGENT_LLM_SHADOW_MODE", False))
    if shadow:
        record["shadow"] = True
        candidate.reasons.append("llm_shadow:logged_not_enforced")
        await _log_verdict(symbol, candidate, decision, mode, arith_conf,
                           verdict, llm_conf, decision.confidence, taken=True, record=record)
        return decision, None

    if verdict == "SKIP":
        await _log_verdict(symbol, candidate, decision, mode, arith_conf,
                           verdict, llm_conf, None, taken=False, record=record)
        return None, f"llm_reasoning_skip:{key_risk or 'weak_edge'}"

    # TAKE → blend arithmetic + LLM confidence (defensive parse).
    try:
        lc = int(llm_conf)
        if 0 <= lc <= 100:
            blended = int(round((decision.confidence + lc) / 2))
            decision.confidence = blended
            decision.confidence_factors["final_confidence_blended"] = blended
    except Exception:
        pass
    await _log_verdict(symbol, candidate, decision, mode, arith_conf,
                       verdict, llm_conf, decision.confidence, taken=True, record=record)
    return decision, None


async def _log_verdict(symbol, candidate, decision, mode, arith_conf,
                       verdict, llm_conf, final_conf, taken: bool, record: dict) -> None:
    """Append one row to reasoning_verdicts (self-contained session, fail-safe) so
    EVERY gate verdict — taken or skipped, either execution path — is captured and
    later joinable to the trade outcome. Never raises into the decision flow."""
    try:
        from db.database import AsyncSessionLocal
        from db.models import ReasoningVerdict

        def _int(x):
            try: return int(x)
            except Exception: return None

        # 7-factor Hub breakdown + modifiers at decision time → per-factor outcome
        # attribution later. Floats kept as-is; tone/bias kept for grouping.
        sub = getattr(candidate, "hub_subscores", {}) or {}
        cf  = getattr(decision, "confidence_factors", {}) or {}
        factors = {
            "technical":   sub.get("technical"),   "news":        sub.get("news"),
            "sector":      sub.get("sector"),      "macro":       sub.get("macro"),
            "earnings":    sub.get("earnings"),    "fundamental": sub.get("fundamental"),
            "options":     sub.get("options"),
            "news_factor": cf.get("news_factor"),  "earnings_tone": cf.get("earnings_tone"),
            "fii_bias":    cf.get("fii_bias"),     "regime_factor": cf.get("regime_factor"),
        }

        async with AsyncSessionLocal() as s:
            s.add(ReasoningVerdict(
                symbol=symbol, mode=mode,
                side=getattr(decision, "action", None) or getattr(candidate, "side", None),
                strategy=getattr(candidate, "strategy", None),
                regime=getattr(decision, "regime", None),
                entry=getattr(candidate, "entry", None),
                arith_confidence=_int(arith_conf),
                llm_verdict=verdict, llm_confidence=_int(llm_conf),
                final_confidence=_int(final_conf), taken=taken,
                key_risk=str(record.get("key_risk", ""))[:120] or None,
                detail=record, factors=factors,
            ))
            await s.commit()
    except Exception as exc:
        logger.debug(f"[agent/verdict_log] {symbol} log failed: {exc}")


class DecisionEngine:

    def fuse(
        self,
        symbol: str,
        candidate,
        regime: str,
        macro_bias: int,
        fund_score: int,
        fund_grade: str,
        equity: float,
    ) -> tuple["AgentDecisionOutput | None", "str | None"]:
        """Return (decision, None) on success or (None, reject_reason) when filtered."""

        if candidate is None:
            return None, "no_candidate"

        from engine.agent.risk_manager import capital_utilization_size

        # Apply regime-based position size reduction flag (set by fetch_hub_candidate)
        size_factor = getattr(candidate, "size_factor", 1.0)

        # Capital-utilization sizing: deploy toward a conviction-weighted target
        # (so the ₹20L is actually used) while keeping the per-trade risk guard,
        # the 20% per-position cap, and the cash buffer. `deployed_notional` is
        # passed by the caller so the cash-buffer room is respected portfolio-wide.
        deployed_notional = getattr(candidate, "deployed_notional", 0.0)
        conviction = abs(getattr(candidate, "master_score", None) or candidate.confidence)
        live_vix: float = 15.0
        try:
            from crawler.live_prices import PRICE_CACHE
            live_vix = float(PRICE_CACHE.get("^INDIAVIX", {}).get("price", 15) or 15)
        except Exception:
            pass
        qty, _size_reason = capital_utilization_size(
            equity, conviction, candidate.entry, candidate.stop,
            deployed_notional, size_factor=size_factor, vix=live_vix,
        )
        if qty <= 0:
            return None, f"qty_zero:{_size_reason}"
        from engine.agent.risk_manager import vix_size_factor as _vix_sf
        _vsf = _vix_sf(live_vix)
        if _vsf < 1.0:
            candidate.reasons.append(f"vix_scaled:{live_vix:.1f}→sf={_vsf:.2f}")

        risk_amt = qty * abs(candidate.entry - candidate.stop)
        risk_pct = risk_amt / max(equity, 1)

        # Varsity M12 — Innerworth: always check the opposing view
        bear = self._bear_case(candidate, regime, macro_bias)
        if bear:
            candidate.reasons.append(f"bear_case:{bear}")

        # ── Conflict detection ────────────────────────────────────────────────
        # Hard skips when hub context disagrees with the BUY signal.
        # Checked BEFORE confidence calculation so we never emit a low-conf order.
        if candidate.side == "BUY":
            conflict_reason = self._check_conflicts(symbol, candidate)
            if conflict_reason:
                candidate.reasons.append(conflict_reason)
                logger.info(
                    f"[agent/decision] {symbol} CONFLICT SKIP — {conflict_reason}"
                )
                return None, conflict_reason

        # ── Multiplicative confidence ─────────────────────────────────────────
        # Replaces the old additive hub modifier.
        bare = symbol.replace(".NS", "")
        raw_master = getattr(candidate, "master_score", None)
        if raw_master is not None:
            signal_strength = abs(raw_master) / 100.0
        else:
            signal_strength = candidate.confidence / 100.0

        regime_factor    = self._regime_factor(candidate.side, regime)
        news_factor      = 1.0
        earnings_factor  = 1.0
        fii_factor       = 1.0
        news_raw         = 0.0
        earnings_tone    = "NEUTRAL"
        fii_bias_val     = 0

        try:
            from engine import intelligence_hub as hub
            if hub.LAST_NEWS_CONTEXT is not None:
                news_raw    = hub.LAST_NEWS_CONTEXT.scores_by_symbol.get(bare, 0.0)
                news_factor = max(0.5, min(1.5, 1.0 + news_raw * 0.5))

            if hub.LAST_EARNINGS_CONTEXT is not None:
                earnings_tone  = hub.LAST_EARNINGS_CONTEXT.tones_by_symbol.get(bare, "NEUTRAL")
                earnings_bonus = {"OPTIMISTIC": 5, "NEUTRAL": 0, "CAUTIOUS": -10, "NEGATIVE": -20}.get(earnings_tone, 0)
                earnings_factor = max(0.5, min(1.5, 1.0 + earnings_bonus / 100.0))

            if hub.LAST_MACRO_CONTEXT is not None:
                fii_bias_val = hub.LAST_MACRO_CONTEXT.fii_bias
                fii_factor   = max(0.6, min(1.4, 1.0 + fii_bias_val * 0.2))

        except Exception as exc:
            logger.debug(f"[agent/decision] hub factors skipped for {symbol}: {exc}")

        market_support    = regime_factor * news_factor * earnings_factor * fii_factor
        final_confidence  = max(0, min(100, int(signal_strength * market_support * 100)))

        conf_factors = {
            "signal_strength":  round(signal_strength, 4),
            "regime_factor":    round(regime_factor, 4),
            "news_raw":         round(news_raw, 4),
            "news_factor":      round(news_factor, 4),
            "earnings_tone":    earnings_tone,
            "earnings_factor":  round(earnings_factor, 4),
            "fii_bias":         fii_bias_val,
            "fii_factor":       round(fii_factor, 4),
            "market_support":   round(market_support, 4),
            "final_confidence": final_confidence,
        }

        candidate.reasons.append(
            f"conf_multi:sig={signal_strength:.2f},regime={regime_factor:.2f},"
            f"news={news_factor:.2f},earn={earnings_factor:.2f},fii={fii_factor:.2f}"
            f"→{final_confidence}"
        )

        if final_confidence < settings.AGENT_CONFIDENCE_THRESHOLD:
            reject = f"confidence<threshold:{final_confidence}<{settings.AGENT_CONFIDENCE_THRESHOLD}"
            logger.debug(f"[agent/decision] {symbol} filtered: {reject}")
            return None, reject

        # NSE rule: short selling only allowed intraday (MIS). CNC delivery
        # shorts are rejected by Zerodha / SEBI.
        product = (
            "MIS"
            if candidate.strategy in _MIS_STRATEGIES or candidate.side == "SELL"
            else getattr(settings, "AGENT_DEFAULT_PRODUCT", "CNC")
        )

        decision = AgentDecisionOutput(
            symbol=symbol,
            action=candidate.side,
            confidence=final_confidence,
            regime=regime,
            strategy=candidate.strategy,
            entry=candidate.entry,
            stop=candidate.stop,
            target=candidate.target,
            qty=qty,
            risk_pct=round(risk_pct, 4),
            risk_reward=candidate.risk_reward,
            product=product,
            reasons=candidate.reasons,
            macro_bias=macro_bias,
            fund_score=fund_score,
            fund_grade=fund_grade,
            ts=datetime.utcnow().isoformat(),
            master_score=raw_master,
            confidence_factors=conf_factors,
        )
        logger.info(
            f"[agent/decision] {symbol} → {candidate.side} | conf={final_confidence}% "
            f"(sig={signal_strength:.2f}×support={market_support:.2f}) | {candidate.strategy}"
        )
        return decision, None

    @staticmethod
    def _regime_factor(side: str, regime: str) -> float:
        """Reduce confidence for counter-trend trades."""
        if side == "BUY"  and regime == "BEAR_TRENDING": return 0.7
        if side == "SELL" and regime == "BULL_TRENDING":  return 0.7
        return 1.0

    @staticmethod
    def _check_conflicts(symbol: str, candidate) -> str:
        """Return conflict reason string if BUY signal conflicts with hub context."""
        bare = symbol.replace(".NS", "")
        try:
            from engine import intelligence_hub as hub

            news_raw      = hub.LAST_NEWS_CONTEXT.scores_by_symbol.get(bare, 0.0) if hub.LAST_NEWS_CONTEXT else 0.0
            earnings_tone = hub.LAST_EARNINGS_CONTEXT.tones_by_symbol.get(bare, "NEUTRAL") if hub.LAST_EARNINGS_CONTEXT else "NEUTRAL"
            fii_bias      = hub.LAST_MACRO_CONTEXT.fii_bias if hub.LAST_MACRO_CONTEXT else 0

            hard: list[str] = []
            if news_raw < -0.3:
                hard.append(f"news_negative({news_raw:.2f})")
            if earnings_tone == "NEGATIVE":
                hard.append("earnings_NEGATIVE")
            if fii_bias <= -2:  # only hard-block on heavy FII selling (>₹2000cr/3d)
                hard.append(f"fii_bearish({fii_bias})")

            if hard:
                return f"conflict:{','.join(hard)}"

            # Soft check: two or more moderate negatives
            soft: list[str] = []
            if news_raw < 0:
                soft.append("news_mild")
            if earnings_tone in ("CAUTIOUS", "NEGATIVE"):
                soft.append("earnings_cautious")
            if fii_bias < 0:
                soft.append("fii_mild")

            if len(soft) >= 2:
                return f"conflict_soft:{','.join(soft)}"

        except Exception as exc:
            logger.debug(f"[agent/decision] conflict check skipped for {symbol}: {exc}")

        return ""

    @staticmethod
    def _bear_case(candidate, regime: str, macro_bias: int) -> str:
        """Varsity M12: document the opposing case before committing."""
        if candidate.side == "BUY":
            if regime == "BEAR_TRENDING": return "STRONG:buying_into_bear_trend"
            if macro_bias <= -2:          return "STRONG:macro_headwind"
        else:
            if regime == "BULL_TRENDING": return "STRONG:shorting_bull_trend"
            if macro_bias >= 2:           return "STRONG:macro_tailwind"
        return ""


# ── Hub 7-Factor Override ─────────────────────────────────────────────────────

async def fetch_hub_candidate(
    symbol: str,
    features,
    session,
) -> "TradeCandidate | None":
    """Query master_intelligence_scores for a fresh 7-factor score.

    Returns a TradeCandidate built from the Hub master_score if:
      - A row exists scored within the last 2 hours
      - abs(master_score) >= AGENT_CONFIDENCE_THRESHOLD
      - Symbol is not blocked (is_blocked=False)
      - For SELL signals: EQUITY_SHORT_ENABLED must be True
      - Regime restriction passes (HIGH_VOL_RANGE blocked, BEAR+BUY needs reversal)
      - No hard conflict between master_score direction and news/earnings/fii

    Sets candidate.size_factor=0.5 for RANGE/LOW_VOL_RANGE regimes.
    """
    from db.models import MasterIntelligenceScore
    from sqlalchemy import select as _sel
    from engine.agent.strategies.base import TradeCandidate

    threshold = settings.AGENT_CONFIDENCE_THRESHOLD
    cutoff    = datetime.utcnow() - timedelta(hours=2)
    regime    = features.regime

    bare = symbol.replace(".NS", "")
    try:
        row = (await session.execute(
            _sel(MasterIntelligenceScore)
            .where(
                MasterIntelligenceScore.symbol.in_([bare, symbol]),
                MasterIntelligenceScore.scored_at >= cutoff,
                MasterIntelligenceScore.is_blocked == False,
            )
            .order_by(MasterIntelligenceScore.scored_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    except Exception as exc:
        logger.debug(f"[hub_override] DB query failed for {symbol}: {exc}")
        return None

    if row is None:
        return None

    master_score = row.master_score
    if abs(master_score) < threshold:
        logger.debug(
            f"[hub_override] {symbol} score={master_score:.1f} below threshold {threshold}"
        )
        return None

    side = "BUY" if master_score > 0 else "SELL"

    # ── Regime restriction ────────────────────────────────────────────────────
    if regime == "HIGH_VOL_RANGE":
        reason = "regime:HIGH_VOL_RANGE_blocks_all"
        logger.info(f"[hub_override] {symbol} SKIP — {reason}")
        await _log_hub_rejection(symbol, master_score, regime, reason, 0, session)
        return None

    if regime == "BEAR_TRENDING" and side == "BUY":
        # Allow BUY only when a reversal pattern is detected:
        # price closes above EMA20 after having made a new lower low
        reversal = (features.close > features.ema20 and
                    features.low < features.swing_low_20)
        if not reversal:
            reason = "regime:BEAR_TRENDING_no_reversal"
            logger.info(
                f"[hub_override] {symbol} SKIP — {reason} "
                f"(close={features.close:.2f} ema20={features.ema20:.2f})"
            )
            await _log_hub_rejection(symbol, master_score, regime, reason, 0, session)
            return None

    entry = features.close
    atr   = features.atr14
    if entry <= 0 or atr <= 0:
        return None

    if side == "BUY":
        stop   = round(entry - 2.0 * atr, 2)
        target = round(entry + 4.0 * atr, 2)
    else:
        stop   = round(entry + 1.0 * atr, 2)
        target = round(entry - 2.0 * atr, 2)

    # Position size reduction flag for range regimes; shorts always half-size
    size_factor = 0.5 if regime in ("RANGE", "LOW_VOL_RANGE") else 1.0
    if side == "SELL":
        size_factor *= 0.5
    if size_factor < 1.0:
        logger.info(
            f"[hub_override] {symbol} {regime} → size_factor=0.5 (50% position)"
        )

    # Build sub-score breakdown for reasons
    reasons = [
        f"hub_7factor:score={master_score:.1f}",
        f"technical={row.technical_score:.1f}",
        f"news={row.news_score:.1f}",
        f"sector={row.sector_score:.1f}",
        f"macro={row.macro_score:.1f}",
        f"earnings={row.earnings_score:.1f}",
        f"fundamental={row.fundamental_score:.1f}",
        f"options={row.options_score:.1f}",
        f"hub_signal:{row.signal}",
        f"regime:{regime}",
    ]
    if size_factor < 1.0:
        reasons.append(f"size_reduced:range_regime")

    # Confidence starts at raw master_score magnitude; fuse() will apply
    # the multiplicative model on top of this.
    confidence = min(int(abs(master_score)), 90)

    logger.info(
        f"[hub_override] {symbol} → {side} | score={master_score:.1f} "
        f"conf={confidence}% | signal={row.signal} | regime={regime} "
        f"| scored_at={row.scored_at.isoformat()}"
    )

    candidate = TradeCandidate(
        symbol=symbol,
        side=side,
        entry=round(entry, 2),
        stop=stop,
        target=target,
        confidence=confidence,
        reasons=reasons,
        strategy="HUB_7FACTOR",
        size_factor=size_factor,
        master_score=master_score,
        regime=row.regime or regime,  # carry real regime through to Telegram alerts
        hub_subscores={
            "technical":   row.technical_score,
            "news":        row.news_score,
            "sector":      row.sector_score,
            "macro":       row.macro_score,
            "earnings":    row.earnings_score,
            "fundamental": row.fundamental_score,
            "options":     row.options_score,
            "signal":      row.signal,
            "regime":      row.regime or regime,
            "reasoning":   row.reasoning or {},
            "scored_at":   row.scored_at.isoformat(),
        },
    )
    return candidate


async def _log_hub_rejection(
    symbol: str,
    master_score: float,
    regime: str,
    drop_reason: str,
    final_confidence: int,
    session,
) -> None:
    """Persist a rejected hub candidate to agent_decisions before dropping."""
    try:
        from db.models import AgentDecision
        db_dec = AgentDecision(
            symbol=symbol,
            action="SKIP",
            confidence=final_confidence,
            regime=regime,
            strategy="HUB_7FACTOR",
            entry=None, stop=None, target=None,
            qty=0,
            risk_pct=0.0,
            reasons=[],
            macro_bias=0,
            fund_score=0,
            skip_reason=drop_reason,
            master_score=master_score,
            confidence_factors=None,
            is_paper=settings.AGENT_PAPER_MODE,
            order_id=None,
        )
        session.add(db_dec)
        await session.commit()
    except Exception as exc:
        logger.debug(f"[hub_override] rejection log failed for {symbol}: {exc}")
