import asyncio
from tasks.india_tasks import _india_price_scan

async def test():
    await _india_price_scan()

asyncio.run(test())
