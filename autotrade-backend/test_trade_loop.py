import asyncio
from tasks.india_tasks import _india_trade_loop

async def main():
    await _india_trade_loop()
        
asyncio.run(main())
