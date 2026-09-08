"""STEP 2O — the V1 evaluation protocol.

No model is trained here and none is trained by the module under test. What is
tested is the part that decides WHICH rows a model may see and HOW its output
is scored — which is where a plausible-looking result usually comes from.

The central property: a training row's target must never land inside a later
block. With a one-session horizon that costs exactly one session at each
boundary, and the tests pin both the necessity and the sufficiency of that.
"""
from __future__ import annotations

import datetime as dt
import math

import pytest

from scripts import v1_protocol as P


def _sessions(n: int, start=dt.date(2020, 1, 1)) -> list:
    """n consecutive weekday sessions — a stand-in NSE calendar."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


# ── chronology ───────────────────────────────────────────────────────────────

class TestChronology:

    def test_blocks_are_strictly_ordered(self):
        s = _sessions(300)
        sp = P.fixed_split(s, s[150], s[240])
        assert not P.split_violations(s, sp)

    def test_training_never_reaches_into_validation(self):
        s = _sessions(300)
        sp = P.fixed_split(s, s[150], s[240])
        assert sp["train"][1] < sp["validation"][0]
        assert sp["validation"][1] < sp["test"][0]

    def test_unpurged_split_is_detected_as_leaking(self):
        """Without the purge the last training target IS the first validation
        session. The checker must say so rather than wave it through."""
        s = _sessions(300)
        sp = P.fixed_split(s, s[150], s[240], purge=0)
        bad = P.split_violations(s, sp)
        assert any("falls inside validation" in b for b in bad), bad

    def test_purge_of_one_is_sufficient(self):
        s = _sessions(300)
        sp = P.fixed_split(s, s[150], s[240], purge=1)
        assert P.split_violations(s, sp) == []

    def test_split_rejects_out_of_order_boundaries(self):
        s = _sessions(100)
        with pytest.raises(ValueError):
            P.fixed_split(s, s[80], s[40])

    def test_validation_cannot_start_at_the_first_session(self):
        s = _sessions(100)
        with pytest.raises(ValueError):
            P.expanding_folds(s, [s[0]])


# ── purge / embargo ──────────────────────────────────────────────────────────

class TestPurge:

    def test_purge_is_one_session(self):
        assert P.PURGE_SESSIONS == 1

    def test_default_embargo_is_zero_and_justified(self):
        assert P.DEFAULT_EMBARGO_SESSIONS == 0
        why = P.purge_rationale()
        assert "one session" in why and "near-duplicates" in why

    def test_purge_removes_exactly_the_boundary_session(self):
        s = _sessions(100)
        end, purged = P.apply_purge(s, s[49], s[50])
        assert end == s[48]
        assert purged == [s[49]]

    def test_purge_is_measured_in_sessions_not_days(self):
        """A weekend spans three calendar days and one session."""
        s = _sessions(100)
        i = next(k for k in range(1, 99) if (s[k] - s[k - 1]).days > 1)
        end, purged = P.apply_purge(s, s[i], s[i + 1])
        assert len(purged) == 1
        assert end == s[i - 1]

    def test_purge_cannot_run_off_the_start(self):
        s = _sessions(10)
        end, _ = P.apply_purge(s, s[0], s[1], purge=5)
        assert end == s[0]

    def test_larger_purge_still_leaves_no_violation(self):
        s = _sessions(300)
        sp = P.fixed_split(s, s[150], s[240], purge=5)
        assert P.split_violations(s, sp) == []


# ── expanding folds ──────────────────────────────────────────────────────────

class TestExpandingFolds:

    def test_training_window_expands(self):
        s = _sessions(400)
        folds = P.expanding_folds(s, [s[100], s[200], s[300]])
        assert all(f.train_start == s[0] for f in folds)
        ends = [f.train_end for f in folds]
        assert ends == sorted(ends) and len(set(ends)) == 3

    def test_validation_blocks_are_contiguous_and_disjoint(self):
        s = _sessions(400)
        folds = P.expanding_folds(s, [s[100], s[200], s[300]])
        assert folds[0].val_end < folds[1].val_start
        assert folds[1].val_end < folds[2].val_start
        assert folds[-1].val_end == s[-1]

    def test_no_fold_trains_on_its_own_validation(self):
        s = _sessions(400)
        for f in P.expanding_folds(s, [s[100], s[200], s[300]]):
            for d in s:
                assert not (f.contains_train(d) and f.contains_val(d))

    def test_each_fold_purges_one_session(self):
        s = _sessions(400)
        for f in P.expanding_folds(s, [s[100], s[200], s[300]]):
            assert len(f.purged) == 1
            assert f.purged[0] > f.train_end
            assert f.purged[0] < f.val_start

    def test_embargo_shifts_validation_forward(self):
        s = _sessions(400)
        a = P.expanding_folds(s, [s[200]])[0]
        b = P.expanding_folds(s, [s[200]], embargo=5)[0]
        assert b.val_start > a.val_start
        assert s.index(b.val_start) - s.index(a.val_start) == 5


# ── baselines ────────────────────────────────────────────────────────────────

class TestBaselines:

    def test_registry_has_every_required_baseline(self):
        for k in ("B0_zero", "B1_historical_mean", "B2_historical_median",
                  "B3_momentum_21d", "B4_volume_weighted_momentum",
                  "B5_majority_class", "B6_rank_by_momentum"):
            assert k in P.BASELINE_REGISTRY

    def test_zero_baseline_predicts_zero(self):
        f = P.Baselines.zero()
        assert f({"return_21d": 0.9}) == 0.0

    def test_mean_and_median_differ_on_a_skewed_sample(self):
        """The V1 cross-section is right-skewed — mean positive, median
        negative — so these two baselines are not interchangeable."""
        xs = [-0.01] * 60 + [0.05] * 40
        assert P.Baselines.historical_mean(xs)({}) > 0
        assert P.Baselines.historical_median(xs)({}) < 0

    def test_constant_baselines_carry_no_ranking_information(self):
        f = P.Baselines.historical_mean([0.01, 0.02])
        scores = [f({}) for _ in range(5)]
        assert len(set(scores)) == 1

    def test_momentum_baseline_uses_a_frozen_feature(self):
        from scripts import v1_contract as C
        assert "return_21d" in C.FEATURES
        assert P.Baselines.momentum_21d()({"return_21d": 0.07}) == 0.07

    def test_volume_gate_uses_only_frozen_features(self):
        from scripts import v1_contract as C
        assert {"return_21d", "volume_ratio_20d"} <= set(C.FEATURES)
        f = P.Baselines.volume_weighted_momentum()
        assert f({"return_21d": 0.1, "volume_ratio_20d": 1.5}) == 0.1
        assert f({"return_21d": 0.1, "volume_ratio_20d": 0.4}) == 0.0

    def test_baselines_return_none_on_a_missing_feature(self):
        assert P.Baselines.momentum_21d()({}) is None
        assert P.Baselines.volume_weighted_momentum()({"return_21d": 0.1}) is None

    def test_majority_class_is_down_when_up_rate_is_below_half(self):
        """46.7% of V1 sessions close up, so the majority class is DOWN and
        any direction accuracy must be read against ~50.9%, not 50%."""
        xs = [0.01] * 467 + [-0.01] * 533
        assert P.Baselines.majority_class(xs)({}) == 0


# ── metrics ──────────────────────────────────────────────────────────────────

class TestMetrics:

    def test_mae_and_rmse(self):
        assert P.mae([1.0, 2.0], [1.0, 4.0]) == 1.0
        assert abs(P.rmse([1.0, 2.0], [1.0, 4.0]) - math.sqrt(2)) < 1e-12

    def test_pearson_of_a_perfect_line(self):
        y = [1.0, 2.0, 3.0, 4.0]
        assert abs(P.pearson(y, [2 * v for v in y]) - 1.0) < 1e-12

    def test_pearson_of_a_constant_prediction_is_undefined(self):
        assert math.isnan(P.pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]))

    def test_spearman_ignores_magnitude(self):
        y = [1.0, 2.0, 3.0, 100.0]
        assert abs(P.spearman(y, [1.0, 2.0, 3.0, 4.0]) - 1.0) < 1e-12

    def test_spearman_handles_ties(self):
        assert not math.isnan(P.spearman([1.0, 1.0, 2.0], [3.0, 3.0, 4.0]))

    def test_balanced_accuracy_exposes_a_constant_predictor(self):
        y = [0.01] * 47 + [-0.01] * 53
        p = [-1.0] * 100                       # always DOWN
        d = P.direction_scores(y, p)
        assert abs(d["accuracy"] - 0.53) < 1e-9
        assert abs(d["balanced_accuracy"] - 0.5) < 1e-9

    def test_direction_precision_recall_for_the_up_class(self):
        y = [0.01, 0.01, -0.01, -0.01]
        p = [1.0, -1.0, 1.0, -1.0]
        d = P.direction_scores(y, p)
        assert d["precision_up"] == 0.5 and d["recall_up"] == 0.5

    def test_topk_picks_by_score_and_reports_the_realised_return(self):
        rows = [(0.9, 0.05), (0.5, -0.02), (0.1, 0.10)]
        r = P.topk_session(rows, 1)
        assert r["avg_return"] == 0.05 and r["hit_rate"] == 1.0

    def test_topk_reports_downside_and_coverage(self):
        rows = [(0.9, -0.04), (0.8, 0.02), (None, 0.5)]
        r = P.topk_session(rows, 2)
        assert r["worst"] == -0.04
        assert abs(r["coverage"] - 2 / 3) < 1e-12

    def test_topk_returns_none_when_the_session_is_too_thin(self):
        assert P.topk_session([(0.5, 0.01)], 5) is None

    def test_aggregation_is_per_session_not_per_row(self):
        """Adjacent rows share 251 of 252 lookback sessions. Pooling rows would
        treat near-duplicates as independent observations."""
        per = [P.topk_session([(0.9, 0.02), (0.1, -0.01)], 1),
               P.topk_session([(0.9, -0.06), (0.1, 0.03)], 1)]
        agg = P.aggregate_topk(per)
        assert agg["sessions_evaluated"] == 2
        assert abs(agg["mean_of_session_avg_return"] - (-0.02)) < 1e-12
        assert agg["worst_session_avg"] == -0.06

    def test_metric_groups_are_kept_separate(self):
        for g in ("return", "direction", "ranking", "trading_relevant"):
            assert P.METRIC_GROUPS[g]

    def test_portfolio_metrics_are_explicitly_excluded(self):
        """V1 has no position sizing, no costs and no holding period. A
        drawdown computed from raw next-session returns would describe a
        strategy that does not exist."""
        for m in ("max_drawdown", "sharpe_ratio", "profit_factor"):
            assert m in P.EXCLUDED_METRICS
            assert m not in sum(P.METRIC_GROUPS.values(), [])
