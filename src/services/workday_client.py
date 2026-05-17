import os
import sys
import requests
import json
from dotenv import load_dotenv

load_dotenv()

class WorkdayClient:
    def __init__(self, api_token=None, base_url=None):
        print("Initializing Workday Client...", file=sys.stderr)

        # Load from .env unless explicitly overridden via arguments
        self.base_url = base_url or os.getenv("WORKDAY_BASE_URL")
        self.token = api_token or os.getenv("WORKDAY_API_TOKEN")
        self.cache = {}  # Simple in-memory cache
        
        if not self.base_url:
            print("WARNING: WORKDAY_BASE_URL is missing from configuration environment!", file=sys.stderr)

        if not self.token:
            print("WARNING: WORKDAY_API_TOKEN is missing from configuration environment!", file=sys.stderr)

    def execute(self, method, full_path, path_params=None, query_params=None):
        # Fail early if executed without endpoints or tokens configured
        if not self.base_url or not self.token:
            raise ValueError(
                f"Cannot execute API request. Configuration missing! "
                f"Base URL configured: {'Yes' if self.base_url else 'No'}, "
                f"Token configured: {'Yes' if self.token else 'No'}"
            )

        # 1. Fix double-path structures gracefully
        if "api/common/v1" in self.base_url and full_path.startswith("/api/common/v1"):
            full_path = full_path.replace("/api/common/v1", "", 1)

        # 2. Inject parameters safely
        if path_params:
            for key, value in path_params.items():
                full_path = full_path.replace(f"{{{key}}}", str(value))
                full_path = full_path.replace(f"%7B{key}%7D", str(value))

        # SAFETY CHECK — prevent invalid raw template API execution attempts
        if "{" in full_path or "%7B" in full_path:
            raise ValueError(f"Missing required path parameter variables for path location: {full_path}")

        # 3. Construct Full URL Properly
        url = f"{self.base_url.rstrip('/')}/{full_path.lstrip('/')}"
        
        # --- CACHE CHECK ---
        cache_key = f"{method.upper()}:{url}:{json.dumps(query_params, sort_keys=True)}"
        if method.upper() == "GET" and cache_key in self.cache:
            print(f"[Cache] Returning CACHED response for: {url}", file=sys.stderr)
            return self.cache[cache_key]
        
        # 4. Setup Authorization Headers
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

        print(f"Executing {method.upper()} request to: {url}", file=sys.stderr)

        # 5. HTTP Call execution block
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=query_params,
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Store in cache if successful GET retrieval operation
            if method.upper() == "GET":
                self.cache[cache_key] = data
                
            return data
            
        except requests.exceptions.RequestException as e:
            print(f"Workday API Error: {e}", file=sys.stderr)
            error_details = response.text if 'response' in locals() and response is not None else "No response body."
            return {"error": str(e), "details": error_details}


if __name__ == "__main__":
    client = WorkdayClient()
    print("Workday Client is ready.", file=sys.stderr)