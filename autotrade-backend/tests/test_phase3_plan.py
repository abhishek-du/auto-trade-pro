"""Tests for phase3_plan.py gate + improvement evaluation logic.

Uses synthetic Phase 2 report dicts so no DB or network is required.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.phase3_plan import evaluate_gate, evaluate_improvements, evaluate_readiness


def _oos_block(mean_r, lo, hi, verdict, n=50):
    return {"n": n, "mean_r": mean_r, "ci_lo_r": lo, "ci_hi_r": hi, "r_verdict": verdict}


def _p2_minimal(oos_lo=0.05, oos_hi=0.25, oos_rv="POSITIVE", overall_rv="POSITIVE",
                edge_status="EDGE CONFIRMED") -> dict:
    oos_block = _oos_block(0.12, oos_lo, oos_hi, oos_rv)
    return {
        "overall":    {"mean_r": 0.10, "r_verdict": overall_rv, "net_pnl": 50000},
        "verdict":    {"edge_status": edge_status},
        "walk_forward": {
            "split1_train2022_23_val2024": {
                "train": _oos_block(0.15, 0.05, 0.25, "POSITIVE"),
                "val":   _oos_block(0.10, 0.01, 0.19, "POSITIVE"),
            },
            "split2_train2022_24_val2025_26": {
                "train": _oos_block(0.12, 0.04, 0.20, "POSITIVE"),
                "val":   oos_block,
            },
        },
        "regime_crosstab": {},
        "confidence_buckets": {"_monotonic_check": {"is_monotonic": None}},
        "hub_unshadowed": {"n": 0, "mean_r": None, "r_verdict": "UNCERTAIN",
                           "ci_lo_r": None, "ci_hi_r": None},
        "exit_policies": {},
        "by_strategy": {},
    }


class TestGateEvaluation:
    def test_passes_when_oos_ci_excludes_zero(self):
        p2 = _p2_minimal(oos_lo=0.02, oos_hi=0.18)
        passed, regime_only, reason = evaluate_gate(p2)
        assert passed
        assert not regime_only
        assert "PASSED" in reason

    def test_fails_when_oos_ci_straddles_zero(self):
        p2 = _p2_minimal(oos_lo=-0.05, oos_hi=0.15, oos_rv="UNCERTAIN",
                         overall_rv="UNCERTAIN", edge_status="NO CONFIRMED EDGE")
        passed, regime_only, reason = evaluate_gate(p2)
        assert not passed
        assert not regime_only

    def test_passes_conditional_when_regime_only(self):
        p2 = _p2_minimal(oos_lo=-0.01, oos_hi=0.20, oos_rv="UNCERTAIN",
                         overall_rv="POSITIVE",
                         edge_status="EDGE CONDITIONAL ON REGIME")
        passed, regime_only, reason = evaluate_gate(p2)
        assert passed
        assert regime_only
        assert "conditional" in reason.lower()

    def test_fails_when_oos_is_negative(self):
        p2 = _p2_minimal(oos_lo=-0.20, oos_hi=-0.02, oos_rv="NEGATIVE",
                         overall_rv="NEGATIVE", edge_status="NO EDGE")
        passed, _, _ = evaluate_gate(p2)
        assert not passed

    def test_passes_when_oos_lo_exactly_zero(self):
        # lo=0.0 is not > 0; should fail
        p2 = _p2_minimal(oos_lo=0.0, oos_hi=0.15, oos_rv="UNCERTAIN",
                         overall_rv="UNCERTAIN", edge_status="NO CONFIRMED EDGE")
        passed, _, _ = evaluate_gate(p2)
        assert not passed


class TestImprovementEvaluation:
    def _minimal_with_ct(self, bt_r=0.20, bear_r=-0.10):
        """Build a p2 dict with regime cross-tab that has enough N."""
        p2 = _p2_minimal()
        p2["regime_crosstab"] = {
            "TREND_BREAKOUT_LONG": {
                "BULL_TRENDING": {"n": 50, "mean_r": bt_r, "wr": 0.60, "verdict": "POSITIVE"},
                "BEAR_TRENDING": {"n": 35, "mean_r": bear_r, "wr": 0.35, "verdict": "NEGATIVE"},
                "RANGE":         {"n": 40, "mean_r": -0.05, "wr": 0.42, "verdict": "UNCERTAIN"},
            }
        }
        return p2

    def test_regime_gating_triggered_when_bull_dominant(self):
        p2 = self._minimal_with_ct(bt_r=0.20, bear_r=-0.12)
        items = evaluate_improvements(p2, gate_passed=True, regime_only=True)
        r1 = next(x for x in items if x.id == "R1")
        assert r1.triggered
        assert r1.tier in ("HIGH", "CRITICAL")

    def test_regime_gating_not_triggered_when_all_positive(self):
        p2 = self._minimal_with_ct(bt_r=0.15, bear_r=0.10)
        items = evaluate_improvements(p2, gate_passed=True, regime_only=False)
        r1 = next(x for x in items if x.id == "R1")
        assert not r1.triggered

    def test_confidence_triggered_when_monotonic_and_gap_large(self):
        p2 = _p2_minimal()
        p2["confidence_buckets"] = {
            "50-59": {"n": 40, "mean_r": 0.05, "r_verdict": "UNCERTAIN"},
            "60-69": {"n": 45, "mean_r": 0.12, "r_verdict": "POSITIVE"},
            "70-79": {"n": 30, "mean_r": 0.22, "r_verdict": "POSITIVE"},
            "_monotonic_check": {"is_monotonic": True},
        }
        p2["overall"]["mean_r"] = 0.10
        items = evaluate_improvements(p2, gate_passed=True, regime_only=False)
        r2 = next(x for x in items if x.id == "R2")
        assert r2.triggered

    def test_confidence_not_triggered_when_not_monotonic(self):
        p2 = _p2_minimal()
        p2["confidence_buckets"] = {
            "60-69": {"n": 50, "mean_r": 0.08},
            "70-79": {"n": 40, "mean_r": 0.05},
            "_monotonic_check": {"is_monotonic": False},
        }
        items = evaluate_improvements(p2, gate_passed=True, regime_only=False)
        r2 = next(x for x in items if x.id == "R2")
        assert not r2.triggered

    def test_hub_not_triggered_when_insufficient_n(self):
        p2 = _p2_minimal()
        p2["hub_unshadowed"] = {"n": 10, "mean_r": 0.30, "r_verdict": "POSITIVE",
                                "ci_lo_r": 0.05, "ci_hi_r": 0.55}
        items = evaluate_improvements(p2, gate_passed=True, regime_only=False)
        r3 = next(x for x in items if x.id == "R3")
        assert not r3.triggered

    def test_hub_triggered_when_significant(self):
        p2 = _p2_minimal()
        p2["hub_unshadowed"] = {"n": 50, "mean_r": -0.20, "r_verdict": "NEGATIVE",
                                "ci_lo_r": -0.35, "ci_hi_r": -0.05}
        items = evaluate_improvements(p2, gate_passed=True, regime_only=False)
        r3 = next(x for x in items if x.id == "R3")
        assert r3.triggered
        assert r3.tier == "HIGH"

    def test_exit_triggered_when_alternative_wins(self):
        p2 = _p2_minimal()
        p2["exit_policies"] = {
            "current":      {"mean_r": 0.10, "ci_lo_r": 0.01, "n": 100},
            "full_trail":   {"mean_r": 0.22, "ci_lo_r": 0.08, "n": 98},
            "be_after_1r":  {"mean_r": 0.05, "ci_lo_r": -0.02, "n": 97},
        }
        items = evaluate_improvements(p2, gate_passed=True, regime_only=False)
        r4 = next(x for x in items if x.id == "R4")
        assert r4.triggered
        assert "full_trail" in r4.evidence

    def test_all_5_candidates_always_returned(self):
        p2 = _p2_minimal()
        items = evaluate_improvements(p2, gate_passed=True, regime_only=False)
        ids = {x.id for x in items}
        assert ids == {"R1", "R2", "R3", "R4", "R5"}


class TestReadiness:
    def test_none_when_gate_not_passed(self):
        p2 = _p2_minimal(oos_lo=-0.05, oos_hi=0.10, oos_rv="UNCERTAIN",
                         overall_rv="UNCERTAIN", edge_status="NO CONFIRMED EDGE")
        tier, notes, gates = evaluate_readiness(p2, gate_passed=False)
        assert tier == "NONE"
        assert any("not ready" in n.lower() for n in notes)

    def test_reachable_1l_when_oos_ci_positive(self):
        p2 = _p2_minimal(oos_lo=0.03, oos_hi=0.20)
        tier, notes, gates = evaluate_readiness(p2, gate_passed=True)
        tier_1l = next(g for g in gates if g["tier"] == "₹1 lakh")
        assert tier_1l["is_reachable"]

    def test_higher_tiers_blocked(self):
        p2 = _p2_minimal(oos_lo=0.03, oos_hi=0.20)
        tier, notes, gates = evaluate_readiness(p2, gate_passed=True)
        for gate in gates:
            if gate["tier"] != "₹1 lakh":
                assert not gate["is_reachable"], f"{gate['tier']} should be blocked"

    def test_paper_only_note_always_present(self):
        p2 = _p2_minimal(oos_lo=0.05)
        _, notes, _ = evaluate_readiness(p2, gate_passed=True)
        assert any("PAPER" in n.upper() for n in notes)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
