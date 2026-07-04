import requests
import base64
import os
from dotenv import load_dotenv

load_dotenv()

token_url     = os.getenv("WORKDAY_TOKEN_URL")
client_id     = os.getenv("WORKDAY_CLIENT_ID")
client_secret = os.getenv("WORKDAY_CLIENT_SECRET")

print("=== Workday OAuth Token Test ===")
print(f"Token URL    : {token_url}")
print(f"Client ID    : {client_id}")
print(f"Secret (len) : {len(client_secret) if client_secret else 'MISSING'}")
print()

credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

resp = requests.post(
    token_url,
    data={"grant_type": "client_credentials"},
    headers={
        "Content-Type":  "application/x-www-form-urlencoded",
        "Authorization": f"Basic {credentials}",
    },
    timeout=15,
)

print(f"Status  : {resp.status_code}")
print(f"Response: {resp.text[:500]}")

if resp.ok:
    token = resp.json().get("access_token", "")
    print(f"\n✅ SUCCESS — token (first 40 chars): {token[:40]}...")
else:
    print("\n❌ FAILED — see response above")
