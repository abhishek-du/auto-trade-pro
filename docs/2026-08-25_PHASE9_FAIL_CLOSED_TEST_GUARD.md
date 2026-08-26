# PHASE 9 — FAIL-CLOSED TEST ISOLATION + BUG-2 VERIFICATION

**One file changed: `autotrade-backend/tests/conftest.py`, +159 lines, test infrastructure only.**
No production file, `.env`, runtime setting or database row was touched. No test was executed.

---

## 1. Executive verdict

| | |
|---|---|
| **BUG-2 verification** | **EVIDENCE NOT AVAILABLE** — fourth consecutive phase. No NSE session has elapsed since the 2026-08-25 20:01 IST deployment. |
| **Fail-closed DB guard** | **IMPLEMENTED** in `tests/conftest.py`, statically verified against 20 checks. |
| **Immediate consequence** | **`pytest` now aborts on this machine.** `TEST_DATABASE_URL`, `ALLOWED_TEST_DB_HOSTS` and `ALLOWED_TEST_DB_NAMES` are unset, so the guard fails closed — which is the requested behaviour, and it means the suite cannot be run until an operator configures a throwaway database. |

**Two Phase 8 classifications are corrected below**, both caused by the same mistake I made twice:
counting `grep` line matches instead of parsing the AST.

---

## 2. BUG-2 verification — **EVIDENCE NOT AVAILABLE**

System clock: **2026-08-25 22:38 IST**. Deployment: 2026-08-25 20:01 IST, after the 15:30 close.

```
trade-worker cycles by date    : 2026-08-25 only (158)
cycles inside 09:15-15:30 IST  : 0
last cycle                     : 2026-08-25 22:38:03
```

| cycle class | count |
|---|---:|
| A — returned at the market-status check before BUG-1 | **158 (100%)** |
| B — reached `:610`, raised `UnboundLocalError` | 0 |
| C — completed past `:610` | 0 |
| D — unknown | 0 |

Every requested market-hours metric — coverage, p75/p95 interval, gap counts, worker isolation,
concurrent Hub load, expired-task evidence — is **unmeasurable**, because the condition that
produces them has not occurred.

The question the brief poses — *"did the dedicated trade worker maintain ~one cycle per 60 s
during real market-hours load while the Hub worker was busy?"* — remains unanswered.
**No extrapolation from after-hours cycles is offered.**

---

## 3. The exact change

`tests/conftest.py`, module-level, above both existing fixtures. Neither fixture was modified.

### What it requires

| variable | purpose |
|---|---|
| `TEST_DATABASE_URL` | full SQLAlchemy URL of a throwaway database |
| `ALLOWED_TEST_DB_HOSTS` | comma-separated hosts permitted to hold a test database |
| `ALLOWED_TEST_DB_NAMES` | comma-separated database names that are test databases |

**None of the three is set anywhere** — not in `.env`, not in the shell. Per the brief, `.env` was
**not modified**, so the suite fails closed until an operator sets them deliberately.

### What it does

1. Reads the three variables from the environment. **Never reads `DATABASE_URL` as a fallback.**
2. Parses `TEST_DATABASE_URL` with `sqlalchemy.make_url` — parsing only, no connection.
3. Requires host **and** database name to be non-empty, then requires each to be present in its
   allowlist.
4. Parses `settings.DATABASE_URL` via the project's own pydantic configuration and refuses to
   proceed if the test URL resolves to the **same** host + port + database. Compared on identity,
   not on the raw string, so the same database reached with a different password or driver is
   still caught.
5. Only then sets `settings.DATABASE_URL` to the test URL.
6. Defensively rebinds `db.database.engine` and `db.database.AsyncSessionLocal` if that module is
   already in `sys.modules`.

### Why it overrides `settings.DATABASE_URL` and not `AsyncSessionLocal`

There are **two** session factories, and they resolve their URL at different times:

