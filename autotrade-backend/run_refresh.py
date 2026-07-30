import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from crawler.zerodha_market import refresh_instrument_tokens

logging.basicConfig(level=logging.INFO)

async def test():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        await refresh_instrument_tokens(session)
        await session.commit()

if __name__ == "__main__":
    asyncio.run(test())
