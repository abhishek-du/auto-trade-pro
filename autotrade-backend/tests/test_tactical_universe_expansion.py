import pytest
from engine.tactical_data_fetcher import get_universe
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_f1_universe_expansion():
    mock_session = AsyncMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = ["RELIANCE", "BALRAMCHIN"]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result
    
    universe = await get_universe(mock_session, 500)
    
    assert len(universe) > 0
    assert "BALRAMCHIN" in universe
    mock_session.execute.assert_called_once()
