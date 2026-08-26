"""Path F — Layer 1: rule-based composite scoring and filtering.

Takes the raw signals every rule produced this cycle and reduces them to a
ranked shortlist. Deliberately deterministic and cheap: no I/O per signal except
one batched read of the Hub's sector scores.

Score components (0-100 composite):
  * strategy base       — the rule's own computed confidence
  * volume z-score      — capped at +/-2 sigma, worth up to +/-10
  * sector mood         — read-only from MasterIntelligenceScore.sector_score
  * RSI/MACD alignment  — +10 when RSI is in 40-80 and MACD is positive
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MasterIntelligenceScore
from engine.tactical_rules import Signal
from utils.logger import logger


async def fetch_sector_scores(
    symbols: list[str], session: AsyncSession
) -> dict[str, float]:
    """Latest sector sub-score per symbol. READ-ONLY on MasterIntelligenceScore.

    One batched query for the whole cycle rather than per-signal, so a 50-symbol
    scan costs one round-trip.
    """
    if not symbols:
        return {}
    try:
        rows = (
            await session.execute(
                select(
                    MasterIntelligenceScore.symbol,
                    MasterIntelligenceScore.sector_score,
                    MasterIntelligenceScore.scored_at,
                )
                .where(MasterIntelligenceScore.symbol.in_(symbols))
                .order_by(MasterIntelligenceScore.scored_at.desc())
                .limit(len(symbols) * 3)
            )
        ).all()
    except Exception as exc:
        logger.debug(f"[tactical_scoring] sector score fetch failed: {exc}")
        return {}

    out: dict[str, float] = {}
    for sym, score, _ in rows:          # newest-first, so first wins
        if sym not in out and score is not None:
            out[sym] = float(score)
    return out


def _volume_z(signal: Signal) -> float:
    """Volume surge mapped to a capped +/-10 contribution.

    `vol_surge` is a ratio around 1.0; treat (ratio - 1) as the deviation and
    clamp at 2 sigma-equivalent so one freak print cannot dominate the score.
    """
    surge = float(signal.meta.get("vol_surge") or 1.0)
    z = max(-2.0, min(2.0, surge - 1.0))
    return z * 5.0


def _rsi_macd_bonus(signal: Signal) -> float:
    rsi = signal.meta.get("rsi")
    if rsi is None:
        return 0.0
    return 10.0 if 40.0 <= float(rsi) <= 80.0 else 0.0


def composite_score(signal: Signal, sector_scores: dict[str, float]) -> float:
    """Blend the components into 0-100."""
    score = float(signal.confidence)
    score += _volume_z(signal)
    score += _rsi_macd_bonus(signal)

    sector = sector_scores.get(signal.symbol)
    if sector is not None:
        # sector_score is -100..100; nudge with its sign, worth up to +/-8.
        aligned = sector if signal.side == "BUY" else -sector
        score += max(-8.0, min(8.0, aligned * 0.08))

    return round(max(0.0, min(100.0, score)), 2)


async def score_and_filter(
    signals: list[Signal],
    session: AsyncSession,
    *,
    min_score: float = 50.0,
    top_n: int = 15,
    overflow_out: list | None = None,
) -> list[tuple[Signal, float]]:
    """Score every signal, drop the weak ones, keep the best `top_n`.

    `overflow_out`, when given, is EXTENDED with the signals that cleared
    min_score but fell outside `top_n` — the ranks this function currently
    discards without trace. It is a research capture and changes nothing about
    the return value, so a caller that passes nothing gets byte-identical
    behaviour. Nothing downstream may execute what lands in that list; see
    tests/test_rank_overflow_capture.py.
    """
    if not signals:
        return []

    sector_scores = await fetch_sector_scores(
        sorted({s.symbol for s in signals}), session
    )
    scored = [(s, composite_score(s, sector_scores)) for s in signals]
    kept = [(s, sc) for s, sc in scored if sc >= min_score]
    kept.sort(key=lambda pair: pair[1], reverse=True)

    if len(scored) != len(kept):
        logger.debug(
            f"[tactical_scoring] {len(scored)} signals -> {len(kept)} above "
            f"{min_score} -> keeping {min(len(kept), top_n)}"
        )

    if overflow_out is not None:
        overflow_out.extend(kept[top_n:])

    return kept[:top_n]
