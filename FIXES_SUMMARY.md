# Audit Remediation — 2026-08-19

Fixes for the critical/high findings in [`docs/DEEP_AUDIT_2026-08-19.md`](docs/DEEP_AUDIT_2026-08-19.md).

**Test result:** 1308 passed · 27 failed · 5 errors · **0 collection errors**
(was 1240 passed · 28 failed · 10 errors · 2 collection errors).
Verified against a clean `HEAD` worktree: **zero net-new failures**, 6 pre-existing
problems resolved. The 27 remaining failures are all pre-existing and untouched.

Every fix below has a regression test that was **mutation-checked** — the defect was
re-introduced and the test confirmed to fail.

---

## D2 — Live order path raised `TypeError` on every call ✅

`engine/decision_router.py` passed `signal_id=` and `confidence=` to `place_real_order`,
which accepts neither. The `TypeError` was swallowed by the broad `except Exception` and
returned as a generic `RoutingOutcome.ERROR` — indistinguishable from a broker outage.
Latent only because `PAPER_MODE=true`.

Dropped both kwargs (Option A) and **passed `signal=signal` instead**. That is not
cosmetic: `place_real_order`'s Rule 3 confidence gate reads `getattr(signal, "confidence")`
and defaults to `100.0` when `signal is None`, so the gate was a no-op on this path.
Forwarding the signal arms it.

`tests/test_live_order_path.py` — binds the call site against the real signature via
`inspect.signature`, so any future drift fails without needing a broker.

## D3 — Price freshness checks were inert ✅

`age = now - cached.get("_ts", now)` evaluated to exactly `0.0` for every entry that never
had `_ts` stamped, so the 30s guard always passed and a days-old price was returned as
`age_seconds: 0.0`.

- `crawler/live_prices.py:86` — default changed to `0` (fail **closed**).
- `crawler/zerodha_ticker.py:47` — same inert default in `get_live_tick`, fixed.
- `crawler/zerodha_ticker.py` — `_ts` now stamped on the **`PRICE_CACHE` mirror**.
  (`LIVE_TICKS` already had one at `:125`; the brief's "add `_ts` in `on_ticks`" was
  half-right — the gap was the mirror, not the tick.)
- `crawler/live_prices.py` — `_ts` stamped in all three batch-fetch result dicts.
- `crawler/market_snapshot.py` — `_from_websocket_tick` now **honours** the
  `_age_seconds` it was already computing and discarding; past 30s it falls through to
  REST. This is the entry-price source for every live news trade.

> **Deviation from the brief, deliberate.** The brief asked for
> `entry["_ts"] = time.time()` on Redis hydration. That would relabel a snapshot up to
> its 900s TTL old as brand new — recreating the same lie. `publish_prices_to_redis`
> JSON-serialises the whole entry, so `_ts` already survives Redis; hydration now
> **preserves** it and falls back to the payload's own `published_at`.

`tests/test_price_freshness.py`

## D1 — News-only contract restored ✅

- `engine/decision_router.py` → `_TECHNICAL_TRADE_ORIGINATION_BLOCKED = True`
- `tasks/india_tasks.py:572` → `_NEWS_ONLY_BLOCKS_HUB_ENTRIES = True`

The brief refers to "two places" set to `False`; there was only **one**.
`india_tasks.py:1737` and `agent_loop.py:312` were already `True`. Comments updated to
record the decision and that the 2026-07-24 justification (the Bedrock outage) ended on
2026-08-17.

## D4 — Authentication on mutating routes ✅

**94 of 103** mutating routes now require the admin JWT (was 5).

Used `dependencies=[Depends(require_auth)]` on the route decorator, per the brief.
(My approved plan had said "append `_admin` to the signature"; the brief's decorator form
is both what you asked for and far safer across 89 edits — no signature rewriting.)

**9 routes deliberately left public** — they are POST but mutate nothing:
`/auth/login`, `/allocation/risk-profile`, `/allocation/rebalancing`, `/india/sip/project`,
the three `/sip/calculator*` endpoints, `/tax/calculate`, `/tax/classify-trade`.

`POST /agent/kill-switch` now requires the JWT **in addition to** `X-Kill-Confirm`.

