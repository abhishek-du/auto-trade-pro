#!/bin/bash
# Auto-refresh Upstox Token and restart backend API

cd /home/cis/windows/auto-trade-pro/autotrade-backend

echo "[$(date)] Starting Upstox Auto-Refresh..." >> upstox_cron.log

# 1. Run the auth script to fetch and save new token to .env
.venv/bin/python crawler/upstox_auth.py >> upstox_cron.log 2>&1

# 2. Restart the FastAPI server to load the new token from .env
systemctl --user restart autotrade-uvicorn.service >> upstox_cron.log 2>&1

echo "[$(date)] Auto-Refresh Complete." >> upstox_cron.log
