import os
from dotenv import set_key
env_path = ".env"
set_key(env_path, "UPSTOX_REDIRECT_URL", "http://127.0.0.1:8000/api/v1/upstox/callback")
print("Environment updated to http 127.0.0.1")
