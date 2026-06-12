#!/usr/bin/env python3
"""One-time Google OAuth authorisation for the trade journal.

Starts a local callback server on port 8085, writes the auth URL to
/tmp/google_auth_url.txt so an automated browser can open it, then
waits for Google to redirect back with the auth code.

After success, saves logs/google_token.pickle — all future syncs use
this token silently (auto-refreshed, no browser needed again).
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_auth_oauthlib.flow import InstalledAppFlow

SECRET = "/home/cis/Downloads/client_secret_917674594319-guj549fsholdao52nvi7aovdh3hr0r28.apps.googleusercontent.com.json"
TOKEN  = "logs/google_token.pickle"
PORT   = 8085
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

os.makedirs("logs", exist_ok=True)

flow = InstalledAppFlow.from_client_secrets_file(SECRET, SCOPES)
flow.redirect_uri = f"http://localhost:{PORT}/"

auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

# Write URL to temp file so the automation script can pick it up
with open("/tmp/google_auth_url.txt", "w") as f:
    f.write(auth_url)

print(f"Auth URL written to /tmp/google_auth_url.txt")
print(f"Waiting for OAuth callback on http://localhost:{PORT}/ ...")

# Run the local server — blocks until Google redirects back
creds = flow.run_local_server(port=PORT, open_browser=False)

with open(TOKEN, "wb") as f:
    pickle.dump(creds, f)

print(f"Token saved to {TOKEN}")
print(f"Valid: {creds.valid}  Has refresh_token: {bool(creds.refresh_token)}")
