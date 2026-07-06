import asyncio
from utils.logger import logger
import logging
logger.setLevel(logging.INFO)

async def test_it():
    from engine.narrative_engine import refresh_narrative_cache, SECTOR_KEYWORD_MAP, _keyword_score
    print("=== TRIGGERING NARRATIVE REFRESH ===")
    
    # Run the refresh explicitly forcing it
    cache = await refresh_narrative_cache(force=True)
    
    print("\n=== FINAL CACHE OUTPUT ===")
    for sec, data in cache.items():
        print(f"🔥 {sec:12} | Boost: +{data['boost']} | Reason: {data['reason']}")
    
    if not cache:
        print("No sectors passed the filters (Fake News Trap might have caught them, or no news matched keywords).")

asyncio.run(test_it())