**WebSockets** — all 7 endpoints protected via a new `require_ws_auth` in `api/auth.py`.
`Depends(require_auth)` **cannot** work on a WS route: `HTTPBearer.__call__` is annotated
`request: Request`, and a `WebSocket` is a sibling class, so the value is never injected
and the handshake dies with `TypeError` rather than a clean 401 (verified against
fastapi 0.136.3). Browsers also cannot set an `Authorization` header on `new WebSocket()`,
so the token arrives as a query param and the socket is closed with **1008** before
`accept()`.

Frontend updated so the UI keeps working — new `src/utils/wsAuth.js` (single place that
knows the scheme) used by `useWebSocket.js`, `LivePricesContext.jsx`,
`CandlestickChart.jsx`.

> Accepted trade-off: a query-string token can appear in access logs. Same-origin only,
> and the alternative — leaving portfolio/trade streams fully public — is worse.

`tests/test_route_auth.py` — includes a **sweep** over the live app, so a new unprotected
mutating route fails the build rather than relying on anyone remembering.

## D6 — Kite rate limiting + order idempotency ✅

New `crawler/zerodha_kite_limiter.py`, cloning the proven Redis Lua limiter in
`utils/llm.py`. Async **and** sync variants (`fetch_prices_batch` runs in an executor
with no event loop). Uses `utils.cache.get_redis`, which re-creates the client when the
event loop changes — without that it silently stops limiting after the first task in each
Celery worker.

> **Important:** wrapping `_get`/`_post` alone would have covered **zero** quote traffic.
> `KiteClient.get_quote/get_ohlc/get_ltp` each inline their own `httpx.AsyncClient` and
> bypass `_get`. All three chokepoints are wrapped, plus `zerodha_kite_lib` (sync) and
> `services/kite_service.py`, which builds its own `KiteConnect`.

Buckets (configurable in `utils/config.py`): `KITE_QUOTE_RPS=1`, `KITE_ORDER_RPS=10`, and
a **reserved** `KITE_EXIT_RPS=1` on a separate key. The stop-loss path
(`get_live_prices(..., exit_bucket=True)` from `_fast_sl_check` and `trade_simulator`)
draws from the reserved bucket so a dashboard burst can never delay an exit.
Fails **open** after `KITE_LIMITER_MAX_WAIT=5s` — a coordination outage must never wedge
the trading loop.

**Idempotency** (Rule 11 in `place_real_order`): checks the live order book for our
`ATP_{signal_id}` tag and returns the existing order instead of double-placing. With
`task_acks_late=True`, a worker killed mid-task **will** redeliver. `REJECTED`/`CANCELLED`
orders are not treated as duplicates — those are retry opportunities.

`tests/test_kite_limiter.py`

## D9 — Orphaned beat entries removed ✅

Deleted `india-options-every-15min`, `india-equity-options-enrich`, `fno-expiry-sweep-daily`
— all named tasks deleted with F&O in `91457d7`.

### D9-b — a fourth orphan, previously unknown 🔴

The generalised test found one the audit missed: `corporate-action-check-daily` scheduled
`tasks.india_tasks.corporate_action_check`, but the task registers as
`tasks.corporate_action_check` (`india_tasks.py:1711`). **The name never resolved, so
split/bonus detection — which adjusts position units and entry/stop/target — has never
run.** Fixed.

`tests/test_beat_schedule.py` asserts every scheduled task resolves in the Celery
registry, so the next one is caught automatically.

## D12 — pytest works out of the box ✅

`autotrade-backend/pytest.ini` (backend root, not repo root — `tests/__init__.py` needs the
backend root on `sys.path`). Added `asyncio_default_fixture_loop_scope = function` beyond
the brief, to silence a per-run deprecation warning.

Bare `pytest` from the backend root: **3m26s / 8 errors → 7s / 0 errors**, and it no longer
fires live HTTPS crawls during collection.

Both broken files **rewritten** against the current API:
- `test_fundamental_analyzer.py` — targets async `fetch_fundamentals_upstox`.
- `test_paper_trading.py` — `SignalGenerator` is gone; rewritten against
  `generate_signal()`. Fixing the import exposed further pre-existing staleness in the
  same file (`PnLCalculator` method renames, `Position`→`OpenPosition`, `FillResult`
  fields) — all fixed. Its `TestRiskManager` class is genuinely obsolete
  (`RiskManagerAgent` now takes a `portfolio_ctx` dict) and is **skipped with a reason**;
  `tests/test_risk_manager_*.py` already cover the current gate.

