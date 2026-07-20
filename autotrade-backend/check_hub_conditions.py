import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.config import settings
from engine.agent.agent_loop import _is_trading_day, _is_market_hours

print(f"AGENT_ENABLED: {settings.AGENT_ENABLED}")
print(f"_is_trading_day(): {_is_trading_day()}")
print(f"_is_market_hours(): {_is_market_hours()}")
