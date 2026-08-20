"""Path F — risk bucket and cooldown, now Redis-backed.

THE BUG THIS FIXES
------------------
`TacticalExecutor` is constructed fresh by every Celery run, so
`TacticalRiskManager.open_risk` — an instance attribute — reset to 0.0 every
minute. The 2% daily cap was only ever enforced *within* a single scan.

The 2026-08-20 audit measured it live: 322 would-trade signals totalling
Rs 793,907 of risk against a Rs 10,000 bucket — 79x over.

The cross-cycle tests below are the point of this file: they construct a NEW
manager per simulated Celery run, exactly as production does.
"""
from __future__ import annotations

import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.tactical_risk import (
    TacticalRiskManager,
    cooldown_key,
    risk_key,
)
from engine.tactical_rules import Signal


class FakeRedis:
    """In-memory stand-in with the handful of ops the manager uses."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v):
        self.store[k] = str(v)

    async def setex(self, k, ttl, v):
        self.store[k] = str(v)
        self.expiries[k] = ttl

    async def incrbyfloat(self, k, amt):
        cur = float(self.store.get(k, 0.0))
        self.store[k] = str(cur + float(amt))
        return float(self.store[k])

    async def incrby(self, k, amt):
        cur = int(float(self.store.get(k, 0)))
        self.store[k] = str(cur + amt)
        return cur + amt

    async def expire(self, k, ttl):
        self.expiries[k] = ttl

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)


def _sig(entry=100.0, stop=98.0, target=106.0, side="BUY"):
    from datetime import datetime
    return Signal("X.NS", side, entry, stop, target, 70.0, "ORB", datetime.now())


@pytest.fixture
def fake_redis():
    r = FakeRedis()
    with patch("utils.cache.get_redis", return_value=r):
        yield r


class TestSizing:
    @pytest.mark.asyncio
    async def test_quantity_from_risk_budget(self, fake_redis):
        d = await TacticalRiskManager(capital=500_000.0).size(_sig())
        # Risk budget alone would buy 1250 shares (2500 / 2.0), but 1250 x 100
        # = Rs 125,000 breaches the TACTICAL 10% notional cap (Rs 50,000), so it
        # trims to 500. CONSEQUENCE: realised risk is then Rs 1,000, not 2,500 --
        # the notional bound, not the risk bound, decides this size.
        assert d.approved and d.quantity == 500
        assert d.risk_amount == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_zero_stop_distance_rejected(self, fake_redis):
        assert not (await TacticalRiskManager().size(_sig(stop=100.0))).approved

    @pytest.mark.asyncio
    async def test_high_ml_prob_scales_up(self, fake_redis):
        # Use a wide stop so sizing stays risk-bound, not notional-bound.
        wide = _sig(entry=100.0, stop=60.0, target=220.0)
        base = (await TacticalRiskManager(capital=500_000.0).size(wide, ml_prob=0.5)).quantity
        hot = (await TacticalRiskManager(capital=500_000.0).size(wide, ml_prob=0.8)).quantity
        assert hot == int(base * 1.2)

    @pytest.mark.asyncio
    async def test_neutral_stub_probability_does_not_resize(self, fake_redis):
        """0.5 means 'Layer 2 has no opinion', not 'weak signal'."""
        d = await TacticalRiskManager(capital=500_000.0).size(_sig(), ml_prob=0.5)
        assert d.quantity == 500

    @pytest.mark.asyncio
    async def test_high_vix_halves_size(self, fake_redis):
        wide = _sig(entry=100.0, stop=60.0, target=220.0)
        calm = (await TacticalRiskManager(capital=500_000.0).size(wide, vix=14.0)).quantity
        wild = (await TacticalRiskManager(capital=500_000.0).size(wide, vix=30.0)).quantity
        assert wild == int(calm * 0.5)


@patch("utils.config.settings.TACTICAL_RISK_BUCKET_ENABLED", True)
class TestBucketPersistsAcrossCycles:
    """These prove the daily cap genuinely caps WHEN ENABLED.

    The cap is off in .env for the current run (contract SS10c), so each test
    pins the flag True rather than inheriting the deployment's value -- exactly
    the behaviour a later re-enable depends on. The OFF direction is covered by
    TestDailyBucketCanBeDisabled below.
    """
    """The regression the audit demanded. Each block = a separate Celery run."""

    @pytest.mark.asyncio
    async def test_risk_accumulates_across_fresh_managers(self, fake_redis):
        for _ in range(3):
            rm = TacticalRiskManager(capital=500_000.0)     # NEW instance, as in prod
            d = await rm.size(_sig())
            assert d.approved
            await rm.commit(d)
        assert float(fake_redis.store[risk_key()]) == pytest.approx(3000.0)  # 3 x 1000

    @pytest.mark.asyncio
    async def test_bucket_blocks_the_fifth_trade_across_cycles(self, fake_redis):
        approved = 0
        for _ in range(12):                                  # 12 separate cycles
            rm = TacticalRiskManager(capital=500_000.0)
            d = await rm.size(_sig())
            if d.approved:
                await rm.commit(d)
                approved += 1
        # Each trade is notional-capped to Rs 1,000 of risk, so exactly 10 fit
        # inside the Rs 10,000 bucket and cycles 11-12 are refused. The refusal
        # happening across SEPARATE manager instances is the point of this file.
        assert approved == 10
        assert float(fake_redis.store[risk_key()]) == pytest.approx(10_000.0)

    @pytest.mark.asyncio
    async def test_the_79x_overrun_can_no_longer_happen(self, fake_redis):
        """322 attempts (the audit's live count) must not exceed the bucket."""
        committed = 0.0
        for _ in range(322):
            rm = TacticalRiskManager(capital=500_000.0)
            d = await rm.size(_sig())
            if d.approved and await rm.commit(d):
                committed += d.risk_amount
        assert committed <= 10_000.0, f"committed {committed} > Rs 10,000 cap"

    @pytest.mark.asyncio
    async def test_rejection_reason_names_the_committed_total(self, fake_redis):
        rm = TacticalRiskManager(capital=500_000.0)
        await fake_redis.incrbyfloat(risk_key(), 9_800.0)
        d = await rm.size(_sig())
        assert not d.approved
        assert "exceed tactical bucket" in d.reason and "committed today" in d.reason

    @pytest.mark.asyncio
    async def test_bucket_key_is_per_day(self, fake_redis):
        assert risk_key() == f"tactical:risk:{date.today().isoformat()}"

    @pytest.mark.asyncio
    async def test_commit_sets_an_expiry(self, fake_redis):
        rm = TacticalRiskManager(capital=500_000.0)
        await rm.commit(await rm.size(_sig()))
        assert fake_redis.expiries.get(risk_key()) == 86_400 * 2


class TestFailsClosed:
    @pytest.mark.asyncio
    async def test_redis_outage_refuses_to_size(self):
        """A cap that stops applying during a blip is not a cap."""
        with patch("utils.cache.get_redis", side_effect=ConnectionError("redis down")):
            d = await TacticalRiskManager(capital=500_000.0).size(_sig())
        assert not d.approved
        assert "risk bucket unavailable" in d.reason

    @pytest.mark.asyncio
    async def test_failed_commit_reports_false(self):
        rm = TacticalRiskManager(capital=500_000.0)
        with patch("utils.cache.get_redis", return_value=FakeRedis()):
            d = await rm.size(_sig())
        with patch("utils.cache.get_redis", side_effect=ConnectionError("down")):
            assert await rm.commit(d) is False


class TestCooldownPersists:
    @pytest.mark.asyncio
    async def test_three_stops_across_cycles_trigger_cooldown(self, fake_redis):
        for _ in range(3):
            await TacticalRiskManager().record_stop_loss()   # separate instances
        assert await TacticalRiskManager().in_cooldown()
        assert not (await TacticalRiskManager().size(_sig())).approved

    @pytest.mark.asyncio
    async def test_two_stops_do_not(self, fake_redis):
        for _ in range(2):
            await TacticalRiskManager().record_stop_loss()
        assert not await TacticalRiskManager().in_cooldown()

    @pytest.mark.asyncio
    async def test_a_win_breaks_the_streak(self, fake_redis):
        for _ in range(2):
            await TacticalRiskManager().record_stop_loss()
        await TacticalRiskManager().record_win()
        await TacticalRiskManager().record_stop_loss()
        assert not await TacticalRiskManager().in_cooldown()

    @pytest.mark.asyncio
    async def test_expired_timestamp_is_not_cooldown(self, fake_redis):
        fake_redis.store[cooldown_key()] = str(int(time.time()) - 10)
        assert not await TacticalRiskManager().in_cooldown()

    @pytest.mark.asyncio
    async def test_cooldown_key_carries_its_own_ttl(self, fake_redis):
        for _ in range(3):
            await TacticalRiskManager().record_stop_loss()
        assert fake_redis.expiries.get(cooldown_key()) == 3600


class TestReset:
    @pytest.mark.asyncio
    async def test_reset_clears_bucket_and_cooldown(self, fake_redis):
        rm = TacticalRiskManager(capital=500_000.0)
        await rm.commit(await rm.size(_sig()))
        for _ in range(3):
            await rm.record_stop_loss()
        await TacticalRiskManager.reset_daily_risk()
        assert risk_key() not in fake_redis.store
        assert not await TacticalRiskManager().in_cooldown()


class TestNotionalCap:
    """Risk-based sizing alone is not enough: a tight stop buys an enormous
    position for the same rupee risk. GROWW.NS on 2026-08-20 had a Rs 5.50 stop,
    so the 0.5% budget bought 1,644 shares = Rs 328,422, caught only by the
    trade_simulator hard guard — which is meant to be the last line of defence."""

    @pytest.mark.asyncio
    async def test_tight_stop_no_longer_produces_a_huge_notional(self, fake_redis):
        # Rs 5.50 stop on a Rs 725 share — the GROWW shape.
        sig = _sig(entry=725.0, stop=719.5, target=740.0)
        d = await TacticalRiskManager(capital=500_000.0).size(sig)
        assert d.approved
        notional = d.quantity * sig.entry_price
        assert notional <= 50_000 * 1.001, f"notional {notional:,.0f} exceeds the 10% TACTICAL cap"

    @pytest.mark.asyncio
    async def test_tactical_cap_overrides_the_global_5pct(self, fake_redis):
        """TACTICAL uses its own 10%, NOT min(10%, global 5%).

        This reverses the earlier behaviour deliberately (owner decision,
        2026-08-20). Under the old clamp a tight-stop name sized to 5% and the
        notional bound dominated every tactical trade.
        """
        sig = _sig(entry=100.0, stop=99.9, target=100.5)   # 0.1 stop -> huge qty
        with patch("utils.config.settings.TACTICAL_MAX_POSITION_NOTIONAL_PCT", 0.10), \
             patch("utils.config.settings.AGENT_MAX_POSITION_WEIGHT", 0.05):
            d = await TacticalRiskManager(capital=500_000.0).size(sig)
        assert d.quantity * 100.0 == pytest.approx(50_000.0), "must reach 10%, not stop at 5%"

    def test_all_three_cap_sites_agree_on_the_family(self):
        """The override is worthless unless every downstream gate honours it.

        `tactical_risk` emitting a 10% size while `validate_signal` check 5 or
        the `trade_simulator` hard guard still bound at 5% would turn a clean
        size into a rejection or a raised ValueError at execution time. All
        three read `max_position_weight_for`.
        """
        from types import SimpleNamespace

        from engine.risk_manager import max_position_weight_for

        tac = SimpleNamespace(strategy_family="TACTICAL")
        news = SimpleNamespace(strategy_family="EVENT_DRIVEN")
        unrouted = SimpleNamespace()          # never passed through the router

        assert max_position_weight_for(tac) == pytest.approx(0.10)
        assert max_position_weight_for(news) == pytest.approx(0.05)
        # Fail-safe: no attribute must get the STRICTER global cap.
        assert max_position_weight_for(unrouted) == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_unaffordable_single_share_is_rejected(self, fake_redis):
        sig = _sig(entry=60_000.0, stop=59_900.0, target=60_400.0)
        d = await TacticalRiskManager(capital=100_000.0).size(sig)
        assert not d.approved and "notional cap" in d.reason

    @pytest.mark.asyncio
    async def test_normal_sizing_is_untouched_by_the_cap(self, fake_redis):
        """A wide stop is risk-bound, not notional-bound — must not be trimmed."""
        # Rs 40 stop: 2500 / 40 = 62 shares = Rs 6,200, far under the Rs 50,000 cap.
        d = await TacticalRiskManager(capital=500_000.0).size(_sig(entry=100.0, stop=60.0, target=220.0))
        assert d.approved and d.quantity == 62
        assert d.risk_amount == pytest.approx(2480.0)   # full risk budget, untrimmed


class TestRiskRewardFloor:
    """The TACTICAL R:R floor is a calibrated number, not an incidental default.

    It was moved 2.0 -> 1.5 -> 1.2 on 2026-08-20 against measured throughput.
    If someone changes it again, these tests should make them justify it and
    update §10a of the contract in the same commit.
    """

    def test_tactical_floor_is_1_2_and_news_floor_is_untouched(self):
        from utils.config import settings

        assert settings.TACTICAL_MIN_RISK_REWARD == pytest.approx(1.2)
        # The whole point of a family-specific floor is that news paths do NOT
        # move. A change here is a contract violation, not a tuning decision.
        assert settings.MIN_RISK_REWARD == pytest.approx(2.0)

    def test_contract_records_the_same_floor(self):
        """Code and §10a must not drift apart on this number."""
        from pathlib import Path

        contract = (Path(__file__).resolve().parents[2] / "docs"
                    / "NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md")
        assert contract.exists(), f"contract not found at {contract}"
        text = contract.read_text(encoding="utf-8")
        assert "| Minimum reward:risk | **2.0** | **1.2** |" in text, (
            "§10a condition 4 does not record the 1.2 TACTICAL floor — update "
            "the contract in the same commit that changed the setting"
        )

    @pytest.mark.parametrize("rr,expected", [(1.1, False), (1.2, True), (2.5, True)])
    def test_floor_admits_and_rejects_around_1_2(self, rr, expected):
        """A signal is judged on reward/risk, so build one with an exact ratio."""
        from types import SimpleNamespace

        from utils.config import settings

        entry, stop = 100.0, 98.0            # risk = 2.0 per share
        target = entry + 2.0 * rr
        sig = SimpleNamespace(strategy_family="TACTICAL", entry_price=entry,
                              stop_loss=stop, take_profit=target)
        risk = abs(sig.entry_price - sig.stop_loss)
        reward = abs(sig.take_profit - sig.entry_price)
        floor = settings.TACTICAL_MIN_RISK_REWARD
        assert ((reward / risk) >= floor - 1e-9) is expected


class TestDailyBucketCanBeDisabled:
    """Contract §10c — the daily cap is off for the current run.

    These pin BOTH directions: that disabling really removes the cap, and that
    the code default still has it, so a fresh checkout keeps the brake.
    """

    def test_code_default_keeps_the_bucket(self):
        from utils.config import Settings

        assert Settings.model_fields["TACTICAL_RISK_BUCKET_ENABLED"].default is True

    @pytest.mark.asyncio
    async def test_bucket_blocks_when_enabled(self, fake_redis):
        await fake_redis.incrbyfloat(risk_key(), 9_800.0)   # nearly spent
        with patch("utils.config.settings.TACTICAL_RISK_BUCKET_ENABLED", True):
            d = await TacticalRiskManager(capital=500_000.0).size(_sig())
        assert not d.approved and "exceed tactical bucket" in d.reason

    @pytest.mark.asyncio
    async def test_no_cap_when_disabled(self, fake_redis):
        """The same over-budget state must now size normally."""
        await fake_redis.incrbyfloat(risk_key(), 9_800.0)
        with patch("utils.config.settings.TACTICAL_RISK_BUCKET_ENABLED", False):
            d = await TacticalRiskManager(capital=500_000.0).size(_sig())
        assert d.approved and d.quantity > 0

    @pytest.mark.asyncio
    async def test_risk_is_still_recorded_when_disabled(self, fake_redis):
        """Observability must survive: the summary and any later re-enable both
        depend on the Redis total continuing to accumulate."""
        with patch("utils.config.settings.TACTICAL_RISK_BUCKET_ENABLED", False):
            rm = TacticalRiskManager(capital=500_000.0)
            d = await rm.size(_sig())
            await rm.commit(d)
        assert float(fake_redis.store[risk_key()]) > 0

    @pytest.mark.asyncio
    async def test_cooldown_still_applies_when_bucket_is_off(self, fake_redis):
        """The 3-stop cooldown is the ONLY loss-reactive control left, so it
        must not have been disabled along with the cap."""
        # The key stores the unix time the pause ENDS, not a count of stops.
        import time as _t
        await fake_redis.set(cooldown_key(), str(int(_t.time()) + 3600))
        with patch("utils.config.settings.TACTICAL_RISK_BUCKET_ENABLED", False):
            d = await TacticalRiskManager(capital=500_000.0).size(_sig())
        assert not d.approved and "cooldown" in d.reason.lower()
