#!/usr/bin/env bash
# Polls Supabase pooler every 30 s until connected, then runs the app.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

source .venv/bin/activate
set -a; source <(grep -v '^#' .env | grep -v '^\s*$'); set +a

echo "[$(date '+%H:%M:%S')] Waiting for Supabase to accept connections..."
echo "  Checking every 30 seconds — press Ctrl+C to stop"
echo ""

while true; do
  RESULT=$(python -c "
import asyncio, asyncpg, os
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])

async def check():
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=str(url.host), port=int(url.port),
                user=str(url.username), password=str(url.password),
                database=str(url.database), statement_cache_size=0,
            ), timeout=10)
        row = await conn.fetchrow('SELECT balance FROM virtual_wallet LIMIT 1')
        await conn.close()
        print(f'READY:{row[\"balance\"]:.2f}')
    except Exception as e:
        print(f'WAIT:{str(e)[:80]}')

asyncio.run(check())
" 2>/dev/null)

  TS="[$(date '+%H:%M:%S')]"

  if echo "$RESULT" | grep -q "^READY:"; then
    BALANCE=$(echo "$RESULT" | cut -d: -f2)
    echo ""
    echo "╔══════════════════════════════════════════════════╗"
    echo "║  $TS Supabase READY — balance: \$$BALANCE"
    echo "║  Starting AutoTrade Pro...                       ║"
    echo "╚══════════════════════════════════════════════════╝"
    echo ""
    exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload
  else
    echo "$TS Still waiting... ($RESULT)"
    sleep 30
  fi
done
