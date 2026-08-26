"""Phase 13 — observability for the trade-loop hang. Logging only.

On 2026-08-26 tasks.india_trade_loop ran to its Celery time limits 16 times
between 10:18 and 12:03 IST: SoftTimeLimitExceeded at 300 s, and five hard kills
at 600 s that SIGKILL the pool worker with no cleanup. Three evidence sources
could not name the blocking call — the soft-limit traceback lands in the event
loop's epoll.poll() rather than the awaiting coroutine, llm_dynamic_sl_tp logged
nothing at all, and py-spy needs ptrace privileges this host does not grant.

Two log boundaries close that gap. These tests pin them, and — just as
importantly — pin that they changed nothing else.

Runtime tests use the allowlisted TEST database (tests/TEST_DATABASE.md).
"""
from __future__ import annotations

import ast
import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

IT = Path("tasks/india_tasks.py")
DM = Path("engine/agent/dynamic_management.py")
CA = Path("tasks/celery_app.py")


def _fn(path: Path, name: str):
    tree = ast.parse(path.read_text())
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


# ── A/B/C/D — runtime: the boundary actually fires and times correctly ────────

def _fake_session(position):
    """A session that answers llm_dynamic_sl_tp's four queries in order.

    An earlier version INSERTed a real row, which fought a moving target:
    open_positions carries 21 NOT NULL columns including trade_id, so the seed
    had to be rewritten every time the schema shifted. The boundary under test is
    logging, not persistence — a stub keeps the test about the thing it names.
    The fail-closed guard still points every session at autotrade_test.
    """
    from unittest.mock import MagicMock, AsyncMock

    def _result(scalars_all=None, one=None):
        r = MagicMock()
        r.scalars.return_value.all.return_value = scalars_all if scalars_all is not None else []
        r.scalar_one_or_none.return_value = one
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _result(scalars_all=[position]),   # positions
        _result(scalars_all=[]),           # recent news headlines
        _result(one=None),                 # hub score
        _result(one=None),                 # fundamentals
    ] + [_result() for _ in range(20)])    # anything further
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _fake_position(symbol: str):
    """direction is an enum on the model — the prompt reads pos.direction.value."""
    from db.models import OpenPosition, TradeDirection
    return OpenPosition(
        symbol=symbol, direction=TradeDirection.BUY, instrument_type="EQUITY",
        entry_price=100.0, current_price=101.0, stop_loss=95.0, take_profit=110.0,
        size_units=10, size_usd=1000.0, unrealised_pnl=0.0, unrealised_pct=0.0,
        product="MIS", trade_style="MIS",
    )


@pytest.mark.asyncio
async def test_bedrock_boundary_logs_start_and_end_with_elapsed():
    """A + B + C, exercised through the real llm_dynamic_sl_tp."""
    from db.database import AsyncSessionLocal
    import engine.agent.dynamic_management as dm

    assert AsyncSessionLocal.kw["bind"].url.database == "autotrade_test"
    sym = "P13ALPHA.NS"
    calls: list[str] = []

    async def _fake_llm(*a, **k):
        return '{"action": "HOLD", "new_stop_loss": 96.0, "new_take_profit": 109.0, "reasoning": "x"}'

    dm._last_manage_ts = 0.0                                  # defeat the throttle
    with patch.object(dm, "call_llm_chat", AsyncMock(side_effect=_fake_llm)), \
         patch.object(dm.logger, "info", MagicMock(side_effect=lambda m, *a, **k: calls.append(str(m)))):
        await dm.llm_dynamic_sl_tp(_fake_session(_fake_position(sym)))

    starts = [c for c in calls if "BEDROCK_CALL_START" in c]
    ends = [c for c in calls if "BEDROCK_CALL_END" in c]
    assert starts, f"no START logged; captured={calls[:4]}"
    assert ends, f"no END logged; captured={calls[:4]}"
    assert sym in starts[0] and sym in ends[0], "the symbol must identify which position"

    ms = int(ends[0].split("elapsed_ms=")[1].split()[0])
    assert ms >= 0, f"elapsed_ms is negative: {ms}"
    assert ms < 60_000, f"implausible elapsed_ms for a mocked call: {ms}"


@pytest.mark.asyncio
async def test_bedrock_boundary_does_not_swallow_an_llm_failure():
    """D + E: the module's existing handler still catches, and START still fires."""
    import engine.agent.dynamic_management as dm

    sym = "P13BETA.NS"
    calls: list[str] = []

    dm._last_manage_ts = 0.0
    with patch.object(dm, "call_llm_chat", AsyncMock(side_effect=RuntimeError("bedrock down"))), \
         patch.object(dm.logger, "info", MagicMock(side_effect=lambda m, *a, **k: calls.append(str(m)))):
        # must NOT propagate — the module's own except already caught it
        await dm.llm_dynamic_sl_tp(_fake_session(_fake_position(sym)))

    assert any("BEDROCK_CALL_START" in c for c in calls), "START must fire before the call"
    assert not any("BEDROCK_CALL_END" in c for c in calls), (
        "END must NOT fire on failure — a START with no END is the signal this "
        "instrumentation exists to produce"
    )


