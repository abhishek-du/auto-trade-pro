"""Strategy execution toggles — admin UI switches (2026-08-20).

Six RuntimeConfig flags, one per origination path, readable by every process so
a toggle takes effect without a restart.

The load-bearing property here is the FAIL-OPEN posture: these are *enable*
switches, so a database blip must not silently halt a trading system the
operator believes is running. That is the opposite of `tactical_risk`, which
fails closed because its flag caps risk. Both directions are pinned below.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from utils.runtime_config import STRATEGY_FLAGS, _KNOWN_KEYS, strategy_enabled


class TestFlagRegistry:

    def test_all_six_paths_are_registered(self):
        assert set(STRATEGY_FLAGS) == {
            "india_trade_loop", "news_engine", "pre_event_gap",
            "direct_news", "tactical", "master_intelligence",
        }

    def test_every_flag_is_whitelisted_as_bool(self):
        """RuntimeConfig.set rejects keys outside _KNOWN_KEYS, so a flag missing
        here would make the API silently unable to persist that toggle."""
        for key in STRATEGY_FLAGS.values():
            assert key in _KNOWN_KEYS, f"{key} not whitelisted — set() would reject it"
            assert _KNOWN_KEYS[key] is bool


class TestFailOpen:
    """A missing row, an unknown name, or an unreachable DB must read ENABLED."""

    @pytest.mark.asyncio
    async def test_unknown_name_is_enabled(self):
        assert await strategy_enabled("no_such_strategy") is True

    @pytest.mark.asyncio
    async def test_absent_row_is_enabled(self):
        """A fresh DB has no rows at all — trading must not be off by default."""
        cfg = type("C", (), {"_get": lambda self, k, d: d})()
        with patch("utils.runtime_config.RuntimeConfig.load", AsyncMock(return_value=cfg)):
            assert await strategy_enabled("tactical", session=object()) is True

    @pytest.mark.asyncio
    async def test_db_failure_is_enabled_not_disabled(self):
        with patch("utils.runtime_config.RuntimeConfig.load",
                   AsyncMock(side_effect=RuntimeError("db down"))):
            assert await strategy_enabled("news_engine", session=object()) is True


class TestFlagIsHonoured:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", sorted(STRATEGY_FLAGS))
    async def test_false_disables_each_strategy(self, name):
        """A stored False must actually read as disabled, for every path."""
        key = STRATEGY_FLAGS[name]
        cfg = type("C", (), {"_get": lambda self, k, d, _k=key: (False if k == _k else d)})()
        with patch("utils.runtime_config.RuntimeConfig.load", AsyncMock(return_value=cfg)):
            assert await strategy_enabled(name, session=object()) is False

    @pytest.mark.asyncio
    async def test_one_flag_does_not_affect_another(self):
        cfg = type("C", (), {
            "_get": lambda self, k, d: (False if k == "strategy_tactical_enabled" else d)
        })()
        with patch("utils.runtime_config.RuntimeConfig.load", AsyncMock(return_value=cfg)):
            assert await strategy_enabled("tactical", session=object()) is False
            assert await strategy_enabled("news_engine", session=object()) is True


class TestCheckSitesExist:
    """Each path must actually consult its flag. A registered flag that no code
    reads is worse than no flag: the UI would report control it does not have."""

    @pytest.mark.parametrize("path,needle", [
        ("engine/tactical_executor.py",      'strategy_enabled("tactical"'),
        ("engine/direct_news_strategy.py",   'strategy_enabled("direct_news"'),
        ("news_discovery_engine.py",         'strategy_enabled("news_engine"'),
        ("tasks/india_tasks.py",             'strategy_enabled("india_trade_loop"'),
        ("tasks/india_tasks.py",             'strategy_enabled("pre_event_gap"'),
        ("tasks/india_tasks.py",             'strategy_enabled("master_intelligence"'),
    ])
    def test_path_checks_its_flag(self, path, needle):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
        assert needle in src, f"{path} never checks {needle}"


class TestExitsAreNeverGated:
    """Disabling a strategy must not strand an open position.

    fast_sl_check is the 5s stop-loss/take-profit loop. It has no strategy flag
    on purpose: with every strategy off, open positions must still exit.
    """

    def test_fast_sl_check_has_no_strategy_toggle(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "tasks" / "india_tasks.py").read_text(
            encoding="utf-8")
        start = src.index("def fast_sl_check")
        body = src[start:start + 4000]
        assert "strategy_enabled(" not in body, (
            "fast_sl_check must never be gated by a strategy toggle — "
            "open positions would stop being stop-lossed"
        )

    def test_path_b_gates_entries_not_exits(self):
        """The india_trade_loop check sits at the ENTRY branch, after the
        exit/risk-management work has already run."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "tasks" / "india_tasks.py").read_text(
            encoding="utf-8")
        idx = src.index('strategy_enabled("india_trade_loop"')
        window = src[idx - 700:idx + 400]
        assert "_NEWS_ONLY_BLOCKS_HUB_ENTRIES" in window, (
            "flag is not at the entry branch — it may be gating exits too"
        )


class TestTradeAttribution:

    def test_paper_trade_model_has_strategy_family(self):
        from db.models import PaperTrade

        assert hasattr(PaperTrade, "strategy_family")

    def test_simulator_populates_it(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "paper_trading"
               / "trade_simulator.py").read_text(encoding="utf-8")
        assert "strategy_family=" in src

    def test_live_table_has_the_column(self):
        """models.py alone is not enough: create_all never ALTERs an existing
        table, so the column must also be in init_db's DDL batch."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "db" / "database.py").read_text(
            encoding="utf-8")
        assert "paper_trades ADD COLUMN IF NOT EXISTS strategy_family" in src
