"""V2 defers profit management. It does NOT weaken risk protection.

Phase 24 measured the signalled subset at net -0.052% held 60 minutes, +0.054%
at 120 and +0.342% to the session close, replicated out of sample. V2 tests
whether the exit horizon is what loses that. The danger in any change of this
shape is that "hold longer" quietly becomes "hold through a loss", so these
tests are weighted toward proving the opposite: that Layer 1 and Layer 2 fire at
every hold duration in both modes.
"""
from __future__ import annotations

import datetime as dt

import pytest

from engine import exit_policy as ep

T0 = dt.datetime(2026, 8, 27, 4, 0)          # UTC-naive, as the whole codebase is
EARLY = T0 + dt.timedelta(minutes=30)        # inside a 120m window
LATE = T0 + dt.timedelta(minutes=121)        # past it


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(ep, "strategy_mode", lambda: ep.MODE_V2)
    monkeypatch.setattr(ep, "is_v2", lambda: True)
    monkeypatch.setattr(ep, "min_hold_minutes", lambda: 120.0)


@pytest.fixture
def control(monkeypatch):
    monkeypatch.setattr(ep, "strategy_mode", lambda: ep.MODE_CONTROL)
    monkeypatch.setattr(ep, "is_v2", lambda: False)


# ── A. CONTROL preserves existing behaviour ─────────────────────────────────

class TestControlChangesNothing:
    @pytest.mark.parametrize("reason", sorted(ep._FAMILY_BY_REASON))
    @pytest.mark.parametrize("now", [EARLY, LATE])
    def test_every_exit_fires_at_every_age(self, control, reason, now):
        allowed, _, _ = ep.exit_allowed(reason, T0, now)
        assert allowed, f"CONTROL must never defer {reason}"

    def test_an_unknown_mode_string_reads_as_control(self, monkeypatch):
        class _S:
            TRADING_STRATEGY_MODE = "v3-experimental"

        monkeypatch.setattr("utils.config.settings", _S)
        assert ep.strategy_mode() == ep.MODE_CONTROL

    def test_a_missing_setting_reads_as_control(self, monkeypatch):
        monkeypatch.setattr("utils.config.settings", object())
        assert ep.strategy_mode() == ep.MODE_CONTROL


# ── B. V2: what is deferred, and what is emphatically not ───────────────────

class TestHardStopIsNeverDeferred:
    """Layer 1. The single most important property of this whole change."""

    @pytest.mark.parametrize("reason", ["STOP_LOSS", "MARKET_SHOCK_FLATTEN"])
    @pytest.mark.parametrize("held", [0, 1, 30, 60, 119])
    def test_fires_at_any_age_inside_the_hold_window(self, v2, reason, held):
        allowed, family, _ = ep.exit_allowed(reason, T0, T0 + dt.timedelta(minutes=held))
        assert allowed, f"{reason} was deferred at {held}m — V2 must never do this"
        assert family == ep.ExitFamily.HARD_STOP


class TestSetupInvalidationIsNeverDeferred:
    """Layer 2. The thesis stopped being true; horizon is irrelevant."""

    @pytest.mark.parametrize("reason", [
        "CONFIRMATION_LOST", "SECTOR_REVERSAL", "POST_EVENT_REVERSAL", "LLM_DYNAMIC_EXIT",
    ])
    @pytest.mark.parametrize("held", [0, 5, 119])
    def test_fires_inside_the_hold_window(self, v2, reason, held):
        allowed, family, _ = ep.exit_allowed(reason, T0, T0 + dt.timedelta(minutes=held))
        assert allowed
        assert family == ep.ExitFamily.SETUP_INVALIDATION


