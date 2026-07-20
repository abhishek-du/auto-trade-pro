import asyncio
import logging
import sys
from loguru import logger
from unittest.mock import patch, AsyncMock

# Add current path
sys.path.append(".")

from news_discovery_engine import process_ticker

async def test_knowledge_graph():
    logger.info("🧪 Starting Knowledge Graph Test for TATAMOTORS")
    
    # We mock llm_tooluse_candidate to guarantee a TAKE verdict
    mock_result = {
        "verdict": "TAKE",
        "confidence": 85,
        "bull": "Massive positive news for Tata Motors, 1000 EV orders secured.",
        "bear": "Slight raw material cost increases."
    }
    
    with patch("news_discovery_engine.llm_tooluse_candidate", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_result
        
        with patch("news_discovery_engine._execute_news_trade", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = True 
            
            # Fire the test!
            await process_ticker("TATAMOTORS.NS", "BUY", "Tata Motors wins massive EV tender", "Detailed summary")
            
            # Check what was executed
            logger.info("✅ Test Complete. Executions captured:")
            for call in mock_exec.call_args_list:
                args, kwargs = call
                ticker = args[0]
                side = args[1]
                headline = args[2]
                logger.info(f"   -> Executed: {ticker} | Side: {side} | Headline: {headline}")

if __name__ == "__main__":
    asyncio.run(test_knowledge_graph())
