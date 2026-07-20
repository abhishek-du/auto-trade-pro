import os
import sys
from dotenv import load_dotenv, set_key

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(env_path)

sys.path.insert(0, os.path.dirname(__file__))
from upstox_totp import UpstoxTOTP

def generate_and_save_upstox_token():
    # We use UPSTOX_USERNAME (mobile number) if provided, else fallback to UPSTOX_CLIENT_ID
    username = os.environ.get("UPSTOX_USERNAME") or os.environ.get("UPSTOX_CLIENT_ID")
    
    try:
        upx = UpstoxTOTP(
            username=username,
            password="dummy", # library requires this but it's often unused
            pin_code=os.environ.get("UPSTOX_PIN"),
            totp_secret=os.environ.get("UPSTOX_TOTP_SECRET"),
            client_id=os.environ.get("UPSTOX_API_KEY"),
            client_secret=os.environ.get("UPSTOX_API_SECRET"),
            redirect_uri=os.environ.get("UPSTOX_REDIRECT_URL", "http://localhost:8000/api/v1/upstox/callback"),
            debug=False
        )
        
        print(f"[*] Attempting Upstox Auto-Login for {username}...")
        response = upx.app_token.get_access_token()
        
        if response.success and response.data and response.data.access_token:
            token = response.data.access_token
            print("[+] Login Successful! Saving token to .env...")
            set_key(env_path, "UPSTOX_ACCESS_TOKEN", token)
            print("[+] UPSTOX_ACCESS_TOKEN updated successfully.")
            return True
        else:
            print("[-] Failed to generate token. Response:", response)
            return False
            
    except Exception as e:
        print("[-] Error during Upstox Auto-Login:")
        print(repr(e))
        return False

if __name__ == "__main__":
    generate_and_save_upstox_token()
