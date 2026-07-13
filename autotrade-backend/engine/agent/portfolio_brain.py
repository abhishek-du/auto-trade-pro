"""Portfolio-level cognitive cycle (the 'veteran trader' top-down step).

Once per trade-loop cycle, before any per-candidate decision, this forms a single
top-down read of the whole book + market — regime, VIX, day's P&L, open-position
load, the candidate shortlist — and returns a stance:

    {stance, halt_new, size_multiplier, max_new_entries, thesis, key_risks}

stance ∈ AGGRESSIVE | NORMAL | DEFENSIVE | HALT. The caller logs every result to
portfolio_theses (for A/B), and — only when NOT in shadow mode — applies it:
HALT or halt_new=True stops new entries this cycle; max_new_entries caps them.

Gated by AGENT_PORTFOLIO_BRAIN_ENABLED; fail-open (any failure → None → the loop
trades exactly as it does today). This is advisory over the deterministic engine,
never the executor.
"""
from __future__ import annotations

from utils.config import settings
from utils.logger import logger


def _stance_defaults(stance: str) -> tuple[bool, int | None, float]:
    """Map a stance label to (halt_new, max_new_cap, size_multiplier) defaults the
    LLM can override. Conservative: a DEFENSIVE read trims, HALT stops."""
    s = (stance or "NORMAL").upper()
    return {
        "HALT":       (True,  0,    0.0),
        "DEFENSIVE":  (False, 1,    0.5),
        "NORMAL":     (False, None, 1.0),
        "AGGRESSIVE": (False, None, 1.0),
    }.get(s, (False, None, 1.0))


async def portfolio_cognitive_cycle(context: dict) -> dict | None:
    """Run the once-per-cycle top-down thesis. `context` is assembled by the caller
    (no globals reached into here, so it stays testable). Returns the stance dict
    or None on any failure (fail-open)."""
    if not getattr(settings, "AGENT_PORTFOLIO_BRAIN_ENABLED", False):
        return None
    try:
        from utils.llm import call_llm_chat
        from engine.agent.decision_engine import _parse_first_json

        sys_prompt = (
            "You are a disciplined 20-year veteran Indian-equity (NSE) portfolio "
            "manager running a long-only swing book. Before any individual trade, "
            "you read the WHOLE picture top-down — market regime, volatility, the "
            "day's P&L, how loaded the book already is — and set the stance for this "
            "cycle. Be risk-first: on a weak or falling tape, with the book already "
            "heavy or the day red, you tighten or stop; you never add risk into "
            "weakness. Respond with ONLY compact JSON:\n"
            '{"stance":"AGGRESSIVE|NORMAL|DEFENSIVE|HALT",'
            '"halt_new":true|false,'
            '"max_new_entries":<int 0-8>,'
            '"size_multiplier":<0.0-1.0>,'
            '"thesis":"<=40 words top-down read",'
            '"key_risks":"<=20 words"}'
        )
        c = context
        user_prompt = (
            f"MARKET: regime={c.get('regime')} vix={c.get('vix')} "
            f"macro_bias={c.get('macro_bias')} mood={c.get('mood')} "
            f"nifty_5d_return={c.get('nifty_5d_ret')}\n"
            f"BOOK: equity=Rs{c.get('equity')} cash=Rs{c.get('cash')} "
            f"deployed={c.get('deployed_pct')}% open_positions={c.get('open_positions')}"
            f"/{c.get('max_positions')} day_pnl={c.get('day_roi')}% "
            f"unrealised=Rs{c.get('unrealised')}\n"
            f"WORST_OPEN: {c.get('worst_open')}   BEST_OPEN: {c.get('best_open')}\n"
            f"SHORTLIST: {c.get('n_candidates')} candidates above threshold "
            f"(buy={c.get('n_buy')} sell={c.get('n_sell')}); top: {c.get('top_candidates')}"
        )
        resp = await call_llm_chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user",   "content": user_prompt}],
            max_tokens=320, temperature=0.2,
        )
        data = _parse_first_json(resp)
        if not data:
            return None

        stance = str(data.get("stance", "NORMAL")).upper()
        d_halt, d_cap, d_mult = _stance_defaults(stance)
        halt = bool(data.get("halt_new", d_halt)) or stance == "HALT"

        def _num(v, default):
            try: return type(default)(v)
            except Exception: return default
        size_mult = _num(data.get("size_multiplier", d_mult), d_mult)
        size_mult = max(0.0, min(1.0, size_mult))
        max_new   = data.get("max_new_entries", d_cap)
        max_new   = _num(max_new, d_cap) if max_new is not None else d_cap

        return {
            "stance": stance, "halt_new": halt,
            "size_multiplier": size_mult, "max_new_entries": max_new,
            "thesis": str(data.get("thesis", ""))[:400],
            "key_risks": str(data.get("key_risks", ""))[:300],
        }
    except Exception as exc:
        logger.debug(f"[portfolio_brain] cycle failed: {exc}")
        return None


async def log_thesis(result: dict, context: dict, enforced: bool) -> None:
    """Append the cycle's thesis + stance to portfolio_theses (fail-safe)."""
    try:
        from db.database import AsyncSessionLocal
        from db.models import PortfolioThesis

        def _f(x):
            try: return float(x)
            except Exception: return None
        def _i(x):
            try: return int(x)
            except Exception: return None

        async with AsyncSessionLocal() as s:
            s.add(PortfolioThesis(
                stance=result.get("stance", "NORMAL"),
                halt_new=bool(result.get("halt_new", False)),
                size_multiplier=_f(result.get("size_multiplier")),
                max_new_entries=_i(result.get("max_new_entries")),
                enforced=enforced,
                equity=_f(context.get("equity")),
                daily_roi=_f(context.get("day_roi")),
                open_positions=_i(context.get("open_positions")),
                vix=_f(context.get("vix")),
                thesis=result.get("thesis"),
                key_risks=result.get("key_risks"),
                detail=result,
            ))
            await s.commit()
    except Exception as exc:
        logger.debug(f"[portfolio_brain] thesis log failed: {exc}")
