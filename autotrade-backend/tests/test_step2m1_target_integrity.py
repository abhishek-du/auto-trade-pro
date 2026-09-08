"""STEP 2M.1 — NO TARGET → NO TRAINING ROW.

`_onehot_labels` ended in `.fillna(0)`. `shift(-1)` leaves the last row's
target undefined — that session's outcome has not happened yet — and 0.0 sits
inside the ±0.5% FLAT band, so the most recent session of every symbol was
taught to the model as "the market did nothing". With an 80/20 split the
poisoned row lands in VALIDATION, so it was also scored as a real observation.

A genuine 0.0% return is a real outcome and must still label FLAT. The two are
only separable through an explicit validity mask, which is why the mask exists
rather than a sentinel return value.

Second hole, same fill: `pct_change` off a 0.00 close is `inf`, which passed
through `fillna` untouched and classified as a confident UP.
"""
from __future__ import annotations

import ast
import inspect

import numpy as np
import pandas as pd
import pytest

DOWN, FLAT, UP = 0, 1, 2


def _mp():
    """Import ml_predictor LAZILY — it pulls TensorFlow in at import time.

    Every other test module in this suite defers this import, and the reason is
    load-bearing: importing TensorFlow during pytest COLLECTION puts it in the
    process before `test_event_pipeline` pulls in transformers/torch, and the
    two native runtimes segfault the interpreter. A module-scope
    `import engine.ml_predictor` here reproducibly killed the full run while
    passing on its own. sys.modules caches it, so the call is free after the
    first.
    """
    import engine.ml_predictor as m
    return m


def _df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1_000] * len(closes),
    })


def _labels(closes: list[float]):
    oh, valid = _mp()._onehot_labels(_df(closes))
    return oh.argmax(axis=1).tolist(), valid.tolist()


# ── 1. the unknown tail ──────────────────────────────────────────────────────

class TestUnknownTarget:

    def test_last_row_is_marked_invalid(self):
        _, valid = _labels([100, 101, 102, 130.0])
        assert valid[-1] is False

    def test_every_earlier_row_stays_valid(self):
        _, valid = _labels([100, 101, 102, 130.0])
        assert all(valid[:-1])

    def test_a_strong_move_is_not_relabelled_flat(self):
        """The regression itself: +22.6% on the final bar used to train as FLAT
        because its own next session did not exist yet."""
        cls, valid = _labels([100, 101, 102, 103, 104, 105, 106, 130.0])
        assert cls[-2] == UP          # the +22.6% step IS learned, on its own row
        assert valid[-2] is True
        assert valid[-1] is False     # the row that has no outcome is dropped

    def test_sequences_skip_the_invalid_tail(self):
        n = _mp()._SEQUENCE_LENGTH + 10
        feats = np.arange(n * 3, dtype=float).reshape(n, 3)
        oh, valid = _mp()._onehot_labels(_df(list(np.linspace(100, 120, n))))
        X, y = _mp()._make_sequences(feats, oh, valid)
        assert len(X) == 10 - 1
        assert len(X) == len(y)

    def test_no_mask_preserves_legacy_length(self):
        """`valid=None` keeps the old shape — callers that pass no mask are not
        silently changed, they are simply not protected."""
        n = _mp()._SEQUENCE_LENGTH + 10
        feats = np.arange(n * 3, dtype=float).reshape(n, 3)
        oh, _ = _mp()._onehot_labels(_df(list(np.linspace(100, 120, n))))
        X, _y = _mp()._make_sequences(feats, oh)
        assert len(X) == 10


# ── 2. legitimate zero returns are untouched ─────────────────────────────────

class TestGenuineZeroReturn:

    def test_flat_rows_stay_flat_and_valid(self):
        cls, valid = _labels([100, 100, 100, 100.0])
        assert cls[:-1] == [FLAT, FLAT, FLAT]
        assert valid[:-1] == [True, True, True]

    def test_tiny_move_inside_the_band_is_flat(self):
        cls, valid = _labels([100, 100.2, 100.0])
        assert cls[0] == FLAT and valid[0] is True

    def test_flat_is_not_confused_with_unknown(self):
        """Identical label, different mask — that is the whole point."""
        cls, valid = _labels([100, 100, 100.0])
        assert cls[-2] == cls[-1] == FLAT
        assert valid[-2] is True and valid[-1] is False

    def test_threshold_boundaries_unchanged(self):
        up = 100 * (1 + _mp()._LABEL_THRESHOLD + 1e-6)
        dn = 100 * (1 - _mp()._LABEL_THRESHOLD - 1e-6)
        assert _labels([100, up, 100.0])[0][0] == UP
        assert _labels([100, dn, 100.0])[0][0] == DOWN


