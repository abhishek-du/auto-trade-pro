# Celery tasks: Indian market data crawl and signal generation.
#
# Schedule (beat):
#   india-price-crawl      — every 5 minutes  (during NSE hours)
#   india-fii-dii-crawl    — every 15 minutes (once-daily data; idempotent upsert)
#   india-options-crawl    — every 10 minutes (NIFTY + BANKNIFTY)
#   india-signal-scan      — every 5 minutes  (requires fresh candles)

import asyncio

from tasks.celery_app import celery_app
from utils.logger import logger


def _run_async(coro):
    return asyncio.run(coro)


# ── 1. India price crawl ─────────────────────────────────────────────────────

async def _crawl_india_prices():
    from crawler.india_price_feed import is_nse_market_open, run_india_price_crawl
    from tasks._db import celery_session

    if not is_nse_market_open():
        logger.info("[india_price_crawl] NSE closed — skipping")
        return

    async with celery_session() as session:
        result = await run_india_price_crawl(session)
        await session.commit()

    logger.info(
        f"[india_price_crawl] symbols={result.get('total_symbols', '?')}  "
        f"fetched={result.get('total_candles_fetched', '?')}  "
        f"saved={result.get('total_candles_saved', '?')}  "
        f"errors={len(result.get('errors', []))}"
    )


@celery_app.task(name="tasks.india_tasks.crawl_india_prices")
def crawl_india_prices():
    """Fetch OHLCV candles for all Indian watchlist symbols via yfinance."""
    logger.info("[india_price_crawl] Starting")
    _run_async(_crawl_india_prices())


# ── 2. FII / DII crawl ───────────────────────────────────────────────────────

async def _crawl_fii_dii():
    from crawler.fii_dii_crawler import fetch_fii_dii_data, save_fii_dii_to_db
    from tasks._db import celery_session

    async with celery_session() as session:
        data = await fetch_fii_dii_data(session)
        await save_fii_dii_to_db(data, session)
        await session.commit()

    logger.info(
        f"[india_fii_dii_crawl] fii_net={data.get('fii_net_buy', 0):+,.0f} Cr  "
        f"dii_net={data.get('dii_net_buy', 0):+,.0f} Cr  "
        f"direction={data.get('market_direction', '?')}"
    )


@celery_app.task(name="tasks.india_tasks.crawl_fii_dii")
def crawl_fii_dii():
    """Fetch and persist daily FII/DII flow data from NSE."""
    logger.info("[india_fii_dii_crawl] Starting")
    _run_async(_crawl_fii_dii())


# ── 3. Options chain crawl ────────────────────────────────────────────────────

async def _crawl_options_chain():
    from crawler.india_price_feed import is_nse_market_open
    from crawler.options_chain import run_options_analysis
    from tasks._db import celery_session

    if not is_nse_market_open():
        logger.info("[india_options_crawl] NSE closed — skipping")
        return

    async with celery_session() as session:
        results = await run_options_analysis(session)
        await session.commit()

    for sym, res in results.items():
        if "error" in res:
            logger.warning(f"[india_options_crawl] {sym}: {res['error']}")
        else:
            logger.info(
                f"[india_options_crawl] {sym}  "
                f"pcr={res.get('pcr', '?')}  max_pain={res.get('max_pain', '?')}  "
                f"score={res.get('options_score', '?')}"
            )


@celery_app.task(name="tasks.india_tasks.crawl_options_chain")
def crawl_options_chain():
    """Fetch NIFTY + BANKNIFTY options chain snapshots and persist to DB."""
    logger.info("[india_options_crawl] Starting")
    _run_async(_crawl_options_chain())


# ── 4. India signal scan ──────────────────────────────────────────────────────

async def _run_india_signal_scan():
    from crawler.india_price_feed import is_nse_market_open
    from engine.india_signal_generator import analyze_all_india_symbols
    from engine.signal_generator import save_signal
    from tasks._db import celery_session

    if not is_nse_market_open():
        logger.info("[india_signal_scan] NSE closed — skipping")
        return

    async with celery_session() as session:
        signals = await analyze_all_india_symbols(session)
        for sig in signals:
            await save_signal(sig, session)
        await session.commit()

    actionable = [s for s in signals if s.action in ("BUY", "SELL")]
    logger.info(
        f"[india_signal_scan] generated={len(signals)}  "
        f"actionable={len(actionable)}  "
        f"symbols={[s.symbol for s in actionable]}"
    )


@celery_app.task(name="tasks.india_tasks.run_india_signal_scan")
def run_india_signal_scan():
    """Generate confluence signals for all Indian watchlist symbols."""
    logger.info("[india_signal_scan] Starting")
    _run_async(_run_india_signal_scan())


# ── 5. Fundamental data weekly update ────────────────────────────────────────

async def _run_fundamental_update():
    from engine.fundamental_analyzer import run_fundamental_update
    from tasks._db import celery_session

    async with celery_session() as session:
        await run_fundamental_update(session)
        await session.commit()


@celery_app.task(name="tasks.india_tasks.run_fundamental_update_task")
def run_fundamental_update_task():
    """Weekly refresh of fundamental data for all NSE large + mid cap symbols."""
    logger.info("[fundamental_update] Starting weekly task")
    _run_async(_run_fundamental_update())


# ── 6. ML model training (weekly) ────────────────────────────────────────────

async def _train_ml_models():
    from engine.ml_predictor import train_all_models
    from tasks._db import celery_session

    async with celery_session() as session:
        await train_all_models(session)


@celery_app.task(name="tasks.india_tasks.train_ml_models_task")
def train_ml_models_task():
    """Weekly LSTM training for all NSE large + mid cap symbols."""
    logger.info("[ml_training] Starting weekly model training")
    _run_async(_train_ml_models())