| factory | when it reads the URL |
|---|---|
| `db/database.py:26` | **module import time** |
| `tasks/_db.py:38` (`celery_session`) | **on every call** — `from utils.config import settings` inside the function |

Patching only the first leaves the second pointed at production. The single value both derive
from is `settings.DATABASE_URL`, so that is what the guard overrides.

### Why it runs at module level rather than as a fixture

pytest imports `conftest.py` **before** collecting test modules. Test modules import
`db.database` during collection, which binds `AsyncSessionLocal` to whatever
`settings.DATABASE_URL` said at that moment. A session-scoped autouse fixture runs **after**
collection — too late to matter. The guard is therefore module-level code, deliberately, and the
static audit checks that ordering rather than assuming it.

---

## 4. Why the guard fails closed

Seven distinct abort conditions, each calling `pytest.exit(..., returncode=4)`:

| # | condition |
|---|---|
| 1 | any of the three variables unset |
| 2 | `TEST_DATABASE_URL` unparseable |
| 3 | host or database name missing from the URL — *identity ambiguous, therefore refused* |
| 4 | host not in `ALLOWED_TEST_DB_HOSTS` |
| 5 | database name not in `ALLOWED_TEST_DB_NAMES` |
| 6 | `DATABASE_URL` unparseable, so the comparison cannot be made |
| 7 | test URL resolves to the same database as `DATABASE_URL` |

Headline on every path:

```
Tests aborted: no explicitly allowlisted test database is configured.
```

**Allowlist, not denylist** — deliberately. A denylist of known production hosts silently permits
every host nobody thought to add.

**What it deliberately does not do:** connect to any database, create one, guess a SQLite file,
substring-match `"test"` anywhere, check `PAPER_MODE` as a proxy, or fall back to `DATABASE_URL`.
Passwords are redacted through `URL.render_as_string(hide_password=True)` in every message.

---

## 5. Static proof — 20 checks, all passing

Run via AST parse of `tests/conftest.py`. **No application module was imported and no test was
executed.**

```
[PASS] guard is invoked at module level                       statement #11
[PASS] guard runs BEFORE every fixture definition             guard 11, first fixture 12
[PASS] guard is not decorated as a fixture
[PASS] reads TEST_DATABASE_URL, never substitutes DATABASE_URL
[PASS] assigns settings.DATABASE_URL exactly once
[PASS] the assigned value is the TEST url
[PASS] fails closed on multiple distinct conditions           7 _abort() sites
[PASS] _abort() calls pytest.exit (does not merely warn)
[PASS] failure messages go through _redact()
[PASS] _redact uses hide_password=True
[PASS] guard never connects or mutates                        banned calls: none
[PASS] guard does not create/drop a database
[PASS] guard does not guess a SQLite fallback (code, not comments)
[PASS] rebinds db.database if already imported
[PASS] overrides the single value both factories read
[PASS] guard requires no patching by individual tests
[PASS] uses an allowlist
[PASS] does not substring-match 'test' in the URL
[PASS] existing market-hours fixture preserved
[PASS] existing confirmation fixture preserved
```

**One check was wrong and I corrected the check, not the guard.** The first version searched the
whole file for `"sqlite"` and failed on the word appearing in a comment that explains the guard
does *not* use one. It now runs against `ast.unparse(tree)` — a code-only view with comments and
docstrings stripped — so prose in the rationale block can neither satisfy nor fail a check.

---

## 6. The existing market-hours fixture — **assessed, not changed**

```python
@pytest.fixture(autouse=True)
def _market_always_open():
    with patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
        yield
```

**Why it is safe as a deterministic test fixture:** `authorize_trade_intent` gates every
`TradeIntent` on real NSE hours. Without this patch, gate tests would pass or fail purely on what
time of day the suite happened to run. Its own docstring records the incident that motivated it
(SHAKTIPUMP.BO opening at 15:51 IST). Making a time-dependent gate deterministic is correct test
practice.

