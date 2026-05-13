# import sys
# import os

# # ---------- FIX PYTHONPATH ----------
# sys.path.append(os.getcwd())
# ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# sys.path.insert(0, ROOT_DIR)

# # IMPORTANT: send to STDERR, not STDOUT
# print("PYTHONPATH FIX APPLIED:", ROOT_DIR, file=sys.stderr)

# # ---------- IMPORTS ----------
# from mcp.server.fastmcp import FastMCP

# # Lazy-load objects
# router = None
# workday_client = None


# def get_router():
#     global router
#     if router is None:
#         print("MCP Loading Router Tool...", file=sys.stderr)
#         from tools.router_tool import WorkdayRouterTool
#         router = WorkdayRouterTool()
#     return router


# def get_client():
#     global workday_client
#     if workday_client is None:
#         print("[MCP] Initializing Workday Client...", file=sys.stderr)
#         from services.workday_client import WorkdayClient
#         workday_client = WorkdayClient()
#     return workday_client


# # ---------- MCP SERVER ----------
# mcp = FastMCP("Workday RAG Router")
# print("[MCP] Server initialized.", file=sys.stderr)


# # ---------- TOOL ----------
# @mcp.tool()
# def ask_workday(natural_language_query: str) -> str:
#     print(f"[MCP] Received Query: {natural_language_query}", file=sys.stderr)

#     router = get_router()
#     client = get_client()

#     try:
#         plan = router.get_routing_plan(natural_language_query)
#         print("[MCP] Routing plan:", plan, file=sys.stderr)

#         raw_data = client.execute(
#             method=plan["method"],
#             full_path=plan["path"],
#             path_params=plan.get("path_params")
#         )

#         print("[MCP] Returning Workday data.", file=sys.stderr)
#         return str(raw_data)

#     except Exception as e:
#         print("[MCP] ERROR:", e, file=sys.stderr)
#         return f"Error gathering data from Workday: {str(e)}"


# # ---------- RUN THE SERVER ----------
# print("[MCP] Workday Router MCP Server STARTED.", file=sys.stderr)
# mcp.run()

import json
import sys
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Ensure imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.tools.router_tool import WorkdayRouterTool
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

# 1. Initialize the MCP Server
mcp = FastMCP("Workday RAG Router")

# 2. Boot up your custom routing logic
router = WorkdayRouterTool()
client = WorkdayClient(
    api_token=os.getenv("WORKDAY_API_TOKEN"),
    base_url=os.getenv("WORKDAY_BASE_URL")
)

# =====================================================================
#  TOOL 1: ask_workday (equivalent to /ask endpoint in your FastAPI)
# =====================================================================

@mcp.tool()
def ask_workday(q: str):
    """
    Finds the right Workday API and returns the ACTUAL data, not just the URL.
    """
    # 1. Ask the Router to find the API mapping and path
    plan = router.get_routing_plan(natural_language_query)
    
    # 2. Heuristic Field Extraction (Advanced Token Optimization)
    # If the user mentions specific fields, we filter for them
    common_fields = ["email", "phone", "name", "title", "position", "manager", "supervisory", "status", "location"]
    requested_fields = [f for f in common_fields if f in natural_language_query.lower()]
    
    # 3. Use the WorkdayClient to fetch the real data
    try:
        raw_data = workday_client.execute(
            method=plan["method"],
            full_path=plan["path"],
            path_params=plan.get("path_params")
        )
        
        # 4. Clean and Truncate the data (Token Optimization)
        # Pass the extracted fields to only return what was asked
        return clean_workday_response(raw_data, required_fields=requested_fields if requested_fields else None)
        
    except Exception as e:
        return {
            "status": "error",
            "api_link_attempted": full_api_link,
            "message": str(e),
            "routing_plan": plan,
        }


# ============================
#   START THE MCP SERVER
# ============================

if __name__ == "__main__":
    mcp.run()