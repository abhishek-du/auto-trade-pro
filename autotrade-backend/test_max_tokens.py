import asyncio
from utils.llm import call_mantle_chat
import logging

logging.basicConfig(level=logging.INFO)

async def test_tokens(max_t):
    try:
        print(f"Testing max_tokens={max_t}...")
        res = await call_mantle_chat(
            [{"role": "user", "content": "Just say 'Hello'."}],
            max_tokens=max_t,
            temperature=0.1
        )
        print(f"Success at {max_t}! Response: {res}")
        return True
    except Exception as e:
        print(f"Failed at {max_t}: {e}")
        return False

async def main():
    limits = [4096, 8192, 16384, 32768, 128000]
    for limit in limits:
        success = await test_tokens(limit)
        if not success:
            print(f"Model limit is likely below {limit}")
            break

if __name__ == "__main__":
    asyncio.run(main())
