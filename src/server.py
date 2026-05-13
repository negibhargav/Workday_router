from mcp.server.fastmcp import FastMCP
from src.tools.router_tool import WorkdayRouterTool
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

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
        return f"Error gathering data from Workday: {str(e)}"