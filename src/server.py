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

# Import the new intelligent supervisor and the old router (for write actions and guardrails)
from src.supervisor import run_intelligent_supervisor
from src.tools.router_tool import WorkdayRouterTool

# 1. Initialize the MCP Server
mcp = FastMCP("Workday RAG Router")

# 2. Boot up your custom routing tool for direct write actions and planning checks
router = WorkdayRouterTool()


# =====================================================================
#  TOOL 1: ask_workday_assistant (The Intelligent Read Orchestrator)
# =====================================================================
@mcp.tool()
def ask_workday_assistant(query: str) -> str:
    """
    The ultimate Workday AI Assistant for READ (GET) operations only.
    Pass ANY Workday-related question here, and the backend agent will handle 
    the multi-step planning, API fetching, and data filtering automatically.
    
    CRITICAL AI DIRECTIVE - HARD STOP CONDITIONS:
    Do NOT look at previous chat history. Evaluate the query in isolation.
    1. If the user's query contains ANY numbers (e.g., "21008") or single names (e.g., "Benny"), you MUST NOT execute this tool. Ask for clarification.
    2. If the user's query is vague or missing a subject (e.g., "who reports?", "show history"), you MUST NOT execute this tool. Ask: "Who are you asking about?"
    3. Do NOT attempt to guess the subject, map numbers, or fill in the blanks yourself.

    If the query is clear and safe to proceed:
    Pass the user's conversational text exactly word-for-word. Do NOT optimize or rewrite it.
    """
    try:
        # --- PROGRAMMATIC GUARDRAIL: Peek at the plan routing metadata to enforce read-only safety ---
        plan = router.get_routing_plan(query)

        if not plan or "method" not in plan:
            return json.dumps({"status": "error", "message": "Router returned an invalid layout specification."})

        # Block writes from entering this read-only supervisor tool
        if plan.get("method", "").upper() != "GET":
            return json.dumps({
                "status": "blocked",
                "message": f"This query requires a {plan['method']} operation. Use execute_workday_action for updates.",
                "routing_plan": plan
            })

        # --- If safe, hand the query over to the internal OpenAI Supervisor ---
        final_answer = run_intelligent_supervisor(user_prompt=query)
        return final_answer
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Supervisor Pipeline Failed: {str(e)}"
        }, indent=2)


# =====================================================================
#  TOOL 2: execute_workday_action (Write operations)
# =====================================================================
@mcp.tool()
def execute_workday_action(natural_language_query: str, confirmed: bool = False) -> str:
    """
    For WRITE operations (POST) only — creates or updates Workday data.
    Requires confirmed=True to actually execute.
    """
    plan = router.get_routing_plan(natural_language_query)

    if not plan or plan.get("method", "").upper() == "GET":
        return json.dumps({"status": "error", "message": "This is a read operation. Use ask_workday_assistant instead."})

    if not confirmed:
        return json.dumps({
            "status": "pending_confirmation",
            "message": "Please confirm this operational write action before execution.",
            "routing_plan": plan
        })

    # Hand complete execution over to the tool's encapsulated engine
    return router.execute_query(user_question=natural_language_query)


if __name__ == "__main__":
    mcp.run()