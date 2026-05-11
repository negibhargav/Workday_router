import sys
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Query

# Fix path to allow importing from root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import your existing project classes
from src.tools.router_tool import WorkdayRouterTool
from src.services.workday_client import WorkdayClient

load_dotenv()

app = FastAPI(title="Workday MCP Mock Server")

# Initialize your project components
# These mimic the server startup in MCP
router = WorkdayRouterTool()
client = WorkdayClient(
    api_token="eyJ4NXQjUzI1NiI6IjdZanpscXN5VFhlakhmZ3lBOWFwR3ROa20xQ1dMSjNqLXdWVExWdG8tbVUiLCJ0eXAiOiJhdCtKV1QiLCJraWQiOiI3WWp6bHFzeVRYZWpIZmd5QTlhcEd0TmttMUNXTEozai13VlRMVnRvLW1VIiwiYWxnIjoiUlM1MTIifQ.eyJzdWIiOiIzZWJlNjkyNzVmMmE0MTk3OWY0M2EwNGQ1YTYxY2FiMyIsImV4cCI6MTc3ODUyMjc0MiwiaWF0IjoxNzc4NTA4MzQyLCJhdWQiOiJodHRwczovL3djcGRldi1zZXJ2aWNlczEud2QxMDEubXl3b3JrZGF5LmNvbS9jY3gvIiwianRpIjoiYjJvd2VIVmhNVGxqZWpnMk4zbG5OalkyYm5GcmIzVnlialo0WjJka2RuVjNhR1o2YlhRd01IQXhkRzAzY0RRek5tdzRkVEpvY21WdWRtRmxiVEZrYnpacmRtVTRhekYzWkhReGFUZzFhek53TTJneFpITXdNakl3WW1jeU9XVjVaR0oxTG1FMFpXSmhZamxsTFRkbFlUTXROR1ZpTmkxaE16aG1MV1JqTkRCak9UZzFPV1JrTUE9PSIsImlzcyI6Imh0dHBzOi8vd2NwZGV2LXNlcnZpY2VzMS53ZDEwMS5teXdvcmtkYXkuY29tL2NjeC9hcGkvdjEvamxsX3djcGRldjEiLCJzY29wZSI6Im9hdXRoIiwiZW52IjoiV0QxMDEtV0NQREVWIiwidGVuYW50IjoiamxsX3djcGRldjEiLCJjbGllbnRfaWQiOiJNRGd3TldKbU1ETXROekZtTUMwME9HVTBMVGsyWm1FdFkyVXhaV1kxTkdWa1ptVXoiLCJhY3QiOnsic3ViIjoiIn19.WyDvMavqxGMMXdcED2KaWMLTdE0xISLG62xLih3-X1Z-DxWKPNer9iH8GyHkngwsxTGYUbtfEY77YEoCIxUuWbnpLPfr-fCrzLklqd8EM6MGOgubEohfp5V3U3hhb6hjwbq4KJ1iU4k9mRvhRhDtggIJYgh6hBNuI9pgnOHedT51LCNztamOyTc0CV-H7mWnSrTvtf2Cw-m3yksLKmnkU2NhaKWd5ha1a3giRnLbhjTGEIlNZ-14cyF_5i9DoYJhA-5OmRXLs0o4HEWJq692WYTVdMJMXjqr_fVQznEIZE5JCKP0MGujpQQbZQpH4MKT28HNGZhg3Kmp7AqwwcAEFQ",
    base_url=os.getenv("WORKDAY_BASE_URL")
)

@app.get("/ask")
async def ask_workday_endpoint(q: str = Query(..., description="The natural language query")):
    """
    This endpoint mimics the 'ask_workday' MCP tool process.
    """
    print(f"--- STEP 1: Received Query: {q} ---")
    
    # STEP 2: Routing (Pinecone Search)
    # This mimics the router finding the API path
    plan = router.get_routing_plan(q)
    print(f"--- STEP 2: Routing Plan: {plan['path']} ---")
    
    # STEP 3: Gathering Data (Workday API Call)
    # This is the "Data Fetcher" step you wanted
    try:
        raw_data = client.execute(
            method=plan["method"],
            full_path=plan["path"],
            path_params=plan.get("path_params")
        )
        
        # STEP 4: Token Optimization / Truncation
        # Mimicking the server providing only gathered data to client
        MAX_CHARS = 8000
        response_str = str(raw_data)
        
        if len(response_str) > MAX_CHARS:
            print(f"DEBUG: Truncating from {len(response_str)} chars")
            response_str = response_str[:MAX_CHARS] + "... [Truncated]"
            
        return {
            "status": "success",
            "routing": plan["path"],
            "gathered_data": raw_data  # This is the 'gathered data' only
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    # Run the server on localhost:8000
    uvicorn.run(app, host="127.0.0.1", port=8000)