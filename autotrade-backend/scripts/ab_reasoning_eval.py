"""A/B validation: does the LLM reasoning gate add value over arithmetic-only?

Replays REAL backtested trades (which carry both the candidate context AND the
known outcome r_multiple) through each reasoning level and measures whether the
gate preferentially SKIPs losers — the only thing that matters.

For each level it reports:
  • skip-rate on WINNERS vs LOSERS   (good gate skips losers MORE)
  • mean R of trades it would KEEP   vs the take-all baseline
  • net edge delta                    (kept_meanR - baseline_meanR)

Baseline = arithmetic-only = "take every qualified trade" (what runs today).

Usage:
  .venv/bin/python scripts/ab_reasoning_eval.py --n 40            # L1 (reasoning)
  .venv/bin/python scripts/ab_reasoning_eval.py --n 30 --level 2  # debate
  .venv/bin/python scripts/ab_reasoning_eval.py --n 20 --level 3  # tool-use
  .venv/bin/python scripts/ab_reasoning_eval.py --src results/bt_revalidate.json
"""
import argparse
import asyncio
import json
import os
import random
import sys
from types import SimpleNamespace

# Make the backend root importable regardless of cwd (script lives in scripts/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _balanced_sample(trades: list[dict], n: int) -> list[dict]:
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    random.shuffle(wins); random.shuffle(losses)
    half = n // 2
    return wins[:half] + losses[: n - half]


def _build(trade: dict):
    """Reconstruct a TradeCandidate + decision shim from a backtest trade row.
    NOTE: backtest rows carry confidence/regime/strategy/levels but NOT the full
    7-factor hub_subscores, so the reasoning here sees a thinner context than live."""
    from engine.agent.strategies.base import TradeCandidate
    rr = abs(trade["target"] - trade["entry"]) / max(abs(trade["entry"] - trade["stop"]), 1e-9)
    cand = TradeCandidate(
        symbol=trade["symbol"], side=trade["side"], entry=trade["entry"],
        stop=trade["stop"], target=trade["target"],
        confidence=int(trade.get("confidence", 50)), strategy=trade["strategy"],
        master_score=float(trade.get("confidence", 50)), regime=trade.get("regime", ""),
        reasons=[], hub_subscores={},
    )
    dec = SimpleNamespace(
        action=trade["side"], regime=trade.get("regime", ""),
        master_score=float(trade.get("confidence", 50)),
        confidence=int(trade.get("confidence", 50)), strategy=trade["strategy"],
        entry=trade["entry"], stop=trade["stop"], target=trade["target"],
        risk_reward=round(rr, 2), confidence_factors={},
    )
    return cand, dec


async def main(src: str, n: int, level: int):
    from utils.config import settings
    settings.AGENT_LLM_REASONING_ENABLED = True
    settings.AGENT_LLM_DEBATE_ENABLED    = (level == 2)
    settings.AGENT_LLM_TOOLUSE_ENABLED   = (level == 3)
    from engine.agent.decision_engine import apply_reasoning_gate

    trades = json.load(open(src))["all_trades"]
    sample = _balanced_sample(trades, n)
    mode = {1: "L1 reasoning", 2: "L2 debate", 3: "L3 tool-use"}[level]

    kept_R, base_R = [], []
    skip_win = skip_loss = n_win = n_loss = 0
    for i, t in enumerate(sample, 1):
        r = float(t["r_multiple"]); is_win = t["pnl"] > 0
        base_R.append(r)
        n_win += is_win; n_loss += (not is_win)
        cand, dec = _build(t)
        try:
            out, _reason = await apply_reasoning_gate(t["symbol"], cand, dec)
        except Exception:
            out = dec  # fail-open → counts as TAKE
        if out is None:                       # gate SKIPPED it
            skip_win += is_win; skip_loss += (not is_win)
        else:                                 # gate KEPT it
            kept_R.append(r)
        print(f"  [{i}/{len(sample)}] {t['symbol']:14} {'WIN ' if is_win else 'LOSS'} "
              f"R={r:+.2f} → {'SKIP' if out is None else 'TAKE'}")

    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    print("\n" + "=" * 64)
    print(f"A/B RESULT — {mode}  | sample={len(sample)} ({n_win}W / {n_loss}L)")
    print("=" * 64)
    print(f"  Skip-rate on LOSERS : {skip_loss}/{n_loss} = {100*skip_loss/max(n_loss,1):.0f}%   (higher = better)")
    print(f"  Skip-rate on WINNERS: {skip_win}/{n_win} = {100*skip_win/max(n_win,1):.0f}%   (lower = better)")
    print(f"  Baseline mean R (take all): {mean(base_R):+.3f}")
    print(f"  Gate-kept mean R          : {mean(kept_R):+.3f}   (n_kept={len(kept_R)})")
    delta = mean(kept_R) - mean(base_R)
    print(f"  EDGE DELTA               : {delta:+.3f} R/trade   "
          f"{'✅ gate ADDS edge' if delta > 0.02 else '⚠️ no clear edge' if abs(delta)<=0.02 else '❌ gate HURTS'}")
    print("  NOTE: backtest rows lack full 7-factor subscores, so live reasoning")
    print("        sees richer context — treat this as directional, not final.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--src",   default="results/bt_revalidate.json")
    p.add_argument("--n",     type=int, default=40, help="sample size (balanced W/L)")
    p.add_argument("--level", type=int, default=1, choices=[1, 2, 3])
    a = p.parse_args()
    asyncio.run(main(a.src, a.n, a.level))
