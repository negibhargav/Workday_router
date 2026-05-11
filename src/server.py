from mcp.server.fastmcp import FastMCP
from src.tools.router_tool import WorkdayRouterTool
from src.services.workday_client import WorkdayClient

# 1. Initialize the MCP Server
mcp = FastMCP("Workday RAG Router")

# 2. Boot up your custom routing logic
router = WorkdayRouterTool()
workday_client = WorkdayClient()
# 3. Define the Tool for the AI
@mcp.tool()
def ask_workday(natural_language_query: str) -> str:
    """
    Finds the right Workday API and returns the ACTUAL data, not just the URL.
    """
    # 1. Ask the Router to find the API mapping and path
    # The router should return the API path (e.g., "/workers") and method
    plan = router.get_routing_plan(natural_language_query)
    
    # 2. Use the WorkdayClient (on the SERVER) to fetch the real data
    # We execute the call here so Cursor only sees the result
    try:
        raw_data = workday_client.execute(
            method=plan["method"],
            full_path=plan["path"],
            path_params=plan.get("path_params")
        )
        
        # 3. Clean or Truncate the data (Token Optimization)
        # We send ONLY the gathered data back to the client
        return str(raw_data) 
        
    except Exception as e:
        return f"Error gathering data from Workday: {str(e)}"