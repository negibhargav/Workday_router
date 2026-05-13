import os,sys
import requests
from dotenv import load_dotenv

load_dotenv()

class WorkdayClient:
    def __init__(self, api_token=None, base_url=None):
        print("Initializing Workday Client...", file=sys.stderr)

        # Load from .env unless overridden
        self.base_url = base_url or os.getenv("WORKDAY_BASE_URL")
        self.token = api_token or os.getenv("WORKDAY_API_TOKEN")

        if not self.base_url:
            print("WARNING: WORKDAY_BASE_URL is missing from .env", file=sys.stderr)

        if not self.token:
            print("WARNING: WORKDAY_API_TOKEN is missing from .env", file=sys.stderr)

    def execute(self, method, full_path, path_params=None, query_params=None):
        """
        Executes a Workday REST API call using the routed details.
        """

        # -----------------------------
        # 1. Fix Double Path Issue
        # -----------------------------
        if (
            self.base_url
            and "api/common/v1" in self.base_url
            and full_path.startswith("/api/common/v1")
        ):
            full_path = full_path.replace("/api/common/v1", "", 1)

        # -----------------------------
        # 2. Inject Path Parameters
        # -----------------------------
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

        # -----------------------------
        # 4. Authentication & Headers
        # -----------------------------
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
            response.raise_for_status()  # Throw exceptions for 4xx/5xx

            # Workday sometimes returns empty responses (204)
            if response.text.strip() == "":
                return {"message": "Success", "status": response.status_code}

            return response.json()

        except requests.exceptions.RequestException as e:
            print(f"Workday API Error: {e}", file=sys.stderr)

            # FIX: response may not exist → prevent UnboundLocalError
            error_body = None
            try:
                error_body = response.text
            except Exception:
                error_body = "No response body."

            return {"error": str(e), "details": error_body}


if __name__ == "__main__":
    client = WorkdayClient()
    print("Workday Client is ready.", file=sys.stderr)