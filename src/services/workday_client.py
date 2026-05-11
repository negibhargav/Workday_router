import os
import requests
from dotenv import load_dotenv

load_dotenv()

class WorkdayClient:
    def __init__(self):
        print("Initializing Workday Client...")
        self.base_url = os.getenv("WORKDAY_BASE_URL")
        self.token = os.getenv("WORKDAY_API_TOKEN")
        
        if not self.base_url:
            print("WARNING: WORKDAY_BASE_URL is missing from .env")

    def execute(self, method, full_path, path_params=None, query_params=None):
        # 1. Fix the double-path issue
        # Workday tenant base URLs already include the API root, so stored catalog
        # paths should be tenant-relative before they are appended.
        if "api/common/v1" in self.base_url and full_path.startswith("/api/common/v1"):
            full_path = full_path.replace("/api/common/v1", "", 1)

        # 2. Inject parameters (like 21001 or subresource IDs)
        if path_params:
            for key, value in path_params.items():
                # Replace {ID} or {subresourceID} with the actual value
                full_path = full_path.replace(f"{{{key}}}", str(value))
                # Also handle the encoded version just in case
                full_path = full_path.replace(f"%7B{key}%7D", str(value))

        url = f"{self.base_url.rstrip('/')}/{full_path.lstrip('/')}"
        
        # 3. Setup Authentication
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json"
        }
        
        print(f"Executing {method} request to: {url}")
        
        # 4. Make the call
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=query_params
            )
            
            # Catch HTTP errors (401 Unauthorized, 404 Not Found, etc.)
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"Workday API Error: {e}")
            error_details = response.text if response is not None else "No response body."
            return {"error": str(e), "details": error_details}

if __name__ == "__main__":
    # A quick local test to ensure it boots up properly
    client = WorkdayClient()
    print("Workday Client is ready.")
    # # if __name__ == "__main__":
    # import json
    
    # client = WorkdayClient()
    # print("Workday Client initialized. Attempting to contact Workday...")
    
    # # We will test the base /workers endpoint. 
    # # We pass 'limit': 1 so we only pull one person just to prove the connection works.
    # test_response = client.execute(
    #     method="GET",
    #     full_path="/workers",
    #     query_params={"limit": 1}
    # )
    
    # print("\n--- Workday API Response ---")
    # print(json.dumps(test_response, indent=2))