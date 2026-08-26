# Test database configuration

`tests/conftest.py` carries a **fail-closed guard**: the suite refuses to start
unless it is pointed at an explicitly allowlisted test database.

## Why

`pytest` writes to whatever `DATABASE_URL` points at unless something stops it,
and it did: `simulation_logs` in production holds 144 rows for a fixture symbol
`TESTCO.NS`, written by `tests/test_integration_pipeline.py`. The chain was —

1. no test database, no `DATABASE_URL` override, no rollback fixture
2. `db/database.py` builds `AsyncSessionLocal` at **import time** from the
   production `settings.DATABASE_URL`
3. the autouse market-hours fixture patches `is_nse_market_open` to `True`,
   removing the router gate that would otherwise refuse an out-of-hours write
4. the test patches `news_discovery_engine.AsyncSessionLocal`
5. `engine/direct_news_strategy.py` re-imports `AsyncSessionLocal` from
   `db.database` at call time, so the patch never reaches it
6. `_log_intent_audit()` then `add()`s and `commit()`s to production

Steps 4–5 are the proximate defect. Step 1 is the cause — nothing stood between
the suite and production, so every future test would have to remember to patch
the right name. The guard removes that requirement.

## Setup

Create the database once (the app's own role has `CREATEDB`):

```sql
CREATE DATABASE autotrade_test;
```

Build its schema with the project's own `init_db()`, and **not** with
`Base.metadata.create_all` alone:

```python
from utils.config import settings
settings.DATABASE_URL = "<url with database='autotrade_test'>"   # BEFORE the import
import db.database as dbmod                                       # now binds to test
await dbmod.init_db()
```

`create_all` is **not sufficient**. This project manages its schema two ways —
Alembic *and* a ~60-statement inline DDL block in `db/database.py::init_db()`
(CLAUDE.md §7). Several objects exist only in that block, including

```
uq_news_items_headline_day
  UNIQUE (md5(headline), (COALESCE(published_at, crawled_at))::date)
  WHERE crawled_at >= '2026-08-21'
```

which is the constraint the news_id duplicate path depends on. A test database
built from ORM metadata alone silently accepts duplicate headlines, so
`test_news_id_traceability.py` fails against it — which is how this was found.

Never run Alembic or `init_db()` against production from a test setup script.

Then create `autotrade-backend/.env.test` (gitignored via the root
`.gitignore`'s `.env.*` rule):

```
TEST_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/autotrade_test
ALLOWED_TEST_DB_HOSTS=localhost
ALLOWED_TEST_DB_NAMES=autotrade_test
```

Environment variables of the same names take precedence over the file. Either
source is fine; supplying neither aborts the suite.

## What the guard does

- reads only `TEST_DATABASE_URL`, `ALLOWED_TEST_DB_HOSTS`,
  `ALLOWED_TEST_DB_NAMES` — **never** `DATABASE_URL`
- parses with `sqlalchemy.make_url`; **opens no connection**
- requires host **and** database name to be present, then requires each to be
  in its allowlist — an **allowlist**, because a denylist of known production
  hosts silently permits every host nobody thought to add
- compares host + port + database against `DATABASE_URL` and refuses if they
  match, so the same database reached with a different password or driver is
  still caught
- only then sets `settings.DATABASE_URL`, which is the single value both
  session factories derive from — `db/database.py:26` (import time) and
  `tasks/_db.py:38` (`celery_session`, on every call)
- redacts passwords in every message

It runs at **conftest import time**, not as a fixture: pytest imports
`conftest.py` before collecting test modules, and those modules import
`db.database` during collection. A session-scoped autouse fixture would run
after collection — too late.

## Failure

```
Tests aborted: no explicitly allowlisted test database is configured.
```

Do not work around this by exporting `DATABASE_URL`. If the message appears,
the test database is not configured — configure it.