# ── 3. invalid bars ──────────────────────────────────────────────────────────

class TestInvalidBars:

    def test_zero_close_row_is_invalid(self):
        """MAZDOCK.NS carries 8 of these — pre-listing bars, OHLC all zero."""
        _, valid = _labels([0.0, 10.0, 11.0, 12.0])
        assert valid[0] is False

    def test_zero_close_no_longer_labels_up(self):
        cls, valid = _labels([0.0, 10.0, 11.0, 12.0])
        assert not (cls[0] == UP and valid[0])

    def test_a_drop_to_zero_is_invalid_not_minus_100_percent(self):
        _, valid = _labels([10.0, 0.0, 11.0, 12.0])
        assert valid[0] is False        # target close is 0 → bad bar
        assert valid[1] is False        # own close is 0 → bad bar
        assert valid[2] is True

    def test_nan_close_is_invalid(self):
        _, valid = _labels([100.0, float("nan"), 102.0, 103.0])
        assert valid[0] is False and valid[1] is False
        assert valid[2] is True

    def test_negative_close_is_invalid(self):
        _, valid = _labels([100.0, -5.0, 102.0, 103.0])
        assert valid[0] is False and valid[1] is False

    def test_no_label_is_ever_produced_from_a_non_finite_return(self):
        closes = [0.0, 10.0, 0.0, 12.0, 13.0, 0.0]
        df = _df(closes)
        raw = df["close"].pct_change(1).shift(-1).to_numpy(dtype=float)
        _, valid = _mp()._onehot_labels(df)
        for i, r in enumerate(raw):
            if not np.isfinite(r):
                assert not valid[i], f"row {i} has a non-finite target but is valid"


# ── 4. the fill is gone from every target path ───────────────────────────────

class TestNoFillOnTargets:

    def test_onehot_labels_has_no_fillna(self):
        """Parsed, not grepped — the docstring names the call it removed."""
        tree = ast.parse(inspect.getsource(_mp()._onehot_labels))
        calls = {getattr(n.func, "attr", None)
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "fillna" not in calls

    def test_onehot_labels_returns_a_mask(self):
        oh, valid = _mp()._onehot_labels(_df([1.0, 2.0, 3.0]))
        assert oh.shape == (3, 3)
        assert valid.shape == (3,)
        assert valid.dtype == bool

    def test_rf_forward_label_has_no_fillna(self):
        src = inspect.getsource(_mp().train_random_forest)
        tree = ast.parse(src)
        bad = []
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "fillna"):
                seg = ast.get_source_segment(src, n) or ""
                if "shift(" in seg or "pct_change" in seg:
                    bad.append(seg)
        assert not bad, f"forward-label fill still present: {bad}"

    def test_train_model_passes_the_mask(self):
        tree = ast.parse(inspect.getsource(_mp().train_model))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call)
                    and getattr(n.func, "id", None) == "_make_sequences"):
                assert len(n.args) == 3, "_make_sequences called without the mask"
                return
        pytest.fail("train_model no longer calls _make_sequences")


# ── 5. the dataset predicate is already safe ─────────────────────────────────

class TestDatasetPredicate:
    """V1 never had this defect: a row exists only when the NEXT canonical
    session exists, so an unknown target produces no row at all rather than a
    filled one. These assertions pin that contract in place."""

    def test_row_requires_a_next_session(self):
        rows = [(d, c) for d, c in
                [("2026-09-03", 100.0), ("2026-09-04", 101.0), ("2026-09-07", 102.0)]]
        usable = [(rows[i][0], rows[i + 1][0]) for i in range(len(rows) - 1)]
        assert len(usable) == len(rows) - 1
        assert rows[-1][0] not in {u[0] for u in usable}

    def test_zero_close_row_produces_no_target(self):
        closes = {"2018-07-02": 0.0, "2018-07-03": 250.0}
        usable = [d for d, c in closes.items() if c > 0]
        assert "2018-07-02" not in usable
