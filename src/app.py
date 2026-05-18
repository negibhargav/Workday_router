# import sys
# import os
# from dotenv import load_dotenv
# from fastapi import FastAPI, Query

# # Fix path to allow importing from root
# sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# # Import your existing project classes
# from src.tools.router_tool import WorkdayRouterTool
# from src.services.workday_client import WorkdayClient

# load_dotenv()

# app = FastAPI(title="Workday MCP Mock Server")

# # Initialize project components
# router = WorkdayRouterTool()
# client = WorkdayClient(
#     api_token=os.getenv("WORKDAY_API_TOKEN"),
#     base_url=os.getenv("WORKDAY_BASE_URL")
# )

# @app.get("/ask")
# async def ask_workday_endpoint(q: str = Query(..., description="The natural language query")):
#     print(f"--- STEP 1: Received Query: {q} ---")
    
#     # 1. Routing (Pinecone Search)
#     plan = router.get_routing_plan(q)
#     original_path = plan["path"]
    
#     # --- NEW: LOGIC FALLBACK ---
#     # If the path requires an {ID} but the router didn't find one, 
#     # we fall back to the general collection endpoint to avoid the error.
#     if "{ID}" in original_path and not plan.get("path_params"):
#         print("DEBUG: No ID detected. Redirecting to general workers list.")
#         # Strip the specific sub-resource and ID template
#         plan["path"] = "/api/common/v1/workers"
    
#     # 2. Construct the URL for visibility
#     path_with_params = plan["path"]
#     if plan.get("path_params"):
#         for key, value in plan["path_params"].items():
#             path_with_params = path_with_params.replace(f"{{{key}}}", str(value))
    
#     # --- NEW: URL SANITIZATION ---
#     # Prevents "api/common/v1" from appearing twice if it's already in the Base URL
#     base = os.getenv('WORKDAY_BASE_URL').rstrip('/')
#     final_path = path_with_params.lstrip('/')
    
#     if "api/common/v1" in base and "api/common/v1" in final_path:
#         # Remove the duplicate version string from the path
#         final_path = final_path.replace("api/common/v1", "").lstrip('/')
    
#     full_api_link = f"{base}/{final_path}"
    
#     print(f"--- STEP 2: Full API Link: {full_api_link} ---")
    
#     # 3. Gathering Data (Actual Workday Call)
#     try:
#         raw_data = client.execute(
#             method=plan["method"],
#             full_path=plan["path"],
#             path_params=plan.get("path_params")
#         )
        
#         return {
#             "status": "success",
#             "query": q,
#             "api_called": full_api_link,
#             "method": plan["method"],
#             "gathered_data": raw_data
#         }
        
#     except Exception as e:
#         return {
#             "status": "error", 
#             "api_link_attempted": full_api_link,
#             "message": str(e)
#         }

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="127.0.0.1", port=8000)

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

# Initialize project components
router = WorkdayRouterTool()
client = WorkdayClient(
    api_token=os.getenv("WORKDAY_API_TOKEN"),
    base_url=os.getenv("WORKDAY_BASE_URL")
)

 
@app.get("/ask")
async def ask_workday_endpoint(q: str = Query(..., description="The natural language query")):
    print(f"--- STEP 1: Received Query: {q} ---")
    
    # 1. Routing (Pinecone Search)
    plan = router.get_routing_plan(q)
    
    if not plan or "method" not in plan or not plan.get("path"):
        return {
            "status": "error",
            "message": "The RAG router could not find a matching vector space in Pinecone.",
            "debug_returned_plan": plan
        }
        
    original_path = plan["path"]
    
    # --- NEW: LIGHTWEIGHT TESTING PARAMETER EXTRACTION ---
    path_params = {}
    
    if "{ID}" in original_path:
        # Check if the query implies a self-lookup
        if any(word in q.lower() for word in ["me", "my", "i ", "myself", "current user"]):
            path_params["ID"] = "me"
        else:
            # Check if there is an explicit numeric ID in the string (e.g., "worker 2104")
            numeric_ids = re.findall(r'\d+', q)
            if numeric_ids:
                path_params["ID"] = numeric_ids[0]

    # --- FIXED: LOGIC FALLBACK CHECK ---
    # Only redirect to general list if we absolutely could not extract an ID for an ID-required path
    if "{ID}" in original_path and "ID" not in path_params:
        print("DEBUG: No ID detected in string context. Redirecting to general workers list.")
        plan["path"] = "/workers"
    
    # 2. Construct the URL with our extracted testing parameters
    path_with_params = plan["path"]
    for key, value in path_params.items():
        path_with_params = path_with_params.replace(f"{{{key}}}", str(value))
    
    # --- URL SANITIZATION ---
    base = os.getenv('WORKDAY_BASE_URL', '').rstrip('/')
    final_path = path_with_params.lstrip('/')
    
    if "api/common/v1" in base and "api/common/v1" in final_path:
        final_path = final_path.replace("api/common/v1", "").lstrip('/')
    
    execution_path = f"/{final_path}" if not final_path.startswith('/') else final_path
    full_api_link = f"{base}{execution_path}"
    
    print(f"--- STEP 2: Full API Link: {full_api_link} ---")
    
    # 3. Gathering Data (Actual Workday Call)
    try:
        raw_data = client.execute(
            method=plan["method"],
            full_path=execution_path,
            path_params=path_params  # Pass the extracted parameters directly to the client execution
        )
        
        return {
            "status": "success",
            "query": q,
            "api_called": full_api_link,
            "method": plan["method"],
            "gathered_data": raw_data
        }
        
    except Exception as e:
        return {
            "status": "error", 
            "api_link_attempted": full_api_link,
            "message": str(e)
        } 
        
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)