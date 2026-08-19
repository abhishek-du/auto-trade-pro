"""Tier 2 — aggregate the day's macro reads into one market-wide view.

Why this tier exists at all
---------------------------
Tier 1 scores individual macro headlines well, but nothing consumed them.
Verified by reading the code rather than assuming: `market_wide_score` in
engine/intelligence_hub.py is the mean of PER-SYMBOL scores, built by looping
over `item.tickers_affected`. A macro headline has no ticker — that is the
router's definition of MACRO — so macro news contributes structurally zero to
it, however well Tier 1 scores it. That is why the live read on 18-Aug showed
`market_wide_score 0.0` with Brent above $91 and a naval blockade running.

And the regime engine (engine/agent/market_regime.py) is built from five
signals — EMA stack, EMA50 slope, 20d ROC, breadth, VIX — every one of them
derived from past price. A geopolitical shock is precisely the thing that has
NOT reached price yet. The engine was structurally blind to it, and VIX 11.39
reading "LOW" during a Hormuz blockade is what that blindness looks like.

The two double-counting traps
-----------------------------
1. Syndication. The same wire story runs 10-15 times; Tier 0's dedupe_key
   handles copies, but paraphrases survive.
2. Theme. Five DIFFERENT oil stories on one day are still one risk. Averaging
   raw scores would let a busy news day on a single theme masquerade as a
   broad-based crisis.

So aggregation collapses by THEME first — worst reading per theme — and only
then combines across themes. Two independent bearish themes matter more than
five stories about one.

Asymmetry is deliberate
-----------------------
The overlay can only ever add caution, never remove it. Bullish macro does not
upgrade the regime. The failure being fixed is "invisible risk", not "missed
rally", and the price-based signals already capture bullish structure well.
An overlay that could upgrade would be a new way to talk the engine into
trading — the opposite of the point.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as _text

from utils.logger import logger

# Redis key for the per-cycle build. The hub, the agent loop and the API can
# all ask for macro context in the same cycle; without this they would each
# rebuild it and each fire the aggregator LLM call.
_CACHE_KEY = "macro_context:current"
_CACHE_TTL = 300           # 5 min — one build per agent cycle

# How far back macro news stays relevant, and how fast it fades. Geopolitical
# risk persists longer than an earnings surprise but does decay: a threat made
# 20 hours ago that produced no follow-through is weaker evidence than one made
# an hour ago.
_LOOKBACK_HOURS = 24
_HALF_LIFE_HOURS = 12.0

# Ceiling on how far the overlay can move the regime composite. The composite's
# state boundaries are +40 / +10 / -15 / -50, so 15 points is enough to push
# MODERATE_BULL down to SIDEWAYS, or SIDEWAYS toward WEAK_BEAR — material, but
# unable on its own to manufacture a STRONG_BEAR out of a calm tape.
MAX_REGIME_PENALTY = 15.0

# Minimum distinct actionable themes before the overlay applies at full weight.
# One theme alone is capped, because a single theme is exactly the shape that
# both syndication and a one-off model error produce.
_SINGLE_THEME_CAP = 0.6


@dataclass
class MacroContext:
    """The day's macro risk, collapsed to one view.

    Two separate numbers on purpose, learned from replaying this against
    13-18 Aug (see aggregate_macro_reads):

      risk_score — 0..100, built from BEARISH themes only. This is what gates.
      bias       — -100..+100, the informational mean across all themes,
                   including bullish ones. Reported, never gates.

    Collapsing them into one signed number is what the first version did, and
    it let a bullish GROWTH story algebraically cancel an oil shock.
    """

    risk_score:   float = 0.0    # 0..100, higher = more macro risk
    bias:         float = 0.0    # -100..+100, informational only
    risk_level:   str   = "NORMAL"   # NORMAL | ELEVATED | HIGH
    themes:       dict  = field(default_factory=dict)   # theme -> worst score
    headline_count: int = 0      # actionable macro headlines behind this
    narrative:    str   = ""     # one-line human explanation
    key_risks:    list  = field(default_factory=list)
    is_stale:     bool  = False  # True when built from no data at all

    @property
    def bearish_themes(self) -> int:
        return sum(1 for v in self.themes.values() if v < 0)

    @property
    def regime_penalty(self) -> float:
        """Points to subtract from the regime composite. Never negative."""
        raw = self.risk_score / 100.0 * MAX_REGIME_PENALTY
        if self.bearish_themes <= 1:
            raw *= _SINGLE_THEME_CAP
        return round(min(max(raw, 0.0), MAX_REGIME_PENALTY), 2)

    def to_dict(self) -> dict:
        return {
            "risk_score": round(self.risk_score, 2),
            "bias": round(self.bias, 2),
            "risk_level": self.risk_level,
            "themes": {k: round(v, 2) for k, v in self.themes.items()},
            "headline_count": self.headline_count,
            "narrative": self.narrative,
            "key_risks": self.key_risks[:5],
            "regime_penalty": self.regime_penalty,
            "is_stale": self.is_stale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MacroContext":
        return cls(
            risk_score=float(d.get("risk_score", 0.0)),
            bias=float(d.get("bias", 0.0)),
            risk_level=str(d.get("risk_level", "NORMAL")),
            themes={k: float(v) for k, v in (d.get("themes") or {}).items()},
            headline_count=int(d.get("headline_count", 0)),
            narrative=str(d.get("narrative", "")),
            key_risks=list(d.get("key_risks") or []),
            is_stale=bool(d.get("is_stale", False)),
        )


async def _fetch_actionable_macro(session, lookback_hours: int = _LOOKBACK_HOURS) -> list[dict]:
    """Pull actionable macro reads written by Tier 1.

    Filters in SQL on the JSON fields Tier 1 persists so a busy news day does
    not drag thousands of company rows into Python to throw most of them away.
    """
    # NAIVE UTC deliberately. news_items.published_at / crawled_at are
    # TIMESTAMP WITHOUT TIME ZONE holding UTC, so a tz-aware bound raises
    # asyncpg's "can't subtract offset-naive and offset-aware datetimes" and,
    # because the caller swallows fetch errors, would silently disable the
    # whole overlay rather than failing loudly. Caught by round-tripping real
    # rows through this query; the aware version never worked once.
    # Matches the convention in engine/intelligence_hub.py.
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    rows = (await session.execute(_text("""
        SELECT headline,
               news_metadata->>'theme'        AS theme,
               news_metadata->>'direction'    AS direction,
               (news_metadata->>'macro_score')::float  AS macro_score,
               (news_metadata->>'confidence')::float   AS confidence,
               news_metadata->>'reasoning'    AS reasoning,
               COALESCE(published_at, crawled_at) AS ts
          FROM news_items
         WHERE news_metadata->>'route' = 'MACRO'
           AND (news_metadata->>'is_actionable')::boolean IS TRUE
           AND COALESCE(published_at, crawled_at) >= :cutoff
         ORDER BY COALESCE(published_at, crawled_at) DESC
         LIMIT 200
    """), {"cutoff": cutoff})).mappings().all()
    return [dict(r) for r in rows]


def aggregate_macro_reads(reads: list[dict], now: datetime | None = None) -> MacroContext:
    """Collapse individual macro reads into one context. Pure, so it is
    testable without a database or an LLM."""
    if not reads:
        return MacroContext(is_stale=True, narrative="no actionable macro news in window")

    now = now or datetime.now(timezone.utc)
    decay_lambda = math.log(2) / _HALF_LIFE_HOURS

    # Worst (most negative) decayed reading per theme. Deliberately not a mean:
    # a genuine crisis produces one severe headline plus a lot of ambient
    # follow-up chatter, and averaging would dilute the severe one away.
    per_theme: dict[str, float] = {}
    for r in reads:
        score = r.get("macro_score")
        if score is None:
            continue
        conf = float(r.get("confidence") or 0.0)
        ts = r.get("ts")
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = max(0.0, (now - ts).total_seconds() / 3600.0) if ts else 0.0

        weighted = float(score) * conf * math.exp(-decay_lambda * age_h)
        theme = (r.get("theme") or "OTHER").upper()
        cur = per_theme.get(theme)
        if cur is None or weighted < cur:
            per_theme[theme] = weighted

    if not per_theme:
        return MacroContext(is_stale=True, narrative="no scored macro reads in window")

    # ── Combine across themes ────────────────────────────────────────────────
    # Bearish themes COMPOUND with diminishing returns; bullish themes are
    # ignored entirely for risk purposes.
    #
    # Both rules come from replaying this against 13-18 Aug rather than from
    # taste. The first version averaged all themes into one signed number, and
    # on 18-Aug that produced -7.5 (NORMAL) from OIL -23, GEOPOLITICS -20,
    # RATES -13 and CURRENCY -10 — because GROWTH +19 and TRADE +7 cancelled
    # them out. Good growth news does not neutralise a Hormuz blockade; risks
    # add up, they do not net off. Averaging also meant that adding a fifth
    # bearish theme could LOWER the risk reading, which is backwards.
    #
    # Diminishing weights keep the compounding honest: the worst theme carries
    # full weight, and each additional independent risk adds less, so a day of
    # broad mild unease cannot outrank a single severe shock.
    _WEIGHTS = (1.0, 0.5, 0.3, 0.2)
    severities = sorted((abs(v) for v in per_theme.values() if v < 0), reverse=True)
    severity = sum(
        s * (_WEIGHTS[i] if i < len(_WEIGHTS) else _WEIGHTS[-1])
        for i, s in enumerate(severities)
    )
    # severity 1.0 == "one maximally severe, fully-confident, fresh theme" and
    # maps to the full penalty; realistic multi-theme days land in 0.3-0.7.
    risk_score = max(0.0, min(100.0, severity * 100.0))

    # Informational only — the signed mean across every theme, bullish included.
    bias = max(-100.0, min(100.0, sum(per_theme.values()) / len(per_theme) * 100.0))

    if risk_score >= 55:
        risk = "HIGH"
    elif risk_score >= 25:
        risk = "ELEVATED"
    else:
        risk = "NORMAL"

    bearish = sorted(
        (r for r in reads if (r.get("macro_score") or 0) < 0),
        key=lambda r: r.get("macro_score") or 0,
    )
    key_risks = []
    seen_themes: set[str] = set()
    for r in bearish:
        t = (r.get("theme") or "OTHER").upper()
        if t in seen_themes:
            continue
        seen_themes.add(t)
        key_risks.append(f"[{t}] {(r.get('headline') or '')[:90]}")
        if len(key_risks) >= 5:
            break

    return MacroContext(
        risk_score=round(risk_score, 2),
        bias=round(bias, 2),
        risk_level=risk,
        themes={k: round(v * 100.0, 2) for k, v in per_theme.items()},
        headline_count=len(reads),
        narrative=(
            f"{risk} macro risk from {len(severities)} bearish theme(s) "
            f"({', '.join(sorted(k for k, v in per_theme.items() if v < 0)) or 'none'}) "
            f"across {len(reads)} actionable headline(s)"
        ),
        key_risks=key_risks,
    )


_SYNTH_PROMPT = """You are the macro risk officer for an INDIAN equity desk.

