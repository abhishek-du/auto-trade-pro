import asyncio
async def test_it():
    from engine.narrative_engine import refresh_narrative_cache
    print("=== TRIGGERING NARRATIVE REFRESH ===")
    cache = await refresh_narrative_cache(force=True)
    print("\n=== FINAL CACHE OUTPUT ===")
    for sec, data in cache.items():
        boost = data["boost"]
        reason = data["reason"]
        print(f"🔥 {sec:12} | Boost: +{boost} | Reason: {reason}")
    if not cache:
        print("No sectors passed the filters (Fake News Trap might have caught them).")
asyncio.run(test_it())

