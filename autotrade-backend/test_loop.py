import asyncio
from db.database import get_db
from tasks.india_tasks import _india_trade_loop
import logging

logging.getLogger("utils.logger").setLevel(logging.DEBUG)

async def main():
    async for db in get_db():
        print("Running trade loop...")
        await _india_trade_loop()
        print("Done!")
        break

asyncio.run(main())
