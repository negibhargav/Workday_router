import os
import base64
import sys
import time
import requests
import json
from dotenv import load_dotenv

load_dotenv()


class WorkdayClient:
    def __init__(self, api_token=None, base_url=None):
        print("Initializing Workday Client...", file=sys.stderr)

        self.base_url = base_url or os.getenv("WORKDAY_BASE_URL")
        self.cache = {}  # Simple in-memory GET cache

        # --- OAuth 2.0 client credentials (for auto-refresh) ---
        self.token_url     = os.getenv("WORKDAY_TOKEN_URL")
        self.client_id     = os.getenv("WORKDAY_CLIENT_ID")
        self.client_secret = os.getenv("WORKDAY_CLIENT_SECRET")

        # Token state — seed from env if provided; treat as expired so we validate on first use
        self._token: str | None = api_token or os.getenv("WORKDAY_API_TOKEN")
        self._token_expires_at: float = 0  # 0 = unknown / treat as expired

        if not self.base_url:
            print("WARNING: WORKDAY_BASE_URL is missing!", file=sys.stderr)

        # Warn only when BOTH a static token AND OAuth creds are absent
        has_static_token = bool(self._token)
        has_oauth_creds  = bool(self.token_url and self.client_id and self.client_secret)
        if not has_static_token and not has_oauth_creds:
            print(
                "WARNING: No auth configured. Set either WORKDAY_API_TOKEN "
                "or WORKDAY_TOKEN_URL + WORKDAY_CLIENT_ID + WORKDAY_CLIENT_SECRET.",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _is_token_expiring(self) -> bool:
        """Return True if the token is absent or expires within 60 seconds."""
        return not self._token or time.time() >= (self._token_expires_at - 60)

    def _fetch_new_token(self) -> None:
        """Request a fresh access token via the client_credentials grant.
        
        Workday requires client credentials as HTTP Basic Auth,
        NOT as body parameters.
        """
        if not (self.token_url and self.client_id and self.client_secret):
            raise RuntimeError(
                "Cannot auto-refresh: WORKDAY_TOKEN_URL, WORKDAY_CLIENT_ID, "
                "and WORKDAY_CLIENT_SECRET must all be set in .env."
            )

        print("[OAuth] Fetching new access token...", file=sys.stderr)

        # Workday expects Basic Auth: base64(client_id:client_secret)
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        resp = requests.post(
            self.token_url,
            data={"grant_type": "client_credentials"},
            headers={
                "Content-Type":  "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            timeout=15,
        )

        if not resp.ok:
            print(
                f"[OAuth] Token request failed: {resp.status_code} — {resp.text}",
                file=sys.stderr,
            )
        resp.raise_for_status()
        payload = resp.json()

        self._token = payload["access_token"]
        # Fallback to 4 hours if expires_in is absent
        expires_in = int(payload.get("expires_in", 14400))
        self._token_expires_at = time.time() + expires_in
        print(f"[OAuth] Token refreshed. Valid for {expires_in}s.", file=sys.stderr)

    def _get_token(self) -> str:
        """Return a valid bearer token, refreshing proactively if needed."""
        if self._is_token_expiring():
            self._fetch_new_token()
        return self._token

    # ------------------------------------------------------------------
    # Core HTTP executor
    # ------------------------------------------------------------------

    def execute(self, method, full_path, path_params=None, query_params=None):
        if not self.base_url:
            raise ValueError("Cannot execute: WORKDAY_BASE_URL is not configured.")

        # 1. Strip duplicate base-path prefix
        if "api/common/v1" in self.base_url and full_path.startswith("/api/common/v1"):
            full_path = full_path.replace("/api/common/v1", "", 1)

        # 2. Inject path parameters
        if path_params:
            for key, value in path_params.items():
                full_path = full_path.replace(f"{{{key}}}", str(value))
                full_path = full_path.replace(f"%7B{key}%7D", str(value))

        # 3. Guard against unresolved template placeholders
        if "{" in full_path or "%7B" in full_path:
            raise ValueError(f"Missing required path parameter in: {full_path}")

        # 4. Build full URL
        url = f"{self.base_url.rstrip('/')}/{full_path.lstrip('/')}"

        # 5. Cache check (GET only)
        cache_key = f"{method.upper()}:{url}:{json.dumps(query_params, sort_keys=True)}"
        if method.upper() == "GET" and cache_key in self.cache:
            print(f"[Cache] HIT for: {url}", file=sys.stderr)
            return self.cache[cache_key]

        return self._execute_with_retry(method, url, query_params, cache_key)

    def _execute_with_retry(self, method, url, query_params, cache_key, _retry=True):
        """Execute the HTTP call; on 401 force-refresh the token and retry once."""
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

        print(f"Executing {method.upper()} → {url}", file=sys.stderr)

        response = None
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=query_params,
                timeout=30,
            )

            # On 401, invalidate token and retry exactly once
            if response.status_code == 401 and _retry:
                print("[OAuth] 401 received — forcing token refresh and retrying...", file=sys.stderr)
                self._token_expires_at = 0  # Force re-fetch on next _get_token()
                return self._execute_with_retry(method, url, query_params, cache_key, _retry=False)

            response.raise_for_status()
            data = response.json()

            # Populate cache on successful GET
            if method.upper() == "GET":
                self.cache[cache_key] = data

            return data

        except requests.exceptions.RequestException as e:
            print(f"Workday API Error: {e}", file=sys.stderr)
            error_details = (
                response.text
                if response is not None
                else "No response body."
            )
            return {"error": str(e), "details": error_details}


if __name__ == "__main__":
    client = WorkdayClient()
    print("Workday Client is ready.", file=sys.stderr)