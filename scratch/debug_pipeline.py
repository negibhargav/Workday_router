"""
debug_pipeline.py -- Auto-fetches OAuth token at startup, then runs the full pipeline.
Usage: uv run python scratch/debug_pipeline.py "your query here"
"""
import json
import os
import sys
import base64
import logging
import requests

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("pinecone").setLevel(logging.ERROR)

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Step 0: Fetch OAuth token ────────────────────────────────────────────────

def fetch_token() -> str:
    token_url     = os.getenv("WORKDAY_TOKEN_URL")
    client_id     = os.getenv("WORKDAY_CLIENT_ID")
    client_secret = os.getenv("WORKDAY_CLIENT_SECRET")

    if not (token_url and client_id and client_secret):
        print("[Auth] ERROR: WORKDAY_TOKEN_URL / CLIENT_ID / CLIENT_SECRET missing in .env")
        sys.exit(1)

    print("[Auth] Fetching OAuth token ...")
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

    if not resp.ok:
        print(f"[Auth] FAILED {resp.status_code}: {resp.text}")
        print()
        print("Fix: regenerate the client secret in Workday -> API Clients for Integrations")
        print("     and update WORKDAY_CLIENT_SECRET in .env")
        sys.exit(1)

    token = resp.json()["access_token"]
    expires_in = resp.json().get("expires_in", "?")
    print(f"[Auth] Token fetched OK (expires in {expires_in}s). First 40 chars: {token[:40]}...")
    return token


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_debug(query: str, token: str):
    from src.brain.planner import Planner
    from src.brain.executor import Executor
    from src.services.workday_client import WorkdayClient

    print(f"\n{'='*60}")
    print(f"QUERY: {query}")
    print(f"{'='*60}\n")

    model = os.getenv("BRAIN_MODEL", "gpt-4o-mini")

    # PHASE 1: Plan
    print("[ PHASE 1: PLANNER ]")
    planner = Planner(model=model)
    plan = planner.plan(query)
    print(f"\nGoal : {plan['goal']}")
    print(f"Steps: {len(plan['steps'])}")
    for s in plan["steps"]:
        print(f"  Step {s['id']}:")
        print(f"    intent        : {s['intent']}")
        print(f"    api_hint      : {s['api_hint']}")
        print(f"    depends_on    : {s['depends_on']}")
        print(f"    param_map     : {s['param_map']}")
        print(f"    extract_fields: {s['extract_fields']}")

    # PHASE 2: Execute with injected token
    print(f"\n[ PHASE 2: EXECUTOR ]")

    # Inject the fresh token directly — bypasses OAuth entirely for this run
    executor = Executor(model=model)
    executor.client._token = token
    executor.client._token_expires_at = float("inf")   # never expire during this run

    context = {}
    for step in plan["steps"]:
        step_key = f"step_{step['id']}"
        print(f"\n  -- Step {step['id']}: {step['intent']}")

        resolved = executor._resolve_params(step.get("param_map"), context)
        print(f"    Resolved params : {resolved}")

        route = executor.dispatcher.route_query(step["api_hint"])
        print(f"    RAG match       : {route.get('method')} {route.get('full_path')} (score={route.get('confidence_score')})")

        result = executor._run_step(step, context)
        context[step_key] = result

        print(f"    API called      : {result.get('api_called')}")
        print(f"    Extracted fields: {json.dumps(result.get('extracted', {}))}")

        if result.get("error"):
            print(f"    !! ERROR        : {result['error']}")
        else:
            raw = result.get("raw_response", "")
            preview = raw[:400] if isinstance(raw, str) else str(raw)[:400]
            print(f"    Raw response (first 400 chars):")
            print(f"      {preview}")

    print(f"\n{'='*60}")
    print("FINAL CONTEXT:")
    for k, v in context.items():
        print(f"  {k}: extracted={v.get('extracted')}, error={v.get('error')}")


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "who reports to worker Joy Banks"
    token = fetch_token()       # fetch token FIRST, fail fast if creds are bad
    run_debug(query, token)     # inject token into executor, run pipeline