You are given today's macro headlines, already individually scored, grouped by
theme. Produce ONE market-wide read for the Nifty 50 over the next 1-3 sessions.

Your job is synthesis, not re-scoring:
- Several stories on the SAME theme are ONE risk, not many. Say so.
- Independent themes compounding (oil AND rates AND currency) is worse than
  any one of them alone.
- Say plainly when the day is ordinary. Most days are.

Respond with ONLY a JSON object, no prose, no markdown fence:
{"risk_level":"NORMAL|ELEVATED|HIGH","narrative":"one sentence, max 25 words",
 "key_risks":["short phrase", ...]}"""


async def _synthesise(ctx: MacroContext, reads: list[dict]) -> MacroContext:
    """One LLM call per cycle to write the human-facing read.

    Deliberately NOT allowed to set the number. The deterministic aggregate
    stays authoritative for `score` and therefore for `regime_penalty`; the LLM
    may only sharpen `risk_level` by ONE step and rewrite the narrative.

    The reason is measured, not stylistic: the same model fabricated a
    confident BULLISH read on a gold/silver headline roughly one time in six
    during Tier 1 testing, reasoning from "positive sentiment around AI and
    tech innovation" — text that appears nowhere in the headline. A single
    aggregator call has no second sample to check it against, so it must not be
    able to move a number that gates trading.
    """
    try:
        from utils.llm import call_llm_chat

        grouped: dict[str, list[str]] = {}
        for r in reads[:40]:
            grouped.setdefault((r.get("theme") or "OTHER").upper(), []).append(
                (r.get("headline") or "")[:110]
            )
        payload = "\n".join(
            f"{theme}:\n" + "\n".join(f"  - {h}" for h in hs[:6])
            for theme, hs in grouped.items()
        )

        raw = await call_llm_chat(
            [{"role": "system", "content": _SYNTH_PROMPT},
             {"role": "user", "content": payload}],
            max_tokens=300, temperature=0.1,
        )
        if not raw:
            return ctx

        import re
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)

        # risk_level may move at most one step, and only in the direction the
        # deterministic score already supports.
        order = ["NORMAL", "ELEVATED", "HIGH"]
        want = str(data.get("risk_level", ctx.risk_level)).upper()
        if want in order:
            cur_i, want_i = order.index(ctx.risk_level), order.index(want)
            ctx.risk_level = order[max(cur_i - 1, min(cur_i + 1, want_i))]

        narrative = str(data.get("narrative") or "").strip()
        if narrative:
            ctx.narrative = narrative[:200]
        risks = data.get("key_risks")
        if isinstance(risks, list) and risks:
            ctx.key_risks = [str(x)[:120] for x in risks[:5]]
    except Exception as exc:
        # Synthesis is cosmetic. Losing it must never cost the risk signal.
        logger.debug(f"[macro_context] synthesis skipped: {exc}")
    return ctx


async def get_macro_context(session, use_llm: bool = True) -> MacroContext:
    """Build (or reuse) the current macro context.

    Cached in Redis for _CACHE_TTL so the aggregator LLM call happens once per
    cycle no matter how many callers ask. Cache failures are non-fatal — they
    cost an extra rebuild, not a wrong answer.
    """
    try:
        from utils.cache import get_redis
        cached = await get_redis().get(_CACHE_KEY)
        if cached:
            return MacroContext.from_dict(json.loads(cached))
    except Exception as exc:
        logger.debug(f"[macro_context] cache read failed: {exc}")

    try:
        reads = await _fetch_actionable_macro(session)
    except Exception as exc:
        # Fail SILENT, not neutral-and-confident: an empty context has
        # regime_penalty 0.0, which is the pre-Tier-2 behaviour.
        logger.warning(f"[macro_context] fetch failed, no macro overlay this cycle: {exc}")
        return MacroContext(is_stale=True, narrative="macro fetch failed")

    ctx = aggregate_macro_reads(reads)
    if use_llm and ctx.headline_count and ctx.risk_level != "NORMAL":
        # Only synthesise when something is actually going on. On an ordinary
        # day this saves the call entirely.
        ctx = await _synthesise(ctx, reads)

    try:
        from utils.cache import get_redis
        await get_redis().set(_CACHE_KEY, json.dumps(ctx.to_dict()), ex=_CACHE_TTL)
    except Exception as exc:
        logger.debug(f"[macro_context] cache write failed: {exc}")

    return ctx
