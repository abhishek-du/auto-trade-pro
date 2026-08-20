"""Path F — Layer 3: LLM news veto and explanation. STUB.

Ships the interface; the real Bedrock calls land in Phase 3. `check_veto`
returns PASS for everything today and says so in the reason string, so a
signal's audit row never implies a veto check happened when it did not.

Phase 3 notes captured now so they are not re-derived:
  * Provider is AWS Bedrock Converse via `utils.llm.call_llm_chat` — single
    provider, no fallback chain, behind a Redis RPM limiter.
  * Bedrock Converse exposes no seed for this model, so responses are NOT
    deterministic. Pin temperature low and treat the veto as advisory.
  * On LLM unavailability, fall back to a deterministic template (the pattern
    `integrations/trade_explainer.py` already uses) rather than blocking.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.tactical_rules import Signal
from utils.logger import logger

VETO_PROMPT = (
    "Analyze these headlines: {headlines}. Is there any negative event (fraud, "
    "regulatory raid, promoter selling, earnings downgrade, severe sector "
    "headwind) that makes this stock untradeable today? "
    "Respond with 'VETO' if yes, else 'PASS'."
)


@dataclass(frozen=True)
class VetoResult:
    vetoed: bool
    reason: str
    checked: bool = False   # False => no LLM call was made


async def check_veto(signal: Signal) -> VetoResult:
    """Phase 1: always PASS, explicitly marked as unchecked."""
    logger.debug(f"[tactical_llm] veto stub PASS for {signal.symbol}")
    return VetoResult(
        vetoed=False,
        reason="llm veto not implemented (Phase 3) — signal not screened for news risk",
        checked=False,
    )


async def explain(signal: Signal, composite: float) -> str:
    """Deterministic template explanation. Phase 3 swaps in the LLM."""
    direction = "long" if signal.side == "BUY" else "short"
    return (
        f"{signal.symbol}: {direction} on {signal.strategy_name} "
        f"(score {composite:.0f}/100, entry {signal.entry_price:.2f}, "
        f"stop {signal.stop_loss:.2f}, target {signal.target:.2f})."
    )
