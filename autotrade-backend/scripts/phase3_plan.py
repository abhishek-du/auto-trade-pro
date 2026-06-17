"""Phase 3 — Optimization Roadmap (Gated)

Reads a Phase 2 validate_edge.py JSON report and produces a Phase 3 action plan.

Phase 3 gate:
  • OOS expectancy CI must exclude zero from below (ci_lo > 0), OR
  • If only regime-conditional, regime-gating is Phase 3 item 1.
  • If gate fails entirely → nothing in Phase 3 is actionable; return to Phase 2.

Improvement candidates (§2) are evaluated from evidence only.
No rule, threshold, exit, or sizing change is recommended without the CI backing it.

Usage:
  .venv/bin/python scripts/phase3_plan.py --phase2 results/phase2.json
  .venv/bin/python scripts/phase3_plan.py --phase2 results/phase2.json --out results/phase3.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Literal

Tier = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "BLOCKED"]

_W = "═" * 72
_f3 = lambda v: f"{v:.3f}" if v is not None else "N/A"
_pct = lambda v: f"{v * 100:.1f}%" if v is not None else "N/A"

# Capital readiness tiers in rupees
_CAPITAL_TIERS = [
    ("₹1 lakh",  1_00_000,   "OOS CI > 0; max-DD < 15%; ≥6 months forward paper matching backtest"),
    ("₹10 lakh", 10_00_000,  "Above + liquidity: position sizes vs ADV; stable across ≥2 regimes"),
    ("₹50 lakh", 50_00_000,  "Above + capacity: live slippage ≈ modeled; ≥2 stable regimes OOS"),
    ("₹1 crore", 1_00_00_000,"Above + 12-month live track record; DD discipline through a losing regime"),
    ("₹10 crore",10_00_00_000,"Above + market-impact model; execution algo; institutional risk controls"),
]


@dataclass
class ImprovementItem:
    id:              str
    title:           str
    tier:            Tier
    trigger:         str       # measured condition that activates this item
    evidence:        str       # what the Phase 2 data says
    expected_impact: str       # ΔExpectancy estimate with CI if measurable
    risk:            str       # overfitting, regime-dependence, reduced sample
    action:          str       # concrete next step
    triggered:       bool = False


@dataclass
class Phase3Report:
    gate_passed:     bool
    gate_reason:     str
    oos_mean_r:      float | None
    oos_ci_lo:       float | None
    oos_ci_hi:       float | None
    oos_verdict:     str
    regime_only:     bool       # positive edge but only in BULL_TRENDING
    improvements:    list[ImprovementItem] = field(default_factory=list)
    readiness_tier:  str = "NONE"
    readiness_notes: list[str] = field(default_factory=list)
    capital_gates:   list[dict] = field(default_factory=list)
    final_statement: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Gate evaluation
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_gate(p2: dict) -> tuple[bool, bool, str]:
    """Return (gate_passed, regime_only, reason)."""
    wf   = p2.get("walk_forward", {})
    oos  = wf.get("split2_train2022_24_val2025_26", {}).get("val", {})
    oos1 = wf.get("split1_train2022_23_val2024",     {}).get("val", {})

    oos_r  = oos.get("mean_r")
    oos_lo = oos.get("ci_lo_r")
    oos_hi = oos.get("ci_hi_r")
    oos_rv = oos.get("r_verdict", "UNCERTAIN")

    overall_rv = p2.get("overall", {}).get("r_verdict", "UNCERTAIN")
    verdict    = p2.get("verdict", {})
    edge_st    = verdict.get("edge_status", "")

    # Regime-only: positive overall but bull-trending only
    regime_only = "CONDITIONAL" in edge_st or "REGIME" in edge_st

    if oos_lo is not None and oos_lo > 0:
        return True, False, f"OOS CI [{_f3(oos_lo)}, {_f3(oos_hi)}] excludes zero — gate PASSED"

    if regime_only and overall_rv == "POSITIVE":
        return True, True, (
            f"Overall edge positive but regime-conditional.  "
            f"OOS CI [{_f3(oos_lo)}, {_f3(oos_hi)}] straddles zero — "
            f"gate PASSED (conditional); regime-gating is Phase 3 item 1"
        )

    # Gate fails
    reason_parts = []
    if oos_lo is None:
        reason_parts.append("OOS (2025-26) has insufficient data (no trades)")
    elif oos_hi is not None and oos_hi < 0:
        reason_parts.append(
            f"OOS CI [{_f3(oos_lo)}, {_f3(oos_hi)}] fully negative — "
            f"edge is statistically confirmed absent in 2025-26"
        )
    elif oos_lo <= 0:
        reason_parts.append(
            f"OOS CI [{_f3(oos_lo)}, {_f3(oos_hi)}] straddles zero — edge not demonstrated"
        )
    if oos_rv in ("NEGATIVE", "UNCERTAIN"):
        reason_parts.append(f"OOS verdict is {oos_rv}")
    if overall_rv in ("NEGATIVE", "UNCERTAIN"):
        reason_parts.append(f"Overall verdict is {overall_rv}")

    return False, False, "; ".join(reason_parts) or "Phase 2 verdict negative or uncertain"


# ═══════════════════════════════════════════════════════════════════════════════
# Improvement candidate evaluation
# ═══════════════════════════════════════════════════════════════════════════════

def _delta_r(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "N/A"
    d = a - b
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.3f} R"


def evaluate_improvements(p2: dict, gate_passed: bool, regime_only: bool) -> list[ImprovementItem]:
    """Evaluate all 5 improvement candidates against Phase 2 evidence."""
    items: list[ImprovementItem] = []
    overall  = p2.get("overall", {})
    by_strat = p2.get("by_strategy", {})
    ct       = p2.get("regime_crosstab", {})
    cb       = p2.get("confidence_buckets", {})
    hub      = p2.get("hub_unshadowed", {})
    ep       = p2.get("exit_policies", {})
    verdict  = p2.get("verdict", {})
    wf_oos   = (p2.get("walk_forward", {})
                  .get("split2_train2022_24_val2025_26", {})
                  .get("val", {}))

    # ── 1. Regime gating ─────────────────────────────────────────────────────
    bt_cells   = [v for s_d in ct.values() for r, v in s_d.items()
                  if r == "BULL_TRENDING" and v.get("n", 0) >= 30]
    bear_cells = [v for s_d in ct.values() for r, v in s_d.items()
                  if r in ("BEAR_TRENDING", "RANGE", "UNKNOWN") and v.get("n", 0) >= 30]
    bt_mean    = (sum(c.get("mean_r") or 0 for c in bt_cells) / len(bt_cells)) if bt_cells else None
    other_mean = (sum(c.get("mean_r") or 0 for c in bear_cells) / len(bear_cells)) if bear_cells else None

    regime_triggered = (
        regime_only
        or (bt_mean is not None and other_mean is not None
            and bt_mean > 0.05 and other_mean < 0.0)
    )

    items.append(ImprovementItem(
        id="R1",
        title="Regime gating — size/enter by measured regime expectancy",
        tier="HIGH" if regime_triggered else "LOW",
        trigger="regime cross-tab CI: non-BULL regimes show negative/uncertain expectancy",
        evidence=(
            f"BULL_TRENDING mean_R≈{_f3(bt_mean)}, other regimes mean_R≈{_f3(other_mean)}.  "
            f"Edge status: {verdict.get('edge_status', 'N/A')}"
            if bt_mean is not None else "Insufficient cross-tab data (N<30 per cell)"
        ),
        expected_impact=(
            f"Eliminate non-BULL trades.  "
            f"If non-BULL mean_R={_f3(other_mean)}, removing them improves blended R.  "
            "CI of improvement requires a dedicated OOS split (not yet measured)."
        ),
        risk="Reduced trade frequency; BULL regime may lag signal (enter too late); "
             "morning_regime already provides partial coverage — measure its hit rate first.",
        action=(
            "1. Confirm morning_regime WAIT/SELECTIVE hit rate on the OOS period.  "
            "2. If hit rate < 60%, tune the NIFTYBEES EMA50 + VIX thresholds.  "
            "3. Only then scale non-BULL positions to 0.5× (not zero — avoids over-concentration)."
        ),
        triggered=regime_triggered,
    ))

    # ── 2. Confidence thresholding ────────────────────────────────────────────
    mono_check    = (cb or {}).get("_monotonic_check", {})
    is_monotonic  = mono_check.get("is_monotonic")
    bucket_items  = {k: v for k, v in (cb or {}).items() if k != "_monotonic_check"}
    best_bucket   = max(bucket_items, key=lambda k: bucket_items[k].get("mean_r") or -999, default=None)
    best_r        = bucket_items[best_bucket].get("mean_r") if best_bucket else None
    overall_mean_r = overall.get("mean_r")

    conf_triggered = (is_monotonic is True and best_r is not None
                      and overall_mean_r is not None
                      and (best_r - overall_mean_r) > 0.05)

    items.append(ImprovementItem(
        id="R2",
        title="Confidence threshold raising — filter entries by score monotonicity",
        tier="MEDIUM" if conf_triggered else "LOW",
        trigger="confidence bucket analysis is monotonic (higher conf → higher mean_R)",
        evidence=(
            f"Monotonic: {is_monotonic}.  "
            f"Best bucket {best_bucket}: mean_R={_f3(best_r)} vs overall mean_R={_f3(overall_mean_r)}."
            if is_monotonic is not None else "Confidence bucket analysis not available."
        ),
        expected_impact=(
            f"Raising threshold to bucket {best_bucket} would add ~{_delta_r(best_r, overall_mean_r)} "
            "per trade.  Trade count impact: depends on bucket N (see Phase 2 report)."
            if conf_triggered else
            "Not triggered — monotonicity not confirmed or improvement < 0.05 R."
        ),
        risk="Reduced trade frequency; small bucket N inflates apparent mean_R; "
             "confidence is calculated on the entry path, not measured OOS.",
        action=(
            f"Raise _CONF_THRESH from current to the lowest bucket floor showing positive CI.  "
            "Re-run walk-forward split 2 with new threshold and compare OOS expectancy."
            if conf_triggered else
            "Do not change confidence threshold — evidence insufficient."
        ),
        triggered=conf_triggered,
    ))

    # ── 3. HUB_SIGNAL inclusion/exclusion ─────────────────────────────────────
    hub_n  = hub.get("n", 0)
    hub_r  = hub.get("mean_r")
    hub_rv = hub.get("r_verdict", "UNCERTAIN")
    hub_lo = hub.get("ci_lo_r")
    hub_hi = hub.get("ci_hi_r")

    hub_triggered = hub_n >= 30 and hub_rv in ("POSITIVE", "NEGATIVE")
    hub_positive  = hub_rv == "POSITIVE" and (hub_lo or 0) > 0
    hub_negative  = hub_rv == "NEGATIVE" and (hub_hi or 0) < 0

    items.append(ImprovementItem(
        id="R3",
        title="HUB_SIGNAL strategy inclusion/exclusion decision",
        tier=(
            "HIGH" if hub_triggered and hub_negative else
            "MEDIUM" if hub_triggered and hub_positive else
            "LOW"
        ),
        trigger="Hub un-shadowed OOS has statistically significant positive or negative mean_R",
        evidence=(
            f"HUB_SIGNAL un-shadowed (technical proxy): n={hub_n}, "
            f"mean_R={_f3(hub_r)}, CI=[{_f3(hub_lo)},{_f3(hub_hi)}], verdict={hub_rv}.  "
            "NOTE: proxy uses EMA/ST/RSI, not real 7-factor hub scores — "
            "treat as directional signal only."
            if hub_n > 0 else
            "HUB_SIGNAL un-shadowed: no trades generated (N=0)."
        ),
        expected_impact=(
            f"If excluded: removes {hub_n} trades with mean_R={_f3(hub_r)}.  "
            f"Expected book improvement: {_delta_r((overall_mean_r or 0) - (hub_r or 0), 0)} on blended R "
            "(rough estimate; requires re-run without HUB_SIGNAL)."
            if hub_triggered else
            "Not triggered — insufficient data or verdict uncertain."
        ),
        risk="HUB_SIGNAL proxy ≠ real 7-factor score.  "
             "Excluding based on proxy may incorrectly penalize a strategy that works with real scores.  "
             "Real HUB_SIGNAL can only be validated on live paper-trade history (not backtest).",
        action=(
            "Re-run with ENABLE_HUB_SIGNAL=false for 30-day forward paper period.  "
            "Compare paper PnL / win-rate to same period with it enabled.  "
            "Only exclude if forward period confirms hub drags performance."
            if hub_triggered else
            "Gather more paper-trade data on HUB_SIGNAL before deciding."
        ),
        triggered=hub_triggered,
    ))

    # ── 4. Exit policy change ─────────────────────────────────────────────────
    current_r = (ep.get("current") or {}).get("mean_r")
    best_ep   = None
    best_ep_r = current_r
    for policy, s in ep.items():
        if policy == "current":
            continue
        r = s.get("mean_r")
        lo = s.get("ci_lo_r")
        if r is not None and r > (best_ep_r or -999) and (lo or -1) > 0:
            best_ep   = policy
            best_ep_r = r

    exit_triggered = (best_ep is not None
                      and best_ep_r is not None
                      and current_r is not None
                      and (best_ep_r - current_r) > 0.05)

    items.append(ImprovementItem(
        id="R4",
        title="Exit policy change — replace current T1-partial + trail with measured winner",
        tier="MEDIUM" if exit_triggered else "LOW",
        trigger="An exit variant beats current by > 0.05 R with OOS CI excluding zero",
        evidence=(
            f"Current exit mean_R={_f3(current_r)}.  "
            f"Best alternative: {best_ep} mean_R={_f3(best_ep_r)} "
            f"(Δ={_delta_r(best_ep_r, current_r)}).  "
            "Exit comparison runs on a sample (N≤100 symbols) — re-run on full universe before acting."
            if current_r is not None else
            "Exit policy comparison results not available."
        ),
        expected_impact=(
            f"Switching to {best_ep}: Δ≈{_delta_r(best_ep_r, current_r)} per trade in backtest.  "
            "OOS CI for this delta: not yet computed — requires a dedicated split."
            if exit_triggered else
            "Not triggered — no exit variant beats current with CI excluding zero."
        ),
        risk="Exit policy comparison uses same entries — any entry-bias amplifies apparent exit gain.  "
             "Run on full universe (443 symbols) not sample before deciding.  "
             "Give-back and MFE metrics (Phase 1 data) are the ground truth for exit quality.",
        action=(
            f"1. Re-run exit comparison on full symbol set.  "
            f"2. Check Phase 1 MFE/give-back by exit_reason in live paper trades.  "
            f"3. If both confirm {best_ep}, switch via `exit_policy` config (no code change — "
            "gated by settings)."
            if exit_triggered else
            "Do not change exit policy — no statistically significant improvement found."
        ),
        triggered=exit_triggered,
    ))

    # ── 5. Position sizing — tie to per-regime expectancy ─────────────────────
    # Compute variance of mean_R across regimes (all strategies combined)
    all_regime_rs = []
    for s_d in ct.values():
        for cell in s_d.values():
            r = cell.get("mean_r")
            if r is not None and cell.get("n", 0) >= 30:
                all_regime_rs.append(r)

    regime_r_std = None
    if len(all_regime_rs) >= 3:
        import math
        mu  = sum(all_regime_rs) / len(all_regime_rs)
        regime_r_std = math.sqrt(sum((r - mu) ** 2 for r in all_regime_rs) / len(all_regime_rs))

    sizing_triggered = regime_r_std is not None and regime_r_std > 0.15

    items.append(ImprovementItem(
        id="R5",
        title="Position sizing — tie lot size to measured per-regime expectancy",
        tier="MEDIUM" if sizing_triggered else "LOW",
        trigger="Regime cross-tab shows σ(mean_R across regimes) > 0.15 R",
        evidence=(
            f"Regime mean_R across cells (N≥30): {[round(r, 3) for r in sorted(all_regime_rs)]}.  "
            f"σ = {_f3(regime_r_std)}.  "
            f"High variance ({regime_r_std:.3f} > 0.15) suggests regime-dependent sizing is justified."
            if regime_r_std is not None else
            "Insufficient regime cross-tab data (N<30 per cell)."
        ),
        expected_impact=(
            "Scaling down non-BULL positions to 0.5× (reducing risk in lower-expectancy regimes) "
            "improves blended expectancy but reduces trade frequency and absolute PnL.  "
            "Quantitative impact requires a dedicated backtest with the sizing rule active."
        ),
        risk="Regime mis-classification lags actual regime shifts (EMA50 is a slow signal).  "
             "Over-sizing in BULL only creates regime-concentrated drawdowns if BULL reversal is sharp.  "
             "Always maintain position-level stops regardless of sizing.",
        action=(
            "1. Add a `regime_size_multiplier` config (default 1.0 — no change).  "
            "2. In run_agent_cycle() / _process_symbol(), multiply qty by multiplier based on "
            "   current morning_regime.  "
            "3. Re-run backtest with BULL=1.0, RANGE=0.5, BEAR=0.25.  "
            "4. Compare OOS expectancy (not just PnL) before enabling."
        ),
        triggered=sizing_triggered,
    ))

    return items


# ═══════════════════════════════════════════════════════════════════════════════
# Production readiness
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_readiness(p2: dict, gate_passed: bool) -> tuple[str, list[str], list[dict]]:
    """Evaluate which capital readiness tier is currently met."""
    if not gate_passed:
        return "NONE", [
            "Phase 2 gate not passed.  No capital tier is reached.",
            "Current honest status: system is NOT ready for real capital at any tier.",
            "Phase 1 + Phase 2 must close measurement and validation gaps first.",
        ], []

    wf_oos = (p2.get("walk_forward", {})
                .get("split2_train2022_24_val2025_26", {})
                .get("val", {}))
    oos_lo = wf_oos.get("ci_lo_r")

    # Proxy max-DD from Phase 2 overall (we don't have a proper equity-curve DD in Phase 2)
    # Phase 2 overall.net_pnl / total capitalised is a rough proxy — Phase 1 portfolio endpoint
    # gives the real DD.  We note it as manual-verify.

    gates: list[dict] = []
    for tier_name, amount, description in _CAPITAL_TIERS:
        met_auto: list[str] = []
        manual_verify: list[str] = []
        blockers: list[str] = []

        if tier_name == "₹1 lakh":
            if oos_lo is not None and oos_lo > 0:
                met_auto.append(f"OOS CI lo={_f3(oos_lo)} > 0 ✓")
            else:
                blockers.append(f"OOS CI lo={_f3(oos_lo)} must be > 0")
            manual_verify.append("max-DD < 15% (check /api/v1/analytics/portfolio)")
            manual_verify.append("≥6 months forward paper-trade with backtest-comparable metrics")

        elif tier_name == "₹10 lakh":
            blockers.append("Requires ₹1L gate first (manual verify above)")
            manual_verify.append("ADV check: position sizes vs 1% of 20-day average volume")
            manual_verify.append("Stable expectancy across ≥2 regimes (Phase 2 regime cross-tab CI)")

        elif tier_name == "₹50 lakh":
            blockers.append("Requires ₹10L gate + capacity analysis")
            manual_verify.append("Live slippage vs modeled (Varsity M7 cost model) within 20%")
            manual_verify.append("≥2 stable regimes OOS (not just BULL_TRENDING)")

        elif tier_name == "₹1 crore":
            blockers.append("Requires 12-month live track record")
            manual_verify.append("Drawdown discipline demonstrated through a losing regime (not just backtested)")

        elif tier_name == "₹10 crore":
            blockers.append("Requires market-impact model + execution algorithm")
            manual_verify.append("Capacity ceiling per name (position size vs float)")
            manual_verify.append("Institutional risk controls (VaR, stress test)")

        gates.append({
            "tier":          tier_name,
            "description":   description,
            "met_auto":      met_auto,
            "manual_verify": manual_verify,
            "blockers":      blockers,
            "is_reachable":  len(blockers) == 0,
        })

    # Find highest reachable tier (all conditions met auto, zero hard blockers)
    reachable = [g["tier"] for g in gates if g["is_reachable"]]
    current_tier = reachable[-1] if reachable else "NONE"

    notes = [
        "Current honest status: system may approach ₹1L tier IF Phase 2 gate + manual verifications pass.",
        "Real-money use at any tier is a decision for the user, not this system.  "
        "All capital tiers require manual verification steps that cannot be checked programmatically.",
        "PAPER TRADING ONLY enforced in .env (PAPER_MODE=true, AGENT_PAPER_MODE=true).",
    ]
    return current_tier, notes, gates


# ═══════════════════════════════════════════════════════════════════════════════
# Report printing
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(r: Phase3Report) -> None:
    print(f"\n{_W}")
    print("  AutoTrade Pro — Phase 3 Optimization Roadmap")
    print(_W)

    # Gate
    print(f"\n── Phase 3 Gate ──────────────────────────────────────────────────────")
    status = "PASSED ✓" if r.gate_passed else "NOT PASSED ✗"
    print(f"  Status  : {status}")
    print(f"  OOS     : mean_R={_f3(r.oos_mean_r)} CI=[{_f3(r.oos_ci_lo)},{_f3(r.oos_ci_hi)}] "
          f"verdict={r.oos_verdict}")
    print(f"  Reason  : {r.gate_reason}")
    if r.regime_only:
        print("  ⚠  Edge is regime-conditional — BULL_TRENDING only.  "
              "Phase 3 item 1 = regime gating, not entry tuning.")

    if not r.gate_passed:
        print("\n  ⛔ Phase 3 is BLOCKED.  Return to Phase 2:")
        print("     • Extend paper-trading period to accumulate OOS trades")
        print("     • Investigate why OOS CI straddles zero (regime, time-period, data quality)")
        print("     • Do NOT change any entry rule, threshold, or sizing until CI excludes zero")
        print(f"\n{_W}\n")
        return

    # Improvements
    print(f"\n── Prioritized Improvement Matrix ────────────────────────────────────")
    tier_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    for tier in tier_order:
        tier_items = [x for x in r.improvements if x.tier == tier]
        if not tier_items:
            continue
        print(f"\n  ▸ {tier}")
        for item in tier_items:
            triggered_tag = " [TRIGGERED]" if item.triggered else " [not triggered]"
            print(f"    [{item.id}] {item.title}{triggered_tag}")
            print(f"         Trigger : {item.trigger}")
            print(f"         Evidence: {item.evidence[:120]}...")
            print(f"         Impact  : {item.expected_impact[:100]}...")
            print(f"         Risk    : {item.risk[:100]}...")
            print(f"         Action  : {item.action[:120]}...")

    # Non-triggered summary
    non_triggered = [x for x in r.improvements if not x.triggered]
    if non_triggered:
        print(f"\n  Not triggered (do NOT action without evidence):")
        for item in non_triggered:
            print(f"    [{item.id}] {item.title} — {item.tier}")

    # Readiness
    print(f"\n── Production Readiness ─────────────────────────────────────────────")
    print(f"  Current tier: {r.readiness_tier}")
    for note in r.readiness_notes:
        print(f"  • {note}")
    print()
    for gate in r.capital_gates:
        status = "✓ reachable" if gate["is_reachable"] else "✗ blocked"
        print(f"  {gate['tier']:<12} {status}")
        for blocker in gate["blockers"]:
            print(f"    ✗ BLOCKER: {blocker}")
        for m in gate["met_auto"]:
            print(f"    ✓ {m}")
        for m in gate["manual_verify"]:
            print(f"    ? manual: {m}")

    # Final statement
    print(f"\n── Final Statement ──────────────────────────────────────────────────")
    print(f"  {r.final_statement}")
    print(f"\n{_W}\n")


def build_final_statement(r: Phase3Report) -> str:
    triggered = [x for x in r.improvements if x.triggered]
    if not triggered:
        return (
            f"Phase 3 gate passed (OOS mean_R={_f3(r.oos_mean_r)}, CI=[{_f3(r.oos_ci_lo)},{_f3(r.oos_ci_hi)}]).  "
            "No improvement candidates are triggered by the evidence.  "
            "Extend paper-trading period and re-run Phase 2 before any optimization."
        )
    ids = ", ".join(x.id for x in triggered)
    return (
        f"Phase 3 gate {'passed' if r.gate_passed else 'NOT PASSED'} "
        f"(OOS mean_R={_f3(r.oos_mean_r)}, CI=[{_f3(r.oos_ci_lo)},{_f3(r.oos_ci_hi)}]).  "
        f"Triggered improvements: {ids}.  "
        f"Recommended action order: {', '.join(x.id + ' ' + x.title.split(' —')[0] for x in triggered)}.  "
        f"Capital readiness: {r.readiness_tier}.  "
        "No optimization should be deployed until a dedicated OOS validation confirms improvement."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def run(phase2_path: str, out_path: str | None) -> Phase3Report:
    with open(phase2_path) as fh:
        p2 = json.load(fh)

    gate_passed, regime_only, gate_reason = evaluate_gate(p2)

    wf_oos = (p2.get("walk_forward", {})
                .get("split2_train2022_24_val2025_26", {})
                .get("val", {}))

    improvements = evaluate_improvements(p2, gate_passed, regime_only)
    readiness_tier, readiness_notes, capital_gates = evaluate_readiness(p2, gate_passed)

    report = Phase3Report(
        gate_passed     = gate_passed,
        gate_reason     = gate_reason,
        oos_mean_r      = wf_oos.get("mean_r"),
        oos_ci_lo       = wf_oos.get("ci_lo_r"),
        oos_ci_hi       = wf_oos.get("ci_hi_r"),
        oos_verdict     = wf_oos.get("r_verdict", "UNCERTAIN"),
        regime_only     = regime_only,
        improvements    = improvements,
        readiness_tier  = readiness_tier,
        readiness_notes = readiness_notes,
        capital_gates   = capital_gates,
    )
    report.final_statement = build_final_statement(report)

    print_report(report)

    if out_path:
        report_dict = asdict(report)
        with open(out_path, "w") as fh:
            json.dump(report_dict, fh, indent=2, default=str)
        print(f"[phase3] Report saved → {out_path}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3 optimization roadmap evaluator")
    parser.add_argument("--phase2", required=True, help="Path to Phase 2 JSON report")
    parser.add_argument("--out",    type=str, default=None, help="Save Phase 3 JSON")
    args = parser.parse_args()

    try:
        run(args.phase2, args.out)
    except FileNotFoundError:
        print(f"ERROR: Phase 2 report not found at {args.phase2}")
        print("Run first: .venv/bin/python scripts/validate_edge.py --out results/phase2.json")
        sys.exit(1)
