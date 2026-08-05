"""Ad-hoc schema migration: adds telegram_message_id to paper_trades and
agent_trades for alert threading (integrations/alerts/router.py Phase 3).

Run once manually: .venv/bin/python alter_notification_columns.py

Follows this repo's existing pattern for schema changes (see alter_trades.py)
rather than Alembic -- there's no committed alembic.ini, so `alembic upgrade`
isn't a working CLI flow here despite db/migrations/ existing.

Each ALTER runs in its own session/transaction -- a failure on one (e.g.
"column already exists") must not abort the other's transaction too.
"""
import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text


async def _alter(table: str) -> None:
    async with AsyncSessionLocal() as session:
        print(f"Altering table {table} to add telegram_message_id...")
        try:
            await session.execute(text(
                f"ALTER TABLE {table} ADD COLUMN telegram_message_id BIGINT;"
            ))
            await session.commit()
            print(f"Successfully added telegram_message_id to {table}!")
        except Exception as e:
            print(f"{table}: failed or already exists: {e}")


async def main():
    await _alter("paper_trades")
    await _alter("agent_trades")


asyncio.run(main())
