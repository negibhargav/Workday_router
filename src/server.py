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

load_dotenv()

# ============================
#   MCP SERVER INITIALIZATION
# ============================

mcp = FastMCP("workday-mcp")

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
    Natural language → Routing plan → Workday API call → Clean JSON
    """

    # 1. Routing via vector search
    plan = router.get_routing_plan(q)
    original_path = plan["path"]

    # ---- FALLBACK: if ID missing but endpoint requires one ----
    if "{ID}" in original_path and not plan.get("path_params"):
        print("DEBUG: No ID detected → fallback to collection endpoint", file=sys.stderr)
        plan["path"] = "/api/common/v1/workers"

    # 2. Build full Workday URL
    base = os.getenv("WORKDAY_BASE_URL", "").rstrip("/")
    path = plan["path"].lstrip("/")

    # Prevent double "api/common/v1"
    if "api/common/v1" in base and "api/common/v1" in path:
        path = path.replace("api/common/v1", "").lstrip("/")

    full_api_link = f"{base}/{path}"

    # 3. Execute Workday Call
    try:
        data = client.execute(
            method=plan["method"],
            full_path=plan["path"],
            path_params=plan.get("path_params")
        )

        return {
            "status": "success",
            "query": q,
            "api_called": full_api_link,
            "method": plan["method"],
            "gathered_data": data,
            "routing_plan": plan,
        }

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