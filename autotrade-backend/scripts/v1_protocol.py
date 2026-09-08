"""V1 evaluation protocol — splits, baselines, metrics (Phase 2O).

DEFINITIONS AND MECHANISM ONLY. Nothing here trains a model, and nothing here
is executed against the materialized dataset in this phase. Every function is
pure so the protocol can be tested without a database and without a fit.

The three parts:

* `expanding_folds` / `fixed_split` — chronological splits over the SESSION
  INDEX, never over row counts and never over calendar dates.
* `Baselines` — the non-ML benchmarks any model must beat before it is
  interesting.
* `metrics` — return, direction, ranking and trading-relevant measures, kept
  separate because they answer different questions.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics as st
from dataclasses import dataclass, field

# ── purge ────────────────────────────────────────────────────────────────────
#
# The target of a row at prediction session D is realised at session D+1.
# A split placed at a boundary must therefore drop ONE prediction session from
# the end of every training block:
#
#     train rows with prediction_session == T_end have their target at T_end+1,
#     which is the FIRST validation session. Standing at the end of T_end —
#     the moment the model would actually be fitted — that outcome has not
#     happened yet.
#
# So the purge is not about label overlap between train and validation (there
# is none: train targets end at T_end+1, validation targets start at
# V_start+1). It is about DEPLOYABILITY: without it the training set contains
# an observation that could not have been known at training time.
PURGE_SESSIONS = 1

# An embargo would additionally drop sessions from the START of validation.
# It buys nothing here — see `purge_rationale()` — and the default is 0.
DEFAULT_EMBARGO_SESSIONS = 0


def purge_rationale() -> str:
    return (
        "Horizon is exactly one session, so training targets (<= T_end+1) and "
        "validation targets (>= V_start+1 = T_end+2) never overlap. The purge "
        "of 1 session exists because the last training row's OUTCOME lands "
        "inside the validation period and is unknown at the fit point. An "
        "embargo (dropping sessions from the start of validation) addresses "
        "overlapping LABEL windows, which a 1-session horizon does not have; "
        "the residual issue is that adjacent rows share 251 of 252 lookback "
        "sessions and are near-duplicates. That is an independence problem in "
        "the metric, not leakage, and it is handled by aggregating per session "
        "rather than per row."
    )


@dataclass(frozen=True)
class Fold:
    name: str
    train_start: dt.date
    train_end: dt.date          # inclusive, AFTER the purge
    val_start: dt.date
    val_end: dt.date            # inclusive
    purged: list = field(default_factory=list)

    def contains_train(self, d: dt.date) -> bool:
        return self.train_start <= d <= self.train_end

    def contains_val(self, d: dt.date) -> bool:
        return self.val_start <= d <= self.val_end


def _idx(sessions: list, d: dt.date) -> int:
    return sessions.index(d)


def apply_purge(sessions: list, train_end: dt.date, val_start: dt.date,
                *, purge: int = PURGE_SESSIONS) -> tuple:
    """Move `train_end` back `purge` sessions. Returns (new_end, purged[])."""
    i = _idx(sessions, train_end)
    j = max(0, i - purge)
    return sessions[j], sessions[j + 1: i + 1]


def expanding_folds(sessions: list, boundaries: list,
                    *, purge: int = PURGE_SESSIONS,
                    embargo: int = DEFAULT_EMBARGO_SESSIONS) -> list:
    """Walk-forward folds: training grows, validation moves forward one block.

    `boundaries` are the first session of each validation block, in order. The
    training block always starts at the first available session — an expanding
    window, not a sliding one, because a sliding window would throw away the
    only 2020 crash this dataset contains.
    """
    out = []
    for k, vs in enumerate(boundaries):
        i = _idx(sessions, vs)
        if i == 0:
            raise ValueError(f"validation cannot start at the first session ({vs})")
        raw_end = sessions[i - 1]
        train_end, purged = apply_purge(sessions, raw_end, vs, purge=purge)
        vstart = sessions[i + embargo] if embargo else vs
        vend = (sessions[_idx(sessions, boundaries[k + 1]) - 1]
                if k + 1 < len(boundaries) else sessions[-1])
        out.append(Fold(f"fold_{k + 1}", sessions[0], train_end, vstart, vend,
                        purged))
    return out


def fixed_split(sessions: list, val_start: dt.date, test_start: dt.date,
                *, purge: int = PURGE_SESSIONS) -> dict:
    """A single train / validation / test partition, purged at both boundaries."""
    i_v, i_t = _idx(sessions, val_start), _idx(sessions, test_start)
    if not 0 < i_v < i_t:
        raise ValueError("need 0 < val_start < test_start")
    train_end, p1 = apply_purge(sessions, sessions[i_v - 1], val_start, purge=purge)
    val_end, p2 = apply_purge(sessions, sessions[i_t - 1], test_start, purge=purge)
    return {
        "train": (sessions[0], train_end),
        "validation": (val_start, val_end),
        "test": (test_start, sessions[-1]),
        "purged": p1 + p2,
    }


def split_violations(sessions: list, split: dict) -> list:
    """Every way a chronological split can be wrong, checked explicitly."""
    bad = []
    tr, va, te = split["train"], split["validation"], split["test"]
    if not tr[0] <= tr[1] < va[0] <= va[1] < te[0] <= te[1]:
        bad.append("blocks are not strictly ordered in time")
    # a training row's target must never land in validation or test
    last_train_target = sessions[_idx(sessions, tr[1]) + 1]
    if last_train_target >= va[0]:
        bad.append(f"last training target {last_train_target} falls inside validation")
    last_val_target = sessions[_idx(sessions, va[1]) + 1]
    if last_val_target >= te[0]:
        bad.append(f"last validation target {last_val_target} falls inside test")
    return bad


# ── baselines ────────────────────────────────────────────────────────────────
#
# Definitions only. Each takes the TRAINING rows (to fit its single statistic,
# where it has one) and returns a prediction function over feature dicts. None
# of them is a model; all of them are the bar a model has to clear.

class Baselines:

    @staticmethod
    def zero(_train=None):
        """B0 — predict 0.00% for every row. The honest null: the median
        next-session return is negative, so 'always zero' already beats
        'always up'."""
        return lambda row: 0.0

    @staticmethod
    def historical_mean(train_returns):
        """B1 — the training-period mean return, applied to every row.
        Constant, so it has zero cross-sectional information and any ranking
        metric on it is exactly chance."""
        m = st.mean(train_returns) if train_returns else 0.0
        return lambda row: m

    @staticmethod
    def historical_median(train_returns):
        """B2 — the training-period median. Different from B1 because the
        cross-section is right-skewed (mean +0.0008, median -0.0005)."""
        m = st.median(train_returns) if train_returns else 0.0
        return lambda row: m

    @staticmethod
    def momentum_21d(_train=None):
        """B3 — predict the sign and scale of the trailing 21-session return.
        The first baseline carrying cross-sectional information, and the one
        the intraday-reversal finding predicts should FAIL."""
        return lambda row: row.get("return_21d")

    @staticmethod
    def volume_weighted_momentum(_train=None):
        """B4 — 21-session momentum gated by a volume expansion. Justified by
        the frozen features `return_21d` and `volume_ratio_20d`; no new family
        is introduced."""
        def f(row):
            r, v = row.get("return_21d"), row.get("volume_ratio_20d")
            if r is None or v is None:
                return None
            return r * (1.0 if v >= 1.0 else 0.0)
        return f

    @staticmethod
    def majority_class(train_returns):
        """B5 — for the direction task: always predict the majority class.
        With 46.7% up, that class is DOWN, and 'accuracy' for any direction
        model must be read against this number, not against 50%."""
        up = sum(1 for r in train_returns if r > 0)
        return lambda row: 1 if up * 2 > len(train_returns) else 0

    @staticmethod
    def rank_by_momentum(_train=None):
        """B6 — cross-sectional ranking baseline: rank each session's symbols
        by trailing 21-session return, highest first."""
        return lambda row: row.get("return_21d")


BASELINE_REGISTRY = {
    "B0_zero": Baselines.zero,
    "B1_historical_mean": Baselines.historical_mean,
    "B2_historical_median": Baselines.historical_median,
    "B3_momentum_21d": Baselines.momentum_21d,
    "B4_volume_weighted_momentum": Baselines.volume_weighted_momentum,
    "B5_majority_class": Baselines.majority_class,
    "B6_rank_by_momentum": Baselines.rank_by_momentum,
}


# ── metrics ──────────────────────────────────────────────────────────────────

def mae(y, p):
    return sum(abs(a - b) for a, b in zip(y, p)) / len(y)


def rmse(y, p):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(y, p)) / len(y))


def pearson(y, p):
    n = len(y)
    my, mp = sum(y) / n, sum(p) / n
    num = sum((a - my) * (b - mp) for a, b in zip(y, p))
    dy = math.sqrt(sum((a - my) ** 2 for a in y))
    dp = math.sqrt(sum((b - mp) ** 2 for b in p))
    return num / (dy * dp) if dy > 0 and dp > 0 else float("nan")


def _ranks(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(y, p):
    """Rank IC. The right correlation for a ranking task — Pearson on raw
    next-session returns is dominated by the fat tail."""
    return pearson(_ranks(y), _ranks(p))


def direction_scores(y, p):
    """Accuracy, balanced accuracy, and precision/recall for the UP class.

    Balanced accuracy is not optional here: the classes are 46.7/50.9, so a
    constant DOWN prediction scores 50.9% plain accuracy and 50.0% balanced.
    """
    tp = sum(1 for a, b in zip(y, p) if a > 0 and b > 0)
    fp = sum(1 for a, b in zip(y, p) if a <= 0 < b)
    fn = sum(1 for a, b in zip(y, p) if a > 0 >= b)
    tn = sum(1 for a, b in zip(y, p) if a <= 0 and b <= 0)
    acc = (tp + tn) / len(y)
    tpr = tp / (tp + fn) if tp + fn else float("nan")
    tnr = tn / (tn + fp) if tn + fp else float("nan")
    return {
        "accuracy": acc,
        "balanced_accuracy": (tpr + tnr) / 2 if not (math.isnan(tpr) or math.isnan(tnr)) else float("nan"),
        "precision_up": tp / (tp + fp) if tp + fp else float("nan"),
        "recall_up": tpr,
    }


def topk_session(rows, k: int):
    """Per-session top-k measures. `rows` are (score, realised_return) for ONE
    prediction session — the unit of aggregation is the SESSION, because
    adjacent rows within a symbol are near-duplicates."""
    usable = [(s, r) for s, r in rows if s is not None]
    if len(usable) < k:
        return None
    picked = sorted(usable, key=lambda x: -x[0])[:k]
    rets = [r for _s, r in picked]
    return {
        "avg_return": sum(rets) / k,
        "median_return": st.median(rets),
        "hit_rate": sum(1 for r in rets if r > 0) / k,
        "worst": min(rets),
        "coverage": len(usable) / len(rows),
    }


def aggregate_topk(per_session: list) -> dict:
    """Aggregate per-session top-k results across the evaluation period."""
    ok = [x for x in per_session if x]
    if not ok:
        return {}
    avg = [x["avg_return"] for x in ok]
    return {
        "sessions_evaluated": len(ok),
        "mean_of_session_avg_return": sum(avg) / len(avg),
        "median_of_session_avg_return": st.median(avg),
        "mean_hit_rate": sum(x["hit_rate"] for x in ok) / len(ok),
        "worst_decile_of_session_avg": sorted(avg)[max(0, len(avg) // 10 - 1)],
        "worst_session_avg": min(avg),
        "mean_coverage": sum(x["coverage"] for x in ok) / len(ok),
    }


METRIC_GROUPS = {
    "return": ["mae", "rmse", "pearson"],
    "direction": ["accuracy", "balanced_accuracy", "precision_up", "recall_up"],
    "ranking": ["spearman_rank_ic", "topk_avg_return", "topk_hit_rate",
                "topk_worst", "coverage"],
    "trading_relevant": ["mean_of_session_avg_return",
                         "median_of_session_avg_return",
                         "worst_decile_of_session_avg", "worst_session_avg"],
}

# Maximum drawdown is deliberately ABSENT. It is a portfolio path statistic and
# this dataset has no portfolio: no position sizing, no costs, no capital
# constraint, no holding period beyond one session. Reporting a drawdown from
# raw next-session returns would describe a strategy that does not exist.
EXCLUDED_METRICS = {
    "max_drawdown": "requires an explicit portfolio simulation, which V1 has none of",
    "sharpe_ratio": "same — needs a return stream from a sized, costed strategy",
    "profit_factor": "implies executed trades; V1 targets are RAW outcomes",
}
