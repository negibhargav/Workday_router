import json
import sys
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment variables from the root .env file
load_dotenv()

# Add the root directory to sys.path so internal imports (from src.xxx) resolve cleanly
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.append(root_dir)

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
#   TOOL 1: ask_workday (equivalent to /ask endpoint in your FastAPI)
# =====================================================================
@mcp.tool()
def ask_workday(natural_language_query: str):
    """
    Routes a human query to the Workday semantic vector database.
    
    CRITICAL DIRECTIVE FOR THE AI AGENT:
    You must extract the user's exact, raw natural language input word-for-word 
    and pass it directly into the 'user_question' parameter. 
    
    DO NOT optimize, paraphrase, technicalize, expand, or rewrite the query. 
    DO NOT inject guessed API paths, HTTP methods, or tokens like 'me'. 
    Pass the raw string precisely as the human typed it.
    """

    # 1. Route the query
    plan = router.get_routing_plan(natural_language_query)

    # 2. Validate plan before doing anything
    if not plan or not isinstance(plan, dict):
        return {"status": "error", "message": "Router returned no plan."}

    missing = [k for k in ["method", "path"] if k not in plan]
    if missing:
        return {"status": "error", "message": f"Routing plan missing keys: {missing}", "plan": plan}

    # 3. Block writes from this tool
    if plan.get("method", "").upper() != "GET":
        return {
            "status": "blocked",
            "message": f"This query requires a {plan['method']} operation. Use execute_workday_action for writes.",
            "routing_plan": plan
        }

    # 4. Validate path_params are present if path has {placeholders}
    import re
    placeholders = re.findall(r"\{(\w+)\}", plan["path"])
    path_params = plan.get("path_params") or {}
    missing_params = [p for p in placeholders if p not in path_params]
    if missing_params:
        return {
            "status": "error",
            "message": f"Cannot call API — missing path params: {missing_params}",
            "path": plan["path"],
            "routing_plan": plan
        }

    # 5. Log what was routed (visibility)
    print(f"[Router] {plan['method']} {plan['path']} | params: {path_params}", file=sys.stderr)

    # 6. Execute
    try:
        raw_data = client.execute(
            method=plan["method"],
            full_path=plan["path"],
            path_params=path_params if path_params else None
        )
        return clean_workday_response(raw_data)

    except Exception as e:
        attempted_path = f"{os.getenv('WORKDAY_BASE_URL', '')}{plan['path']}"
        return {
            "status": "error",
            "api_link_attempted": attempted_path,
            "message": str(e),
            "routing_plan": plan,
        }


@mcp.tool()
def execute_workday_action(natural_language_query: str, confirmed: bool = False):
    """
    For WRITE operations (POST) only — creates or updates Workday data.
    Requires confirmed=True to actually execute.
    """
    plan = router.get_routing_plan(natural_language_query)

    if not plan or plan.get("method", "").upper() == "GET":
        return {"status": "error", "message": "This is not a write operation. Use ask_workday instead."}

    if not confirmed:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm this action before it executes.",
            "routing_plan": plan
        }

    try:
        raw_data = client.execute(
            method=plan["method"],
            full_path=plan["path"],
            path_params=plan.get("path_params"),
            body=plan.get("body")
        )
        return clean_workday_response(raw_data)
    except Exception as e:
        return {"status": "error", "message": str(e), "routing_plan": plan}
# ============================
#   START THE MCP SERVER
# ============================

if __name__ == "__main__":
    mcp.run()