import os
from dotenv import set_key

env_path = "/home/cis/windows/auto-trade-pro/autotrade-backend/.env"
set_key(env_path, "UPSTOX_REDIRECT_URL", "http://localhost:8000/api/v1/upstox/callback")
print("Fixed redirect URL in .env")