class TestProfitManagementIsDeferredThenReleased:
    @pytest.mark.parametrize("reason", [
        "TAKE_PROFIT", "TRAIL_STOP", "EXHAUSTION", "T1_REVERSAL_EXIT", "T1_HIT", "T2_HIT",
    ])
    def test_suppressed_before_the_minimum_hold(self, v2, reason):
        allowed, family, note = ep.exit_allowed(reason, T0, EARLY)
        assert not allowed
        assert family == ep.ExitFamily.PROFIT_MANAGEMENT
        assert "120" in note and "30" in note, f"the note must say why: {note}"

    @pytest.mark.parametrize("reason", [
        "TAKE_PROFIT", "TRAIL_STOP", "EXHAUSTION", "T1_REVERSAL_EXIT", "T1_HIT", "T2_HIT",
    ])
    def test_eligible_after_the_minimum_hold(self, v2, reason):
        allowed, _, _ = ep.exit_allowed(reason, T0, LATE)
        assert allowed

    def test_the_boundary_is_inclusive(self, v2):
        """At exactly the horizon the exit is live, not one tick later."""
        allowed, _, _ = ep.exit_allowed("TAKE_PROFIT", T0, T0 + dt.timedelta(minutes=120))
        assert allowed

    def test_one_minute_earlier_is_still_deferred(self, v2):
        allowed, _, _ = ep.exit_allowed("TAKE_PROFIT", T0, T0 + dt.timedelta(minutes=119))
        assert not allowed


class TestMaxHoldStillApplies:
    """Layer 4. V2 may extend a horizon; it may not hold past the squareoff."""

    @pytest.mark.parametrize("held", [0, 30, 119, 400])
    def test_squareoff_is_never_deferred(self, v2, held):
        allowed, family, _ = ep.exit_allowed(
            "MIS_SQUAREOFF", T0, T0 + dt.timedelta(minutes=held))
        assert allowed
        assert family == ep.ExitFamily.MARKET_SQUAREOFF

    @pytest.mark.parametrize("reason", ["STALE_EXIT", "POST_EVENT_TIME_EXIT"])
    def test_time_exits_are_never_deferred(self, v2, reason):
        allowed, family, _ = ep.exit_allowed(reason, T0, EARLY)
        assert allowed
        assert family == ep.ExitFamily.TIME_EXIT

    def test_multi_day_positions_are_unaffected(self, v2):
        """A 45-day stale exit is a TIME_EXIT and outside the experiment."""
        old = T0 - dt.timedelta(days=45)
        allowed, family, _ = ep.exit_allowed("STALE_EXIT", old, T0)
        assert allowed and family == ep.ExitFamily.TIME_EXIT


class TestOperatorActions:
    @pytest.mark.parametrize("reason", ["MANUAL", "KILL_SWITCH", "REALLOCATED"])
    def test_are_never_deferred(self, v2, reason):
        allowed, family, _ = ep.exit_allowed(reason, T0, EARLY)
        assert allowed
        assert family == ep.ExitFamily.CONTROL_EXISTING


# ── C. Fail-safe direction ──────────────────────────────────────────────────

class TestNothingCanTrapAPosition:
    """Every ambiguity must resolve toward letting the exit happen."""

    def test_unknown_reason_is_not_gated(self, v2):
        allowed, family, _ = ep.exit_allowed("SOME_NEW_EXIT_ADDED_LATER", T0, EARLY)
        assert allowed
        assert family == ep.ExitFamily.CONTROL_EXISTING

    def test_missing_opened_at_is_not_gated(self, v2):
        allowed, _, _ = ep.exit_allowed("TAKE_PROFIT", None, EARLY)
        assert allowed

    def test_a_zero_horizon_disables_the_gate(self, monkeypatch, v2):
        monkeypatch.setattr(ep, "min_hold_minutes", lambda: 0.0)
        allowed, _, _ = ep.exit_allowed("TAKE_PROFIT", T0, EARLY)
        assert allowed

    def test_an_exception_inside_the_gate_allows_the_exit(self, monkeypatch, v2):
        def _boom():
            raise RuntimeError("settings unreachable")

        monkeypatch.setattr(ep, "min_hold_minutes", _boom)
        allowed, _, _ = ep.exit_allowed("TAKE_PROFIT", T0, EARLY)
        assert allowed, "a fault in the gate must never hold a position hostage"

    def test_a_broken_settings_object_falls_back_to_control(self, monkeypatch):
        class _Boom:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        monkeypatch.setattr("utils.config.settings", _Boom())
        assert ep.strategy_mode() == ep.MODE_CONTROL
        assert ep.exit_allowed("TAKE_PROFIT", T0, EARLY)[0]

    def test_tz_aware_timestamps_do_not_raise(self, v2):
        aware = T0.replace(tzinfo=dt.timezone.utc)
        allowed, _, _ = ep.exit_allowed("TAKE_PROFIT", aware, EARLY)
        assert not allowed, "normalisation must produce a usable comparison"


