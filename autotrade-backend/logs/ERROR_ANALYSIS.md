# AutoTrade Pro — Backend Error Analysis
**Generated:** 2026-05-04  
**Log sources analysed:** `startup_capture.log`, `uvicorn.log`, `celery_worker.log`, `celery_beat.log`, `autotrade_2026-05-04.log`

---

## SUMMARY

The app has **4 critical bugs** preventing normal operation and **3 warnings** that degrade features.

---

## CRITICAL ERRORS

---

### [BUG-1] Port 8000 Already In Use — Uvicorn Fails to Start

**Log line:**
```
ERROR:    [Errno 98] Address already in use
```

**Cause:**  
A previous uvicorn process is still running on port 8000 when `./start.sh` is executed again.
`set -e` in `start.sh` causes the script to exit immediately, so Celery workers are orphaned.

**Fix:**  
Kill old processes before starting:
```bash
pkill -f "uvicorn main:app" || true
pkill -f "celery.*autotrade" || true
./start.sh
```
Or add this to the top of `start.sh`:
```bash
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "celery.*autotrade_pro" 2>/dev/null || true
```

---

### [BUG-2] asyncpg 32,767 Argument Limit — Bulk Candle Insert Fails

**Log line:**
```
ERROR | crawler.price_feed:run_price_crawl:416 — 
  ✗  EUR/USD: (sqlalchemy.dialects.postgresql.asyncpg.InterfaceError)
     the number of query arguments cannot exceed 32767
```

**Cause:**  
`crawler/price_feed.py:312` — `save_candles_to_db()` inserts ALL candles in a single
`pg_insert(...).values(rows)` call. For EUR/USD, yfinance fetches 17,228 candles × 8 columns
= **137,824 query parameters**, far exceeding asyncpg's hard limit of 32,767.

**File:** `crawler/price_feed.py`, function `save_candles_to_db()` at line ~289-315

**Fix — chunk the insert:**
```python
CHUNK_SIZE = 3000  # 3000 rows × 8 cols = 24000 params — safely under 32767

async def save_candles_to_db(candles: list[dict], session: AsyncSession) -> int:
    if not candles:
        return 0

    rows = [
        {
            "symbol":    c["symbol"],
            "timeframe": c["timeframe"],
            "open":      c["open"],
            "high":      c["high"],
            "low":       c["low"],
            "close":     c["close"],
            "volume":    c["volume"],
            "timestamp": c["timestamp"],
        }
        for c in candles
    ]

    total_inserted = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        stmt = pg_insert(Candle).values(chunk).on_conflict_do_nothing(constraint="uq_candle_bar")
        result = await session.execute(stmt)
        total_inserted += result.rowcount

    await session.flush()
    return total_inserted
```

**Impact:** Without this fix, NO price candles are saved to the DB, so the signal engine
always sees 0 candles and skips all symbols:
```
WARNING | engine.signal_generator:analyze_all_symbols:400 —
  analyze_all_symbols: skipping EUR/USD — only 0 candles in DB (need ≥ 10)
```
This means **no signals are ever generated and no paper trades open.**

---

### [BUG-3] asyncio Event Loop Conflict in Celery Paper Trade Task

**Log line:**
```
ERROR/ForkPoolWorker-2] Task tasks.paper_trade_loop.run_paper_trade_loop[...] raised unexpected:
RuntimeError("Task <Task pending ...coro=<_loop() running at tasks/paper_trade_loop.py:41>...>
got Future <Future pending cb=[BaseProtocol._on_waiter_completed()]> attached to a different loop")
```

**Cause:**  
`tasks/paper_trade_loop.py:12-17` — `_run_async()` creates a **new** event loop:
```python
def _run_async(coro):
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)
```
But `AsyncSessionLocal` (SQLAlchemy async engine) was initialized in a **different** event loop
(the one belonging to the forked worker process before the new loop was created). asyncpg
connections are bound to the loop they were created in and cannot be used from another loop.

**File:** `tasks/paper_trade_loop.py`, lines 12-17 and 115

**Fix:**
```python
import asyncio

def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
```

Or better, use `asyncio.run()` which properly handles teardown:
```python
def _run_async(coro):
    return asyncio.run(coro)
```

But also ensure the DB engine is created **inside the task** (not at module import time)
so its connection pool is bound to the correct loop. Move `AsyncSessionLocal` creation
to be lazy (created on first use within the same loop).

---

