import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import KiteSession

async def check_kite_session():
    async with AsyncSessionLocal() as session:
        query = select(KiteSession)
        res = await session.execute(query)
        sessions = res.scalars().all()
        print(f"Total KiteSessions: {len(sessions)}")
        for s in sessions:
            print(f"- User: {s.user_id}, Active: {s.is_active}, Updated: {s.updated_at}, Token: {s.access_token[:5] if s.access_token else 'None'}")

if __name__ == "__main__":
    asyncio.run(check_kite_session())