**Why it becomes dangerous when the test database is production:** it removes the last check that
would have refused the write. All 10 TESTCO rows I created were written between 16:59 and 20:34
IST — hours after the close — and reached `EXECUTED_PAPER` *because* this fixture had opened the
gate. The fixture converted an out-of-hours safety refusal into a successful production write.

**How the new guard closes it:** the fixture's blast radius is now bounded by which database the
suite can reach. With the guard in place, `settings.DATABASE_URL` points at an allowlisted test
database before any test module imports `db.database` — so an ungated write goes to the throwaway
database, or the suite never starts.

**The fixture was not modified, and should not be.** It is not the defect; it was the amplifier.

---

## 7. `tests/test_news_side_from_classifier.py` — **SAFE**, correcting Phase 8

Phase 8 classified this file **UNKNOWN** because it appeared to call `maybe_direct_trade` without
patching a session or the executor. Static trace:

```
tests/test_news_side_from_classifier.py:72    i_dn  = src.index("await maybe_direct_trade(")
tests/test_news_side_from_classifier.py:75    i_llm = src.index("await llm_tooluse_candidate(", i_dn)
```

Those are **string literals passed to `str.index()`** — the test asserts on the *source text* of
the production module, checking the order in which two calls appear. It never executes either
function. It contains zero `patch(...)` targets and zero references to `execute_trade_intent` or
`AsyncSessionLocal` because it needs none.

```
TEST → (no call) → no session → no executor → no DB write
```

**Classification: SAFE. CONFIRMED. Phase 8's UNKNOWN is withdrawn.**

### The recurring mistake, named

This is the **second** false lead produced the same way:

| phase | claim | reality |
|---|---|---|
| 7 | *"`test_execution.py` has 13 `execute_trade_intent` calls — highest priority"* | 13 **patch targets**; AST shows **0** calls. Corrected in Phase 8. |
| 8 | *"`test_news_side_from_classifier.py` is UNKNOWN — calls `maybe_direct_trade`"* | 2 **string literals** inside `src.index(...)`; **0** calls. Corrected here. |

Both came from `grep -c`, which counts lines mentioning a name — imports, comments, patch
strings, string literals. **For "does this code call X", parse the AST.** Every inventory in
Phase 8 §3 and every check in §5 above was built that way; these two predate that discipline.

---

## 8. TESTCO status

| | |
|---|---|
| rows before Phase 9 | 144 |
| **rows after Phase 9** | **144** |
| newest row | 2026-08-25 15:04:16 (unchanged) |
| rows created during Phase 9 | **0** |
| rows deleted or modified | **0** |
| `simulation_logs` total | 18,237 |

Verified by `SELECT` only. That SELECT used the production session because the guard applies
under pytest, and **no pytest process was started in this phase**.

---

## 9. Files changed

```
 autotrade-backend/tests/conftest.py | 159 ++++++++++++++++++++++++++++++++++++
 1 file changed, 159 insertions(+)
```

Nothing outside `tests/`. Verified against `engine/`, `tasks/`, `db/`, `utils/`,
`paper_trading/`, `api/`, `news_discovery_engine.py`, `deploy/` and `.env` — all clean.
`.env` mtime is unchanged at Aug 25 08:15, which predates this session's work.

The four other modified files in the tree (`crawler/live_prices.py`, `extract_telegram.py`,
`scripts/backtest_day_rules.py`, `telegram_analysis.md`) were modified before this session began
and are untouched by me.

---

## 10. Second-layer controls — **DESIGN ONLY, not implemented**

Per Part J, no second layer was added. Database isolation remains the primary control **because
it is the only one that is not opt-in**: a `PAPER_MODE` assertion, a credential-absence check or
a mocked broker each protect one path and depend on the test author cooperating. The database
guard protects every test, including ones not yet written, and it fails before any code path has
a chance to matter.

Designs, for a later decision:

| control | value | why it is secondary |
|---|---|---|
| test-time `PAPER_MODE` assertion | blocks the live-order branch | `PAPER_MODE=true` already; adds a second lock on a door that is closed |
| broker-credential absence assertion | makes a real order impossible | the broker client is already mocked in the only two tests that reach it |
| execution API deny-by-default under pytest | strongest for orders | needs a hook inside production execution code — **out of scope; would be a production change** |
| network/broker mocks | prevents live HTTP | partially present via the existing snapshot fixture |

---

## 11. Findings

### Proven

| # | finding | classification |
|---|---|---|
| 1 | The guard runs at conftest import, before any fixture and before collection | **CONFIRMED** — AST |
| 2 | It cannot fall back to `DATABASE_URL` | **CONFIRMED** — AST |
| 3 | It fails closed on 7 distinct conditions | **CONFIRMED** |
| 4 | It never connects, creates or mutates a database | **CONFIRMED** — no banned call in its body |
| 5 | It redacts passwords | **CONFIRMED** |
| 6 | It covers both session factories via `settings.DATABASE_URL` | **CONFIRMED** |
| 7 | It requires no cooperation from individual tests | **CONFIRMED** |
| 8 | `test_news_side_from_classifier.py` never calls `maybe_direct_trade` | **CONFIRMED** — corrects Phase 8 |
| 9 | `pytest` currently aborts, because nothing is configured | **CONFIRMED** |
| 10 | No production file, `.env` or DB row changed | **CONFIRMED** |

### Unproven

- **BUG-2 under market load.** §2, fourth phase.
- **That the guard behaves correctly at runtime.** It is statically verified and **was not
  executed** — the brief forbids running pytest. Its first real exercise will be an operator
  running the suite, which will abort with the configuration message. **EVIDENCE NOT AVAILABLE**
  until then.
- **That no other test writes to tables other than `simulation_logs`.** Phase 8 traced only that
  path. **EVIDENCE NOT AVAILABLE.**
- **Whether any pytest plugin imports `db.database` before conftest.** The guard rebinds
  defensively if so, but the scenario was not observed. **INCONCLUSIVE.**

### Observability gaps carried forward

`simulation_logs` still records no emitting process; Celery still logs nothing on task expiry;
`causal_events.news_id` remains unlinked, untouched per Objective J.

---

## 12. Recommended next step

**One decision, then one measurement.**

1. **Decide whether to configure a test database.** The suite is now unrunnable until
   `TEST_DATABASE_URL`, `ALLOWED_TEST_DB_HOSTS` and `ALLOWED_TEST_DB_NAMES` are set. That is the
   behaviour that was asked for, and it is a real operational change — CI and any local
   `pytest tests/` will abort with the configuration message. If that trade-off is not wanted,
   revert `tests/conftest.py`; the guard is one self-contained block and reverting it restores
   the previous behaviour exactly.

2. **Then take the BUG-2 measurement** after the next session — one query against
   `logs/celery-trade-worker.log`, outstanding since Phase 5.

**Not recommended:** relaxing the guard to "warn instead of abort". That reproduces the state
Phase 8 documented, in which the suite could write to production and nothing stopped it.

---

## Mandatory final safety table

| | |
|---|---|
| production files modified | **NO** |
| tests-only files modified | **YES** — `tests/conftest.py` |
| `.env` modified | **NO** |
| runtime settings modified | **NO** |
| strategy parameters changed | **NO** |
| Master Intelligence connected | **NO** |
| BUG-1 fixed | **NO** |
| BUG-2 modified | **NO** |
| orders submitted | **NO** |
| paper trades opened | **NO** |
| database INSERT | **NO** |
| database UPDATE | **NO** |
| database DELETE | **NO** |
| tests executed | **NO** |
| execution modules invoked | **NO** |
| database connections opened by Phase 9 | **YES** — three read-only `SELECT`s for the §8 TESTCO count, no writes |

No unexpected mutation occurred.

**PHASE 9 ENDS WITH: production strategy untouched · BUG-1 untouched · BUG-2 untouched · no
tests executed · no database mutation · one narrowly scoped test-isolation guard implemented.**