### [BUG-4] PostgreSQL Database Unreachable (DNS / Connection Failure)

**Log lines:**
```
WARNING | main:lifespan:35 —
  DB init skipped — will retry on first request:
  (ENOTFOUND) tenant/user postgres.oecrjhaankiwaghcfwii not found

asyncpg.exceptions.InternalServerError:
  (ENOTFOUND) tenant/user postgres.oecrjhaankiwaghcfwii not found
```

**Cause:**  
The Supabase/Neon PostgreSQL instance at `postgres.oecrjhaankiwaghcfwii` cannot be
resolved from DNS. This happens intermittently — the DB worked at 19:28 on 2026-04-28
but fails on 2026-05-04.

**Possible causes:**
- Supabase free-tier project is **paused** (auto-pauses after 1 week of inactivity)
- VPN / firewall blocking the hostname
- `.env` DB_URL points to wrong/stale host

**Fix:**
1. Go to your Supabase dashboard → check if the project is paused → click "Resume"
2. Verify `DATABASE_URL` in `.env` matches the current Supabase connection string
3. Test: `python3 -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('YOUR_DB_URL'))"`

---

## WARNINGS (Non-blocking but degrade functionality)

---

### [WARN-1] Celery Beat Schedule File Locked on Startup

**Log line:**
```
ERROR/MainProcess] Removing corrupted schedule file 'celerybeat-schedule':
  error(11, 'Resource temporarily unavailable')
```

**Cause:** An old `celery beat` process is still holding a lock on `celerybeat-schedule`
(a gdbm file). When the new beat process starts, it cannot open the file.

**Fix:** Before running `./start.sh`, kill existing celery processes:
```bash
pkill -f "celery" || true
rm -f /windows/auto-trade-pro/autotrade-backend/celerybeat-schedule* 2>/dev/null || true
```

---

### [WARN-2] NEWSAPI_KEY Not Configured

**Log line:**
```
WARNING | crawler.news_crawler:fetch_newsapi_headlines:95 —
  NEWSAPI_KEY not configured — skipping NewsAPI fetch
```

**Cause:** `NEWSAPI_KEY` environment variable is not set.  
**Impact:** NewsAPI headlines are skipped. RSS feed (Yahoo Finance + Finnhub) still works.  
**Fix:** Add `NEWSAPI_KEY=your_key` to `.env`

---

### [WARN-3] FinBERT Sentiment Model Unavailable

**Log line:**
```
WARNING | crawler.news_crawler:_load_finbert_pipeline:251 —
  FinBERT unavailable (Could not import module 'pipeline'...) — using keyword fallback
```

**Cause:** The `transformers` library's `pipeline` function is not importable (likely
missing `torch` or wrong `transformers` version).  
**Impact:** Sentiment analysis falls back to a simple keyword-based scorer.  
**Fix:** `pip install torch transformers` — or ignore if keyword fallback is acceptable.

---

## ROOT CAUSE ORDER OF PRIORITY

| # | Issue | Impact | Effort |
|---|-------|--------|--------|
| 1 | BUG-1: Port 8000 in use | App won't start at all | Trivial — add pkill to start.sh |
| 2 | BUG-4: DB unreachable | All API endpoints return 500 | Medium — resume Supabase project |
| 3 | BUG-2: 32767 arg limit | No candles saved → no signals → no trades | Easy — chunk the insert |
| 4 | BUG-3: asyncio loop conflict | Paper trade Celery task always fails | Medium — fix _run_async() |
| 5 | WARN-1: Beat file locked | Scheduled tasks delayed on startup | Trivial — add pkill + rm |
| 6 | WARN-2: No NewsAPI key | Reduced news coverage | Easy — add to .env |
| 7 | WARN-3: No FinBERT | Weaker sentiment scoring | Optional |

---

## HOW TO VERIFY FIXES

After applying BUG-1 + BUG-2 + BUG-3 + BUG-4:

```bash
# 1. Kill stale processes
pkill -f "uvicorn\|celery" || true
rm -f celerybeat-schedule*

# 2. Start fresh
cd /windows/auto-trade-pro/autotrade-backend
./start.sh 2>&1 | tee logs/startup_$(date +%Y%m%d_%H%M%S).log

# 3. Health check
curl http://localhost:8000/health

# 4. Portfolio (should not 500)
curl http://localhost:8000/api/v1/portfolio/

# 5. Check candles are being saved (after ~5 min)
curl http://localhost:8000/api/v1/signals/
```
