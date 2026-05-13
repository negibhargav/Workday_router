import os,sys
import requests
import json
from dotenv import load_dotenv

load_dotenv()

class WorkdayClient:
    def __init__(self, api_token=None, base_url=None):
        print("Initializing Workday Client...", file=sys.stderr)

        # Load from .env unless overridden
        self.base_url = base_url or os.getenv("WORKDAY_BASE_URL")
        self.token = api_token or os.getenv("WORKDAY_API_TOKEN")
        self.cache = {} # Simple in-memory cache
        
        if not self.base_url:
            print("WARNING: WORKDAY_BASE_URL is missing from .env", file=sys.stderr)

        if not self.token:
            print("WARNING: WORKDAY_API_TOKEN is missing from .env", file=sys.stderr)

    def execute(self, method, full_path, path_params=None, query_params=None):
        # 1. Fix the double-path issue
        if "api/common/v1" in self.base_url and full_path.startswith("/api/common/v1"):
            full_path = full_path.replace("/api/common/v1", "", 1)

        # 2. Inject parameters
        if path_params:
            for key, value in path_params.items():
                full_path = full_path.replace(f"{{{key}}}", str(value))
                full_path = full_path.replace(f"%7B{key}%7D", str(value))

        # SAFETY CHECK — prevent invalid API calls
        if "{" in full_path or "%7B" in full_path:
            raise ValueError(f"Missing required parameters for path: {full_path}")

        # -----------------------------
        # 3. Construct Full URL Properly
        # -----------------------------
        url = f"{self.base_url.rstrip('/')}/{full_path.lstrip('/')}"
        
        # --- CACHE CHECK ---
        cache_key = f"{method}:{url}:{json.dumps(query_params, sort_keys=True)}"
        if method.upper() == "GET" and cache_key in self.cache:
            print(f"Returning CACHED response for: {url}")
            return self.cache[cache_key]
        
        # 3. Setup Authentication
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

        print(f"Executing {method} request to: {url}", file=sys.stderr)

        # -----------------------------
        # 5. HTTP Call
        # -----------------------------
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=query_params,
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Store in cache if GET
            if method.upper() == "GET":
                self.cache[cache_key] = data
                
            return data
            
        except requests.exceptions.RequestException as e:
            print(f"Workday API Error: {e}")
            error_details = response.text if 'response' in locals() and response is not None else "No response body."
            return {"error": str(e), "details": error_details}

if __name__ == "__main__":
    client = WorkdayClient()
    print("Workday Client is ready.", file=sys.stderr)