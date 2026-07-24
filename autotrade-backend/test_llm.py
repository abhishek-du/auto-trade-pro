from utils.llm import call_llm_chat
import asyncio

async def test():
    print("Testing LLM...")
    res = await call_llm_chat("Analyze this text: 'L&T wins 500cr order'. Output JSON with category.", json_output=True)
    print(res)

asyncio.run(test())
