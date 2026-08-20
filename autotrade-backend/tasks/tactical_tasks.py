"""Path F — Celery entry points (shadow mode).

These tasks run the Tactical pipeline on a schedule. They open no positions;
see engine/tactical_executor.py for why that is structural rather than
configurable.

Two operational constraints shaped this file:

1. **Overlap guards.** Redis `SET NX EX`, released in `finally`, with a TTL
   above the task's hard limit — the same idiom as `price_cache`,
   `india_price_scan`, `kite_live_candles` and `news_scan`. F1 fires every
   minute, so a run that outlives its tick absolutely will stack without this.

2. **The worker is `--concurrency=2` on a single queue**, shared with the 5s
   `fast_sl_check` exit loop. Tactical scans must therefore be time-boxed and
   give the slot back promptly. Soft limit 50s / hard 60s, and the executor
   catches `SoftTimeLimitExceeded` and abandons the cycle cleanly rather than
   being killed mid-transaction.

The brief's sample code imported `utils.redis_client.redis_client`, which does
not exist in this repo. `utils.cache.get_redis()` is the real helper, but it
returns an **async** client keyed on the running event loop; Celery prefork runs
each task in its own `asyncio.run()`, so we build a short-lived client inside
the coroutine exactly as `price_cache` does.
"""
from __future__ import annotations

import asyncio

from tasks.celery_app import celery_app
from utils.config import settings
from utils.logger import logger

_LOCK_TTL = 65  # > time_limit (60s) so a killed task's lock still expires


def _run_guarded(lock_key: str, coro_factory) -> dict:
    """Run an async scan under a Redis overlap guard. Never raises."""
    from utils.config import settings
    import redis.asyncio as _aioredis

    async def _main() -> dict:
        r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            acquired = bool(await r.set(lock_key, "1", nx=True, ex=_LOCK_TTL))
            if not acquired:
                logger.debug(f"[tactical] {lock_key} held — previous cycle still running")
                return {"skipped": "overlap guard"}
            try:
                return await coro_factory()
            finally:
                await r.delete(lock_key)
        finally:
            await r.aclose()

    try:
        return asyncio.run(_main())
    except Exception as exc:
        # A scan failure must never poison the worker or retry-storm a 1-minute
        # beat; log and return.
        logger.error(f"[tactical] {lock_key} failed: {type(exc).__name__}: {exc}")
        return {"error": str(exc)}


@celery_app.task(
    name="tasks.tactical_tasks.run_tactical_intraday",
    soft_time_limit=50,
    time_limit=60,
)
def run_tactical_intraday() -> dict:
    """F1 — intraday momentum scan on 1-minute candles. Every minute."""

    async def _scan():
        from engine.tactical_executor import TacticalExecutor

        return await TacticalExecutor().run_intraday_scan()

    return _run_guarded("tactical_intraday:running", _scan)


@celery_app.task(
    name="tasks.tactical_tasks.run_tactical_mean_reversion",
    soft_time_limit=50,
    time_limit=60,
)
def run_tactical_mean_reversion() -> dict:
    """F4 — mean-reversion scan on 5-minute candles. Every 5 minutes."""

    async def _scan():
        from engine.tactical_executor import TacticalExecutor

        return await TacticalExecutor().run_mean_reversion_scan()

    return _run_guarded("tactical_mean_reversion:running", _scan)


@celery_app.task(
    name="tasks.tactical_tasks.tactical_daily_summary",
    soft_time_limit=110,
    time_limit=120,
)
def tactical_daily_summary() -> dict:
    """One end-of-day line-item summary of the Path F paper run.

    Runs at 15:35 IST (10:05 UTC) — five minutes after the NSE close, so the
    5s `fast_sl_check` loop has settled the day's exits before we count wins.

    Reads the day the *bucket* is keyed on rather than "yesterday/today" logic:
    `tactical_risk.risk_key()` uses `date.today()`, so a summary that computed
    its own date differently would silently report a different day's bucket.
    """

    async def _summary() -> dict:
        from datetime import date

        from sqlalchemy import text

        from db.database import AsyncSessionLocal
        from engine.tactical_risk import cooldown_key, risk_key
        from utils.cache import get_redis

        today = date.today()

        risk_used = 0.0
        cooldowns = None
        try:
            r = get_redis()
            risk_used = float(await r.get(risk_key(today)) or 0.0)
            cooldowns = await r.get(cooldown_key(today))
        except Exception as exc:                     # Redis is not load-bearing here
            logger.warning(f"[tactical.summary] bucket unreadable: {exc}")

        async with AsyncSessionLocal() as ses:
            sig = (await ses.execute(text("""
                SELECT COUNT(*)                                  AS generated,
                       COUNT(*) FILTER (WHERE executed)          AS executed
                FROM tactical_signals
                WHERE created_at::date = :d
            """), {"d": today})).fetchone()

            # route_decision(source=intent.strategy) writes "TACTICAL_<rule>" into
            # paper_trades.source (verified, not assumed). Close column is closed_at.
            pnl = (await ses.execute(text("""
                SELECT COUNT(*)                                          AS closed,
                       COUNT(*) FILTER (WHERE pnl > 0)                   AS wins,
                       COUNT(*) FILTER (WHERE pnl <= 0)                  AS losses,
                       COALESCE(SUM(pnl), 0)                             AS realised
                FROM paper_trades
                WHERE (source LIKE 'TACTICAL%' OR strategy_name LIKE 'TACTICAL%')
                  AND status = 'CLOSED' AND closed_at::date = :d
            """), {"d": today})).fetchone()

            open_n = (await ses.execute(text("""
                SELECT COUNT(*) FROM paper_trades
                WHERE (source LIKE 'TACTICAL%' OR strategy_name LIKE 'TACTICAL%') AND status = 'OPEN'
            """))).scalar()

        budget = float(getattr(settings, "TACTICAL_CAPITAL", 500_000.0)) * \
            float(getattr(settings, "TACTICAL_MAX_TOTAL_RISK", 0.02))

        out = {
            "date": today.isoformat(),
            "signals_generated": sig.generated, "signals_executed": sig.executed,
            "trades_closed": pnl.closed, "wins": pnl.wins, "losses": pnl.losses,
            "realised_pnl": round(float(pnl.realised), 2),
            "open_positions": open_n,
            "risk_used": round(risk_used, 2), "risk_budget": round(budget, 2),
            "cooldown_symbols": cooldowns,
        }

        win_rate = (pnl.wins / pnl.closed * 100.0) if pnl.closed else 0.0
        logger.info(
            f"[TACTICAL] DAILY SUMMARY {out['date']} | "
            f"signals {out['signals_generated']} generated, {out['signals_executed']} executed | "
            f"closed {out['trades_closed']} (W{out['wins']}/L{out['losses']}, "
            f"{win_rate:.0f}% win) | realised Rs {out['realised_pnl']:,.2f} | "
            f"open {out['open_positions']} | "
            f"risk Rs {out['risk_used']:,.0f}/{out['risk_budget']:,.0f} "
            f"({(risk_used / budget * 100.0) if budget else 0:.0f}% of bucket)"
        )
        return out

    return _run_guarded("tactical_daily_summary:running", _summary)
