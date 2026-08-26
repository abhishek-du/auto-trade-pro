# PHASE 10 — PRODUCTION HARDENING + DEPLOYMENT

**Deployed:** 2026-08-25 ~23:03–23:09 IST · **Commit:** `1f40a9bc5d1e`
**Migration:** none · **Strategy change:** none · **Orders:** 0 · **Paper trades:** 0

---

## 1. Executive summary

Three proven defects fixed and deployed. Nothing speculative, no strategy touched.

| # | defect | status |
|---|---|---|
| 1 | `causal_events.news_id` NULL since 2026-07-22 | **DEPLOYED**, forward-only |
| 2 | `simulation_logs` has no emitting-process identity | **DEPLOYED**, additive JSON |
| 3 | `pytest` could write to the production database | **DEPLOYED**, fail-closed guard + `autotrade_test` |

**BUG-1 remains unfixed and still blocks Hub origination. Master Intelligence remains
disconnected. `PAPER_MODE` unchanged. No migration. No historical row rewritten.**

Two things are worth flagging before the detail:

- **Part of the deployment happened implicitly.** `autotrade-celery-worker`,
  `autotrade-celery-exit-worker` and `autotrade-news-engine` run under
  `watchmedo auto-restart --pattern="*.py"`, so they hot-reloaded my edits as I saved them
  (news-engine child restarted 23:02:53, default worker 23:03:00). Only `scan-worker` and
  `trade-worker` run celery directly and needed an explicit restart. This is a property of the
  deployment architecture, not a choice I made — but it means the window between edit and
  deployment was seconds, not a controlled step.
- **A test caught a real gap in my own setup.** `Base.metadata.create_all` does **not** build a
  faithful test schema (§6).

---

## 2. Files changed

**Production (2):**

```
autotrade-backend/engine/decision_router.py    +26
autotrade-backend/news_discovery_engine.py     +99 −9
```

**Test infrastructure (5):**

```
autotrade-backend/tests/conftest.py                    (guard + .env.test support)
autotrade-backend/tests/TEST_DATABASE.md               (new)
autotrade-backend/tests/test_db_isolation_guard.py     (new, 4 tests)
autotrade-backend/tests/test_news_id_traceability.py   (new, 8 tests)
autotrade-backend/tests/test_simulation_log_emitter.py (new, 5 tests)
```

**Not committed, local only:** `autotrade-backend/.env.test` — gitignored by the root
`.gitignore`'s `.env.*` rule, verified with `git check-ignore`. Production `.env` untouched
(mtime still 2026-08-25 08:15, predating this session).

**Migrations:** none. `skip_code` was deliberately not implemented (§9).

---

## 3. Database identities

| | host | port | database |
|---|---|---|---|
| production | localhost | 5432 | `autotrade_pro` |
| test | localhost | 5432 | **`autotrade_test`** |

Same server, different database — which the guard compares on **host + port + database**, not on
the raw URL string, so the same database reached with a different password or driver is still
caught. Created with the app's own role (`autotrade`, which carries `CREATEDB`); no Docker, no
sudo, no production data copied.

**Proof they differ** — the guard's own negative test, run deliberately:

```
TEST_DATABASE_URL=…/autotrade_pro  ALLOWED_TEST_DB_NAMES=autotrade_pro  pytest …

  Exit: Tests aborted: no explicitly allowlisted test database is configured.
  TEST_DATABASE_URL resolves to the same database as DATABASE_URL
  (postgresql+asyncpg://autotrade:***@localhost:5432/autotrade_pro).
  Refusing to run the suite against production.
```

Password redacted, as designed. A second negative test with nothing configured aborts with
*"The suite will not fall back to DATABASE_URL"*.

---

## 4. news_id traceability

**Root cause (Phase 7):** `news_id` was 100% populated 2026-07-16→07-21 by
`crawler/event_pipeline.py`, then 0% from 07-22 when origination moved to
`news_discovery_engine`, whose `CausalEvent` site hardcoded `news_id=None` — deliberately and
with a comment, because that path had no `NewsItem` to link at the time.

**What changed:** the RSS insert already carried `RETURNING id` and discarded it. That id is now
threaded `process_ticker → _build_evidence → CausalEvent`, and the NSE announcement insert
gained the same `RETURNING`.

**The duplicate case is the one that matters.** `ON CONFLICT DO NOTHING` returns NULL, so a
repeated headline would silently produce a NULL link again — reintroducing the defect for exactly
the rows most likely to recur. `_resolve_news_id()` re-reads using the **conflict target itself**:

```sql
md5(headline) = md5(:headline)
AND (COALESCE(published_at, crawled_at))::date
    = (COALESCE(CAST(:published_at AS timestamp), now()))::date
AND crawled_at >= TIMESTAMP '2026-08-21 00:00:00'
```

All three parts of `uq_news_items_headline_day` are reproduced, **including the partial-index
predicate** — a conflict can only have been raised against a row inside that range, and without
the predicate an older duplicate outside it could be returned instead.