## D11-o — dependencies pinned ✅

`boto3==1.43.55`, `openai==2.44.0`.

> **Deviation, deliberate.** The brief said `openai==1.40.0` — that is a **major-version
> downgrade** from the 2.44.0 actually installed and running. Pinned to the working
> versions instead, which achieves the goal (no surprise upgrades) without changing
> behaviour.

## D11-p — log rotation ✅

> **The brief's version would have taken production down.** The four units are *user*
> units running as `cis`; `/var/log` is not writable by `cis` and `/var/log/autotrade`
> does not exist, so `StandardOutput=append:/var/log/autotrade/…` makes systemd **fail to
> start all four services** on the next restart.

Units now write to `logs/<svc>.{log,err}` (user-writable, already present). Added
`deploy/logrotate/autotrade` (daily, 14 rotations, compress, `copytruncate` — required
because systemd holds the fd open) and `deploy/systemd/autotrade-logrotate.{service,timer}`
so rotation runs as a `--user` timer with **no sudo**. Validated by running `logrotate` as
`cis`. `logs/` added to `.gitignore`.

The app's own loguru log already rotates via `utils/logger.py` and is excluded so the two
mechanisms don't fight.

## D11-q — frontend credentials removed ✅

Values in `autotrade-frontend/.env` replaced with placeholders; keys documented in
`.env.example`. The file is **untracked** (gitignored), so there was nothing to purge from
history — "commit the removal" did not apply.

## D5 — Indicator lookahead mitigated ✅

`compute_indicators(df, *, exclude_forming_bar=False)`. When true it drops the last
(still-forming) bar before computing anything. Default unchanged, so all 13 production
call sites behave exactly as before; **Path F opts in with
`compute_indicators(df, exclude_forming_bar=True)`.**

> **Naming deviation, deliberate.** The brief specified `bar_closed=True → drop the last
> bar`, which reads backwards (asserting a bar *is* closed should mean keep it).
> `exclude_forming_bar` says what it does. Keyword-only, so it can't be bound positionally
> by mistake.

`tests/test_indicators_forming_bar.py` — includes a test that the flag actually changes
the answer, so it can't silently become a no-op.

---

## Additional fix (found during D12, approved)

`engine/fundamental_analyzer.py:690` called `fetch_fundamentals_yfinance`, deleted in the
Upstox migration. The `NameError` fired on **every** call and was swallowed by a bare
`except` at `logger.debug`, so the yfinance leg has silently contributed nothing since
that migration. Dead branch removed, remaining swallow raised to `logger.warning`, stale
docstrings cleaned.

Also fixed `tests/test_decision_engine_tools.py`, which patched that same dead symbol —
`patch()` raised `AttributeError`, so the test never actually ran.

## Housekeeping

- `utils/llm.py` header rewritten: it documented `amazon.nova-pro-v1:0` (17 stale
  references) while the configured model is `nvidia.nemotron-super-3-120b`.
- Deleted the stale duplicate `autotrade-backend/deploy/systemd/` (4 files, missing the
  uvicorn and news-engine units; superseded by the repo-root `deploy/`).

---

## ⚠️ Review notes

1. **This commit carries another author's uncommitted work.**
   `engine/decision_router.py` had an uncommitted change from 2026-08-19 16:44 threading
   `product=` through to `place_real_order` and `open_paper_trade`. It is at the *same
   call site* as the D2 fix and could not be separated. The change is correct and is
   preserved. It also broke two integration tests whose stub didn't accept the new kwarg —
   stubs widened to `**kw`.

2. **`engine/hub_universe.py` is deliberately NOT in this commit.** It carries the same
   author's uncommitted universe widening (`top_n` 3000→20000, `min_turnover_cr` 1.0→0.0
   — audit D13). Left untouched for them to decide on.

3. **Services were not restarted.** The systemd unit change needs an owner-chosen restart
   window:
   ```bash
   cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user restart autotrade-uvicorn autotrade-celery-worker \
                            autotrade-celery-beat autotrade-news-engine
   systemctl --user enable --now autotrade-logrotate.timer
   ```

4. **The 157 root-level scratch scripts were not deleted.** `testpaths` already
   neutralises the pytest harm; removing 137 tracked files would swamp this review.
   Recommended as a separate commit.

5. **Not pushed.** Ready for review.
