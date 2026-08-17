# systemd units

The five long-running services plus the daily Zerodha token timer. These are
copies of what is actually running under `~/.config/systemd/user/` — tracked
here because the tuning in them is load-bearing and was previously untracked,
so a machine rebuild would have silently lost it.

They are **user** units (`systemctl --user`), not system units. No root needed.

| Unit | What it does |
|---|---|
| `autotrade-uvicorn.service` | FastAPI API on `127.0.0.1:8000` |
| `autotrade-celery-worker.service` | Celery worker — trade loop, crawls, price/sector publisher |
| `autotrade-celery-beat.service` | Celery beat scheduler |
| `autotrade-news-engine.service` | 24/7 news-first discovery engine |
| `autotrade-zerodha-refresh.service` + `.timer` | Daily Zerodha token refresh, 08:00 IST |

## Install

```bash
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now autotrade-uvicorn autotrade-celery-worker \
                              autotrade-celery-beat autotrade-news-engine \
                              autotrade-zerodha-refresh.timer
```

Postgres comes up separately, before these:

```bash
sudo docker compose -f autotrade-backend/docker-compose.yml up -d postgres
```

## Paths are hardcoded

Every unit refers to `/home/cis/windows/auto-trade-pro/...` and that repo's
`.venv`. On a different checkout or user, rewrite them first:

```bash
sed -i "s|/home/cis/windows/auto-trade-pro|$PWD|g" ~/.config/systemd/user/autotrade-*.service
```

The venv must be Python 3.11 — the host `python3` is 3.14 and its ABI is
incompatible with the venv's compiled extensions (`pydantic_core` and friends),
so the units invoke `.venv/bin/python` explicitly rather than `python3`.

## Settings that are deliberate, not defaults

**`autotrade-uvicorn.service`**
- `MemoryHigh=2G` / `MemoryMax=3G` — the service was OOM-killed twice in one
  week (6.0G peak on 14-Aug, 5.6G on 17-Aug) **by the kernel**, which picks its
  victim across the whole box, so an unrelated process could have died instead.
  The cgroup cap makes an OOM kill only this unit, and `Restart=always` brings
  it back. Steady state is ~270M, so this is headroom, not a tight fit. The
  growth itself is still un-diagnosed — it is activity-driven and was not
  reproducible with the market closed.
- `TimeoutStopSec=30` (was 8) — shutdown must close connections, cancel the
  lifespan background tasks and dispose the DB engine. 8s was routinely not
  enough, so every stop hit the timeout and was SIGKILLed, meaning the shutdown
  handler never ran at all.
- `--timeout-graceful-shutdown 12` on the ExecStart, for the same reason.

**`autotrade-celery-worker.service`**
- `--concurrency=2` (was 4) — 4 workers saturated all 4 cores and starved the
  API. Note this did **not** fix the API stalls on its own (those turned out to
  be sequential Upstox lookups and inline sector computation inside the API
  process); it is kept because 4 workers on a 4-core box shared with uvicorn,
  the news engine and a browser is simply over-subscribed.
- Both worker and beat run under `watchmedo auto-restart --pattern="*.py"`, so
  a `.py` edit reloads them automatically. **`.env` is not watched** — a
  credential change needs a manual `systemctl --user restart`. That gap cost
  ~5 days of LLM downtime in August 2026: a valid Bedrock key sat in `.env`
  from 12-Aug and no running process ever re-read it.

## Logs

`StandardOutput`/`StandardError` append to `/tmp/*.log`, not journald, so
`journalctl` shows only unit lifecycle events:

- `/tmp/uvicorn.log`, `/tmp/celery_worker.log`, `/tmp/celery_beat.log`,
  `/tmp/news-engine.log`, `/tmp/zerodha_refresh.log`

None of them rotate. `/tmp/celery_worker.log` reached **2.6 GB** — worth a
logrotate rule or a `--loglevel` trim.
