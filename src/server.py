import json
import sys
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment variables from the root .env file
load_dotenv()

# Add the root directory to sys.path so internal imports resolve cleanly
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.append(root_dir)

# ── NEW: Import your multi-step brain instead of the old router tool ──
from supervisor import run_intelligent_supervisor, _get_brain

# 1. Initialize the MCP Server
mcp = FastMCP("Workday RAG Router")


# =====================================================================
#  TOOL 1: ask_workday (Read operations)
# =====================================================================
@mcp.tool()
def ask_workday(natural_language_query: str) -> str:
    """
    Routes a human query to the Workday semantic vector database to fetch data.
    
    You may execute this tool for general directory lookups, worker profiles, 
    direct reports, history tracking, and title lookups. 
    Accepts names, keywords, or numeric worker/employee IDs.
    """
    try:
        # 1. Lazy-load the multi-step brain components
        planner, executor, synthesizer = _get_brain()

        # 2. Let the Planner LLM decompose the query to inspect safety
        plan = planner.plan(natural_language_query)

        if not plan or "steps" not in plan:
            return json.dumps({"status": "error", "message": "Planner failed to generate a valid execution schema."})

        # 3. Block any write operations (POST/PUT/DELETE) from running in this read-only tool
        has_write = any(
            step.get("method", "GET").upper() != "GET" 
            for step in plan.get("steps", [])
        )
        if has_write:
            return json.dumps({
                "status": "blocked",
                "message": "This query requires a write operation (POST/PUT/DELETE). Use execute_workday_action for updates.",
                "routing_plan": plan
            }, indent=2)

        # 4. If read-only, execute the multi-step chain and synthesize the answer!
        context = executor.run(plan)
        return synthesizer.synthesize(
            user_query=natural_language_query, 
            plan=plan, 
            context=context
        )
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Internal MCP Tool Intercept: {str(e)}"
        }, indent=2)


# =====================================================================
#  TOOL 2: execute_workday_action (Write operations)
# =====================================================================
@mcp.tool()
def execute_workday_action(natural_language_query: str, confirmed: bool = False) -> str:
    """
    For WRITE operations (POST/PUT/DELETE) only — creates or updates Workday data.
    Requires confirmed=True to actually execute.
    """
    try:
        planner, _, _ = _get_brain()
        
        # 1. Inspect the plan first
        plan = planner.plan(natural_language_query)

        # Verify it actually contains a write operation
        has_write = any(
            step.get("method", "GET").upper() != "GET" 
            for step in plan.get("steps", [])
        )
        if not has_write:
            return json.dumps({
                "status": "error", 
                "message": "This is a read-only GET operation. Use ask_workday instead."
            }, indent=2)

        # 2. Require confirmation before executing write actions
        if not confirmed:
            return json.dumps({
                "status": "pending_confirmation",
                "message": "Please confirm this operational write action before execution.",
                "routing_plan": plan
            }, indent=2)

        # 3. Once confirmed, run the full intelligent supervisor pipeline!
        return run_intelligent_supervisor(natural_language_query)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Write execution failed: {str(e)}"
        }, indent=2)


if __name__ == "__main__":
    mcp.run()