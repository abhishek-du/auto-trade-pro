#!/bin/bash
# AutoTrade Pro — PAPER TRADING MODE startup script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_SITE="$SCRIPT_DIR/.venv/lib/python3.10/site-packages"

if [ ! -d "$VENV_SITE" ]; then
    echo "ERROR: Virtual environment not found at $VENV_SITE"
    echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Kill any stale processes from a previous run so port 8000 and the
# celerybeat-schedule lock are always free before we start.
echo "[startup] Stopping any previous AutoTrade Pro processes..."
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "celery.*autotrade_pro" 2>/dev/null || true
pkill -f "celery.*tasks.celery_app" 2>/dev/null || true
sleep 1
rm -f "$SCRIPT_DIR/celerybeat-schedule" "$SCRIPT_DIR/celerybeat-schedule.db" 2>/dev/null || true

export PYTHONPATH="$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "  AutoTrade Pro — PAPER TRADING MODE"
echo "  ⚠  FAKE/VIRTUAL CURRENCY ONLY — No real money is used"
echo "============================================================"
echo "  Python path  : $(python3 -c 'import sys; print(sys.executable)')"
echo "  Site-packages: $VENV_SITE"
echo "============================================================"

# Start Celery worker (background)
echo "[celery] Starting worker..."
python3 -m celery -A tasks.celery_app worker --loglevel=info --concurrency=2 &
CELERY_WORKER_PID=$!

# Start Celery beat (background)
echo "[celery] Starting beat scheduler..."
python3 -m celery -A tasks.celery_app beat --loglevel=info &
CELERY_BEAT_PID=$!

# Trap Ctrl+C — kill background processes cleanly
trap "echo ''; echo 'Shutting down...'; kill $CELERY_WORKER_PID $CELERY_BEAT_PID 2>/dev/null; exit 0" INT TERM

echo "[uvicorn] Starting API server on http://0.0.0.0:8000 ..."
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