# ── D. The taxonomy itself ──────────────────────────────────────────────────

class TestTaxonomy:
    def test_covers_every_reason_the_codebase_emits(self):
        """Grepped from the live close call sites, not invented.

        A reason that stops being mapped becomes CONTROL_EXISTING and silently
        leaves the experiment, so this list is the contract.
        """
        emitted = {
            "STOP_LOSS", "TAKE_PROFIT", "TRAIL_STOP", "EXHAUSTION", "STALE_EXIT",
            "SECTOR_REVERSAL", "CONFIRMATION_LOST", "T1_REVERSAL_EXIT",
            "POST_EVENT_REVERSAL", "POST_EVENT_TIME_EXIT", "MIS_SQUAREOFF",
            "LLM_DYNAMIC_EXIT", "MARKET_SHOCK_FLATTEN", "REALLOCATED",
            "KILL_SWITCH", "MANUAL", "T1_HIT", "T2_HIT",
        }
        missing = emitted - set(ep._FAMILY_BY_REASON)
        assert not missing, f"unmapped exit reasons: {missing}"

    def test_every_reason_maps_to_a_declared_family(self):
        families = {v for k, v in vars(ep.ExitFamily).items() if not k.startswith("_")}
        assert set(ep._FAMILY_BY_REASON.values()) <= families

    def test_classification_is_case_and_whitespace_insensitive(self):
        assert ep.classify(" stop_loss ") == ep.ExitFamily.HARD_STOP

    def test_only_profit_management_is_ever_gated(self, v2):
        """The blast radius, stated as a test rather than as a comment."""
        for reason, family in ep._FAMILY_BY_REASON.items():
            allowed, _, _ = ep.exit_allowed(reason, T0, EARLY)
            assert allowed == (family != ep.ExitFamily.PROFIT_MANAGEMENT), reason


# ── E. Wiring: the call sites actually consult the policy ───────────────────

class TestCallSitesAreWired:
    def _src(self, rel):
        import pathlib

        return (pathlib.Path(__file__).resolve().parents[1] / rel).read_text()

    def test_the_five_second_loop_gates_profit_management_only(self):
        src = self._src("tasks/india_tasks.py")
        i = src.index("_pm_ok = ")
        assert "profit_management_allowed" in src[i - 400:i + 60]
        # EXHAUSTION, the T2 partial and the TAKE_PROFIT/T1 branch each carry it.
        assert src.count("_pm_ok") >= 5

    def test_the_five_second_loop_never_gates_the_fixed_stop(self):
        """sl_hit must be computed from the stop alone."""
        src = self._src("tasks/india_tasks.py")
        i = src.index("sl_hit = (is_buy and price <= pos.stop_loss)")
        assert "_pm_ok" not in src[i:i + 200]

    def test_the_sixty_second_loop_routes_sl_tp_through_the_policy(self):
        src = self._src("paper_trading/trade_simulator.py")
        i = src.index('"TRAIL_STOP" if hit_sl and is_trailing')
        seg = src[i:i + 900]
        assert "_exit_allowed(reason" in seg

    def test_a_deferred_exit_does_not_skip_the_pnl_update(self):
        """It must fall through, not `continue` — a held position still marks."""
        src = self._src("paper_trading/trade_simulator.py")
        i = src.index("if not _sl_allowed:")
        seg = "\n".join(
            ln for ln in src[i:i + 400].splitlines()
            if not ln.strip().startswith("#")      # the comment says "continue"
        )
        assert "hit_sl = hit_tp = False" in seg
        assert "continue" not in seg
