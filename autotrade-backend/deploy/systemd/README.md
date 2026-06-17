# Always-on Celery (systemd user services)

These run the Celery worker + beat as auto-restarting user services so the
trade engine, journal sync, and **daily Zerodha auto-login (08:00 IST)** keep
running across crashes and reboots.

## Install
```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/autotrade-celery-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now autotrade-celery-worker autotrade-celery-beat
# Survive logout / run at boot without an active login session:
sudo loginctl enable-linger "$USER"
```

## Key settings (why they matter)
- `Environment="PYTHONPATH=.../autotrade-backend"` — without this the forked
  workers can't `import crawler`/`scripts`, which silently broke the 08:00 IST
  auto-login ("No module named 'scripts'").
- `Restart=always` + `StartLimitIntervalSec=0` — revive on any crash.
- Logs: `/tmp/celery_worker.log`, `/tmp/celery_beat.log`.

## Check
```bash
systemctl --user status autotrade-celery-worker autotrade-celery-beat
grep zerodha_ensure_token /tmp/celery_worker.log   # boot token catch-up
```
