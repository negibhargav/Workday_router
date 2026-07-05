#!/usr/bin/env python3
"""
diagnose_direct_reports.py
Hits the Workday directReports endpoint directly and inspects the raw
response, bypassing the router/executor/extraction logic entirely.
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

# Load environment variables automatically from .env
load_dotenv()

# ---- CONFIG: Pull from env or use defaults ----
BASE_URL = os.environ.get(
    "WORKDAY_BASE_URL",
    "https://wcpdev-services1.wd101.myworkday.com/api/common/v1/jll_wcpdev1"
).rstrip("/")

RAW_WORKER_ID = os.environ.get("WORKDAY_WORKER_ID", "me")
TOKEN = os.environ.get("WORKDAY_API_TOKEN")

if not TOKEN:
    raise SystemExit("❌ ERROR: WORKDAY_API_TOKEN is not set in your environment or .env file.")


# ---- HELPER: Apply universal Workday ID formatting ----
def format_workday_id(raw_id: str) -> str:
    """Formats raw IDs into Workday's expected path syntax (ID= vs Worker_ID=)."""
    bare_id = str(raw_id).removeprefix("Worker_ID=").removeprefix("ID=").strip()
    
    if bare_id.lower() == "me" or not bare_id:
        return "me"
    # If the ID is a 32-character hex WID string, use ID=
    elif len(bare_id) == 32 and all(c in "0123456789abcdefABCDEF" for c in bare_id):
        return f"ID={bare_id}"
    # Otherwise, treat it as a standard employee badge number
    else:
        return f"Worker_ID={bare_id}"


# 1. Format the target ID and construct the URL
WORKER_ID = format_workday_id(RAW_WORKER_ID)
url = f"{BASE_URL}/workers/{WORKER_ID}/directReports"

print("=" * 60)
print(f"🎯 DIAGNOSING WORKDAY DIRECT REPORTS")
print(f"Target Input ID : '{RAW_WORKER_ID}' -> Formatted: '{WORKER_ID}'")
print(f"Executing GET   : {url}")
print("=" * 60)

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json"
}

# ---- 2. Execute Request & Intercept Error Bodies ----
try:
    resp = requests.get(url, headers=headers)
    print(f"HTTP Status: {resp.status_code} ({resp.reason})")
    resp.raise_for_status()
    raw = resp.json()
except requests.exceptions.HTTPError as e:
    print("\n❌ WORKDAY API REQUEST FAILED!")
    try:
        error_payload = resp.json()
        print("Workday Error Details:", json.dumps(error_payload, indent=2))
    except Exception:
        print("Raw Error Text:", resp.text)
    sys.exit(1)

# ---- 3. Inspect the raw payload shape ----
total = raw.get("total", 0)
data = raw.get("data", [])

print("\n--- RAW RESPONSE SUMMARY ---")
print(f"Total field reported by API : {total}")
print(f"Actual len(data) returned   : {len(data)}")

print("\n--- EACH ENTRY IN data[] ---")
if not data:
    print("⚠️  No direct reports found for this worker.")
else:
    for i, entry in enumerate(data):
        # Workday typically returns the full name in 'descriptor'
        name = entry.get("descriptor") or entry.get("businessTitle") or "UNKNOWN_NAME"
        worker_id = entry.get("id", "UNKNOWN_ID")
        print(f"  [{i+1}] WID: {worker_id} | Name: {name}")

# ---- 4. Simulate Extractor Logic ----
def buggy_single_extractor(payload):
    """Mimics an extractor that flattens a list by grabbing only the first item."""
    d = payload.get("data", [])
    first = d[0] if d else {}
    return {"id": first.get("id"), "name": first.get("descriptor")}

def correct_list_extractor(payload):
    """Mimics what your Executor should pass down to the Synthesizer."""
    d = payload.get("data", [])
    return [{"id": e.get("id"), "name": e.get("descriptor")} for e in d]

print("\n--- WHAT A BUGGY SINGLE-OBJECT EXTRACTOR PRODUCES ---")
print(json.dumps(buggy_single_extractor(raw), indent=2))

print("\n--- WHAT THE CORRECT LIST EXTRACTOR PRODUCES ---")
print(json.dumps(correct_list_extractor(raw), indent=2))

# ---- 5. Explicit Pagination Test ----
print("\n--- TESTING WITH EXPLICIT limit=100 ---")
resp2 = requests.get(url, headers=headers, params={"limit": 100, "offset": 0})
if resp2.status_code == 200:
    data2 = resp2.json().get("data", [])
    print(f"len(data) with limit=100 : {len(data2)}")
    if len(data2) > len(data):
        print("⚠️  NOTICE: Pagination limit was capping your default results!")
else:
    print(f"Failed explicit limit test: HTTP {resp2.status_code}")
print("=" * 60)