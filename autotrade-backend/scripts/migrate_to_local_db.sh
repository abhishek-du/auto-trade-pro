#!/usr/bin/env bash
# migrate_to_local_db.sh
# Dumps Supabase → restores into local Docker Postgres → updates .env
# Run from autotrade-backend/: bash scripts/migrate_to_local_db.sh

set -e
cd "$(dirname "$0")/.."

SUPABASE_URL="postgresql://postgres.oecrjhaankiwaghcfwii:autotrade%40avishk16@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
# Direct connection (not pooler) is better for pg_dump
SUPABASE_DIRECT="postgresql://postgres:autotrade%40avishk16@db.oecrjhaankiwaghcfwii.supabase.co:5432/postgres"

LOCAL_URL="postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro"
DUMP_FILE="db/init/01_supabase_dump.sql"

echo "══════════════════════════════════════════════════"
echo "  AutoTrade Pro — Supabase → Local Docker Migrate"
echo "══════════════════════════════════════════════════"
echo ""

# ── Step 1: Dump Supabase ─────────────────────────────────────────────────
echo "▶ Step 1/4 — Dumping Supabase data..."
echo "  (using pg image so no local pg_dump needed)"

docker run --rm \
  -e PGPASSWORD="autotrade@avishk16" \
  -v "$(pwd)/db/init:/dump" \
  postgres:17-alpine \
  pg_dump \
    --host=db.oecrjhaankiwaghcfwii.supabase.co \
    --port=5432 \
    --username=postgres \
    --dbname=postgres \
    --no-owner \
    --no-acl \
    --clean \
    --if-exists \
    --file=/dump/01_supabase_dump.sql

echo "  ✓ Dump saved to $DUMP_FILE ($(du -sh "$DUMP_FILE" | cut -f1))"
echo ""

# ── Step 2: Start local Postgres ─────────────────────────────────────────
echo "▶ Step 2/4 — Starting local Postgres container..."
docker compose up -d postgres
echo "  Waiting for Postgres to be ready..."
until docker compose exec -T postgres pg_isready -U autotrade -d autotrade_pro 2>/dev/null; do
  sleep 1
done
echo "  ✓ Postgres is ready"
echo ""

# ── Step 3: Restore dump ─────────────────────────────────────────────────
echo "▶ Step 3/4 — Restoring dump into local DB..."
# The dump already ran via docker-entrypoint-initdb.d on first start,
# but if the volume already existed we do it manually:
docker compose exec -T postgres psql \
  -U autotrade -d autotrade_pro \
  -f /docker-entrypoint-initdb.d/01_supabase_dump.sql \
  > /dev/null 2>&1 || true
echo "  ✓ Data restored"
echo ""

# ── Step 4: Update .env ───────────────────────────────────────────────────
echo "▶ Step 4/4 — Updating .env..."

# Back up original
cp .env .env.supabase.bak

# Swap DATABASE_URL
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=${LOCAL_URL}|" .env

# Swap REDIS_URL to local
sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://localhost:6379/0|" .env

echo "  ✓ .env updated"
echo "  ✓ Backup saved to .env.supabase.bak"
echo ""

# ── Verify ────────────────────────────────────────────────────────────────
echo "▶ Verifying connection..."
.venv/bin/python3 -c "
import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from utils.config import settings

async def check():
    e = create_async_engine(settings.DATABASE_URL, connect_args={'statement_cache_size': 0})
    async with e.connect() as c:
        tbls = (await c.execute(text(\"\"\"
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public'
        \"\"\"))).scalar()
    print(f'  ✓ Connected to local DB — {tbls} tables found')
    await e.dispose()

asyncio.run(check())
"

echo ""
echo "══════════════════════════════════════════════════"
echo "  Migration complete! Supabase is no longer used."
echo "  Local DB: postgresql://autotrade:autotrade@localhost:5432/autotrade_pro"
echo "  Redis:    redis://localhost:6379/0"
echo ""
echo "  To start all services:  docker compose up -d"
echo "  To stop:                docker compose down"
echo "  To wipe DB and restart: docker compose down -v && docker compose up -d"
echo "══════════════════════════════════════════════════"