# ── E — exception semantics unchanged, proven structurally ───────────────────

def test_call_site_exception_semantics_are_byte_identical():
    """The original handler must survive unchanged: same catch, same message."""
    src = IT.read_text()
    assert 'logger.error(f"[india_trade_loop] Dynamic management failed: {e}")' in src, (
        "the original error message changed — exception semantics may have shifted"
    )
    fn = _fn(IT, "_india_trade_loop")
    handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers
                if any(isinstance(x, ast.Call) and "Dynamic management failed" in (ast.unparse(x))
                       for x in ast.walk(h))]
    assert len(handlers) == 1, "expected exactly one dynamic-management handler"
    h = handlers[0]
    assert isinstance(h.type, ast.Name) and h.type.id == "Exception", "the catch type changed"
    assert not any(isinstance(n, ast.Raise) for n in ast.walk(h)), (
        "a re-raise was added — the original swallowed, so this alters semantics"
    )


# ── C — monotonic clock, not wall clock ──────────────────────────────────────

@pytest.mark.parametrize("path,name", [(IT, "_india_trade_loop"), (DM, "llm_dynamic_sl_tp")])
def test_timing_uses_a_monotonic_clock(path, name):
    """Scoped to the instrumentation's OWN elapsed computation.

    An earlier version of this test scanned the whole function for any wall-clock
    arithmetic and failed on `datetime.utcnow() - timedelta(minutes=60)` — a
    pre-existing news-lookback window that has nothing to do with timing a call.
    The check was wrong, not the code.
    """
    body = ast.unparse(_fn(path, name))
    fstrings = [ast.unparse(n) for n in ast.walk(_fn(path, name))
                if isinstance(n, ast.JoinedStr) and "elapsed_ms=" in ast.unparse(n)]
    assert fstrings, f"{name} logs no elapsed_ms"
    for f in fstrings:
        assert "monotonic()" in f, f"{name} computes elapsed_ms without a monotonic clock: {f}"
        for banned in ("utcnow", "time.time()", "datetime.now()"):
            assert banned not in f, f"{name} computes elapsed_ms from a wall clock ({banned})"


# ── F — nothing sensitive is logged ──────────────────────────────────────────

def test_no_prompt_response_or_credential_is_logged():
    dm_fn = ast.unparse(_fn(DM, "llm_dynamic_sl_tp"))
    for call in [n for n in ast.walk(_fn(DM, "llm_dynamic_sl_tp"))
                 if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("info", "error", "warning")]:
        arg = ast.unparse(call).lower()
        if "bedrock_call" not in arg:
            continue
        for banned in ("prompt", "resp}", "{resp", "messages", "api_key", "authorization", "token", "secret"):
            assert banned not in arg, f"a Bedrock log line references {banned!r}"
    assert "chars=" in dm_fn, "response length is logged instead of the response — keep it that way"
    assert "{resp}" not in dm_fn, "the raw model response is being logged"


# ── G + static guards — nothing else changed ─────────────────────────────────

def test_celery_time_limits_and_queue_config_untouched():
    src = CA.read_text()
    assert "task_soft_time_limit=300," in src, "the soft time limit changed"
    assert "task_time_limit=600," in src, "the hard time limit changed"
    assert '"expires": 55' in src, "the beat expiry changed"
    assert '"tasks.india_trade_loop": {"queue": "trade_queue"' in src, "the queue routing changed"


def test_bug1_is_still_present():
    """Phase 13 is observability only. BUG-1 stays unfixed and blocking."""
    from tasks.india_tasks import _india_trade_loop
    code = _india_trade_loop.__code__
    inner = [c for c in code.co_consts if hasattr(c, "co_varnames")]
    names = set(code.co_varnames) | {n for c in inner for n in c.co_varnames}
    assert "settings" in names, "BUG-1 may have been fixed — that is not this phase's scope"


def test_strategy_parameters_untouched():
    from utils.config import settings as s
    assert s.PAPER_MODE is True
    assert getattr(s, "PAPER_CONFIDENCE_THRESHOLD") == 50.0
    assert getattr(s, "MAX_NEW_ENTRIES_PER_CYCLE") == 5
    assert getattr(s, "NEWS_ONLY_BLOCKS_HUB_ENTRIES") is False


def test_instrumentation_does_not_branch_on_its_own_timing():
    """Logging must be additive: no control flow may depend on elapsed_ms."""
    for path, name in ((IT, "_india_trade_loop"), (DM, "llm_dynamic_sl_tp")):
        fn = _fn(path, name)
        for node in ast.walk(fn):
            if isinstance(node, (ast.If, ast.While)):
                t = ast.unparse(node.test)
                assert "elapsed" not in t and "_dyn_t0" not in t and "_llm_t0" not in t, (
                    f"{name} branches on instrumentation timing: {t}"
                )
