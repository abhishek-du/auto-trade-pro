import os
import sys
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, "/home/cis/windows/auto-trade-pro/autotrade-backend/crawler")
from upstox_totp import UpstoxTOTP

try:
    upx = UpstoxTOTP(
        username=os.environ.get("UPSTOX_CLIENT_ID"), # Wait, using client_id instead of mobile number?
        password="dummy", # library requires this but might not use it?
        pin_code=os.environ.get("UPSTOX_PIN"),
        totp_secret=os.environ.get("UPSTOX_TOTP_SECRET"),
        client_id=os.environ.get("UPSTOX_API_KEY"),
        client_secret=os.environ.get("UPSTOX_API_SECRET"),
        redirect_uri="http://localhost:8000/api/v1/upstox/callback",
        debug=True
    )
    print("Init successful.")
    res = upx.app_token.get_access_token()
    print("Response:", res)
except Exception as e:
    print("Error:", repr(e))
