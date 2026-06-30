#!/bin/bash
# AutoTrade Pro — PAPER TRADING MODE startup script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_SITE="$SCRIPT_DIR/.venv/lib/python3.11/site-packages"
# Always use the venv's OWN interpreter. The system python3 may be a newer
# version (e.g. 3.14) whose ABI is incompatible with the 3.11-compiled
# extensions in the venv (pydantic_core, etc.) — invoking bare `python3` then
# loads those .so files under the wrong interpreter and crashes on import.
PY="$SCRIPT_DIR/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "ERROR: venv interpreter not found at $PY"
    echo "Run: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Both uvicorn and celery are managed by always-on systemd user services.
# This script just restarts them cleanly if needed.
SYSTEMD_CELERY=0
if systemctl --user is-active --quiet autotrade-celery-worker 2>/dev/null; then
    SYSTEMD_CELERY=1
fi
SYSTEMD_UVICORN=0
if systemctl --user is-active --quiet autotrade-uvicorn 2>/dev/null; then
    SYSTEMD_UVICORN=1
fi

echo "[startup] Stopping any previous AutoTrade Pro processes..."
pkill -f "uvicorn main:app" 2>/dev/null || true
if [ "$SYSTEMD_CELERY" -eq 0 ]; then
    pkill -f "celery.*tasks.celery_app" 2>/dev/null || true
    sleep 1
    rm -f "$SCRIPT_DIR/celerybeat-schedule" "$SCRIPT_DIR/celerybeat-schedule.db" 2>/dev/null || true
fi

cd "$SCRIPT_DIR"

echo "============================================================"
echo "  AutoTrade Pro — PAPER TRADING MODE"
echo "  ⚠  FAKE/VIRTUAL CURRENCY ONLY — No real money is used"
echo "============================================================"
echo "  Python path  : $("$PY" -c 'import sys; print(sys.executable)')"
echo "  Python version: $("$PY" --version 2>&1)"
echo "  Site-packages: $VENV_SITE"
echo "============================================================"

if [ "$SYSTEMD_CELERY" -eq 1 ]; then
    echo "[celery] Managed by systemd — not starting a duplicate."
    CELERY_WORKER_PID=""
    CELERY_BEAT_PID=""
else
    echo "[celery] Starting worker..."
    "$PY" -m celery -A tasks.celery_app worker --loglevel=info --concurrency=2 &
    CELERY_WORKER_PID=$!
    echo "[celery] Starting beat scheduler..."
    "$PY" -m celery -A tasks.celery_app beat --loglevel=info &
    CELERY_BEAT_PID=$!
fi

if [ "$SYSTEMD_UVICORN" -eq 1 ]; then
    echo "[uvicorn] Managed by systemd — restarting via systemctl..."
    systemctl --user restart autotrade-uvicorn
    echo "[uvicorn] Started. Logs: journalctl --user -u autotrade-uvicorn -f"
    trap "echo ''; echo 'Shutting down celery...'; kill $CELERY_WORKER_PID $CELERY_BEAT_PID 2>/dev/null; exit 0" INT TERM
    wait
else
    trap "echo ''; echo 'Shutting down...'; kill $CELERY_WORKER_PID $CELERY_BEAT_PID 2>/dev/null; exit 0" INT TERM
    echo "[uvicorn] Starting API server on http://0.0.0.0:8000 ..."
    "$PY" -m uvicorn main:app --host 0.0.0.0 --port 8000
fi