**It never guesses.** No timestamp proximity, no symbol matching, no fuzzy headline comparison,
no `LIMIT 1`/`ORDER BY`. If the key does not resolve to exactly one row it returns `None` and
the event is written with `news_id` NULL. A test asserts those banned constructs are absent.

**Paths that legitimately have no NewsItem** — anomaly-catalyst discovery (`:1255`) and the
pre-market queue (`:1623`) — still pass `None`, and NULL there is correct.

**No historical row was backfilled.** Phase 7 established the historical linkage is unrecoverable:
0 exact matches on every candidate key, `event_title` is a category label with only 180 distinct
values across 6,703 events, and 0 of 400 sampled events had *any* news item crawled in the 60 s
before them.

---

## 5. Emitter identity

`engine/decision_router.py::_log_intent_audit` now writes:

```json
"emitter": { "pytest": false, "process": "celery", "pid": 2879012 }
```

`PYTEST_CURRENT_TEST` is set by pytest per test, so `pytest: true` marks rows a test actually
caused. `process` is `argv[0]`'s basename — enough to tell the workers, the news engine and
uvicorn apart without leaking a path.

Additive JSON: no schema change, no routing change, no execution change. Tests assert the
function touches no session, no `await`, no `RoutingOutcome` — it is a pure metadata read.
**Historical rows are untouched and TESTCO is not retroactively labelled.**

This field immediately paid for itself: it is what proves §8's "0 production rows written by a
test", which previously required a code trace.

---

## 6. Test isolation — and a gap a test caught in my own setup

The guard aborts unless `TEST_DATABASE_URL`, `ALLOWED_TEST_DB_HOSTS` and
`ALLOWED_TEST_DB_NAMES` name an allowlisted database that is not production. It opens no
connection, never falls back to `DATABASE_URL`, redacts passwords, and runs at **conftest import
time** — before collection, because test modules bind `db.database` during collection and a
session-scoped fixture would be too late. It overrides `settings.DATABASE_URL`, the single value
both session factories derive from: `db/database.py:26` (import time) and `tasks/_db.py:38`
(`celery_session`, every call).

**Allowlist, not denylist** — a denylist of known production hosts silently permits every host
nobody thought to add.

### The gap

My first schema build used `Base.metadata.create_all`, and
`test_duplicate_headline_resolves_to_the_same_existing_id` failed: the second insert did **not**
conflict. Cause: `uq_news_items_headline_day` lives in the ~60-statement inline DDL block in
`db/database.py::init_db()`, **outside the ORM metadata**. CLAUDE.md documents this dual-schema
arrangement; I had not accounted for it.

**A metadata-only test database silently accepts duplicate headlines** — it would have passed a
weaker test and hidden the exact case the resolver exists for. Fixed by building the test schema
with the project's own `init_db()`, and documented in `tests/TEST_DATABASE.md` so the next
person does not repeat it.

---

## 7. Tests

All against **`autotrade_test`**. Never production.

| suite | result |
|---|---|
| `test_db_isolation_guard.py` | **4 passed** — incl. a real INSERT proven to land in the test DB |
| `test_news_id_traceability.py` | **8 passed** — new / duplicate / absent / one-to-one across 4 headlines / same-headline-different-day / 3 AST guards |
| `test_simulation_log_emitter.py` | **5 passed** |
| guard negative tests | **2 aborts**, as designed |
| **full suite** | **1,729 passed** · 27 failed · 7 skipped · 5 errors |

Baseline (Phase 5): 1,711 passed / 28 failed. **+18 passing. Failure sets diffed line by line:
ZERO new failures**, and one pre-existing failure
(`test_daily_watchdog_stays_quiet_when_a_small_partial_write_is_newest`) now passes — it had
been reading production data.

---

## 8. Database mutation audit

Production counts, immediately before and after the full suite:

| table | before | after | delta |
|---|---:|---:|---:|
| `simulation_logs` | 18,237 | 18,237 | **+0** |
| `paper_trades` | 45 | 45 | **+0** |
| `agent_decisions` | 8,062 | 8,062 | **+0** |
| `causal_events` | 11,672 | 11,672 | **+0** |
| `news_items` | 37,424 | 37,424 | **+0** |
| TESTCO.NS rows | 144 | 144 | **+0** |

**Production rows marked `emitter.pytest=true`: 0.**
The same run put **5 pytest-marked rows and 2 TESTCO rows into `autotrade_test`** — the
contamination landing where it belongs. That is the complete proof loop.

A later reading shows `news_items` at 37,427 (+3). That is the **live news engine** ingesting
RSS while the market is closed — 0 of those rows are pytest-marked. Tables the running system
writes to cannot be held constant, so the contamination-sensitive check is
`simulation_logs`/`paper_trades`/TESTCO, all +0.

**No INSERT, UPDATE or DELETE was issued against production by this phase.** `CREATE DATABASE
autotrade_test` was the only DDL, on a new database.

---

## 9. Not implemented, deliberately

**`skip_code`** — needs a schema migration, and the prose it would replace is 92.0% unique
strings with 38.7% unclassifiable and 13.9% ambiguous (Phase 6). Implementing it now would mean
either a migration for a field whose taxonomy is not yet trustworthy, or regex-backfilling
LLM prose into codes — manufacturing a classification rather than recording one. Documented as
future work.

