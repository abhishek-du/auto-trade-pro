import asyncio
from db.database import AsyncSessionLocal
from api.portfolio_tracker import sync_upstox
from fastapi import Request
import traceback

async def main():
    try:
        async with AsyncSessionLocal() as session:
            class DummyRequest:
                pass
            req = DummyRequest()
            req.state = type('State', (), {'user_id': 1})()
            res = await sync_upstox(session=session)
            print("Success:", res)
    except Exception as e:
        print("Error:")
        traceback.print_exc()

asyncio.run(main())