---

## 10. Deployment

| | |
|---|---|
| previous commit | `fec95f3` |
| **new commit** | **`1f40a9bc5d1e`** |
| migration | **none** |
| deployed | 2026-08-25 ~23:03–23:09 IST |

**Services and how each received the change:**

| service | mechanism | picked up |
|---|---|---|
| `autotrade-news-engine` | watchmedo | **auto**, child restarted 23:02:53 |
| `autotrade-celery-worker` | watchmedo | **auto**, child restarted 23:03:00 |
| `autotrade-celery-exit-worker` | watchmedo | auto |
| `autotrade-celery-scan-worker` | direct | **explicit restart** |
| `autotrade-celery-trade-worker` | direct | **explicit restart** |
| `autotrade-celery-beat` | — | not restarted, no change affects it |
| `autotrade-uvicorn` | — | not restarted |

Only the two units that cannot hot-reload were restarted, and both call
`decision_router` so both needed the emitter change.

### Post-deployment health

```
all 7 services   active, NRestarts=0
queue consumers  default 4 · exit_queue 2 · scan_queue 2 · trade_queue 2
                 (2 = celery MainProcess + 1 pool worker at concurrency=1, not a duplicate)
trade worker     cycling — "Starting cycle" at 23:10:04
errors           ImportError / ModuleNotFound / SyntaxError / OperationalError: 0 in all four logs
```

**Strategy safety, re-proven after deployment:**

```
BUG-1 still blocks Hub candidate creation : YES  (settings read :610, local import :632)
Master Intelligence can reach candidates  : NO
Order submission reachable                : NO
A paper trade can be opened               : NO
A live trade can be opened                : NO
paper_trades opened in the last 2h        : 0
gate events in the last 2h                : 0
```

---

## 11. Status

| item | status |
|---|---|
| BUG-1 | **UNFIXED, intentionally** — CONFIRMED still blocking |
| BUG-2 | **DEPLOYED / STATICALLY VERIFIED / LIVE MARKET VERIFICATION PENDING** |
| Master Intelligence | **DISCONNECTED** — RULED OUT as reachable |
| news traceability | **DEPLOYED** — live effect not yet observable (§13) |
| test isolation | **CONFIRMED** — proven by counts and by the emitter field |

---

## 12. Rollback

```bash
git revert 1f40a9bc5d1e
systemctl --user restart autotrade-celery-scan-worker autotrade-celery-trade-worker
# watchmedo reloads news-engine, celery-worker and exit-worker on its own
```

No migration to reverse, no schema change, no data to restore. Reverting restores
`news_id=None`, removes the `emitter` key from *new* rows only, and restores the previous
`conftest.py` — after which pytest can reach production again, which is the trade-off.

`autotrade_test` can be left in place; nothing in production references it. To remove it:
`DROP DATABASE autotrade_test;`.

---

## 13. Remaining unknowns

| # | item | classification |
|---|---|---|
| 1 | **BUG-2 under real market load** — fifth phase requesting this. No NSE session has elapsed since the 2026-08-25 20:01 IST deployment; 0 cycles inside 09:15–15:30 IST | **EVIDENCE NOT AVAILABLE** |
| 2 | **news_id populating live** — the engine has created no `causal_event` since 16:01 IST (it only creates one on tradeable news), so the change is deployed but its live effect is unobserved | **EVIDENCE NOT AVAILABLE** |
| 3 | Whether other tests write to tables beyond `simulation_logs` — only that path was traced; the guard now covers all of them regardless | **EVIDENCE NOT AVAILABLE** |
| 4 | NSE's API returned `403 Forbidden` in the news-engine log during this phase — external, pre-existing, unrelated to this change, not investigated | **OBSERVABILITY GAP** |
| 5 | `skip_code` taxonomy reliability | **INCONCLUSIVE** — §9 |

**Next:** measure BUG-2 after the next session (one query against
`logs/celery-trade-worker.log`), and confirm `news_id` is non-NULL on the first `causal_event`
the engine creates.

---

## Mandatory final safety table

| | |
|---|---|
| production files modified | **YES** — 2, both additive observability |
| tests-only files modified | **YES** — 5 |
| `.env` modified | **NO** |
| runtime settings modified | **NO** |
| strategy parameters changed | **NO** |
| Master Intelligence connected | **NO** |
| BUG-1 fixed | **NO** |
| BUG-2 changed | **NO** |
| BUG-2 verified under market load | **NO** — EVIDENCE NOT AVAILABLE |
| orders submitted | **NO** |
| paper trades opened | **NO** |
| database INSERT (production) | **NO** |
| database UPDATE (production) | **NO** |
| database DELETE (production) | **NO** |
| historical rows modified | **NO** |
| tests executed | **YES** |
| tests executed against production DB | **NO** |
| tests executed against test DB | **YES** — `autotrade_test` |
| execution modules invoked | **NO** (production); test-DB only under pytest |
| unexpected mutation | **NO** |
| deployment successful | **YES** |
| rollback required | **NO** |
