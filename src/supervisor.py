import os
import json
from openai import OpenAI
from dotenv import load_dotenv

# Import your actual Workday tool logic
from src.tools.router_tool import WorkdayRouterTool

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
router = WorkdayRouterTool()

# =====================================================================
# 1. INTERNAL TOOL EXECUTION (What the Supervisor can do)
# =====================================================================
def fetch_workday_data(natural_language_query: str) -> str:
    """The internal function the OpenAI Supervisor calls to get raw Workday data."""
    print(f"\n[Supervisor] 🛠️ Executing internal Workday fetch for: '{natural_language_query}'")
    try:
        # Calls your existing Python router to get the JSON from Workday
        return router.execute_query(user_question=natural_language_query)
    except Exception as e:
        return json.dumps({"error": str(e)})

available_functions = {
    "fetch_workday_data": fetch_workday_data
}

# The schema telling the OpenAI Supervisor what its tool does
tools = [
    {
        "type": "function",
        "function": {
            "name": "fetch_workday_data",
            "description": "Fetches raw data from the Workday API based on a natural language query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "natural_language_query": {
                        "type": "string",
                        "description": "A specific query to fetch data, e.g., 'List all employees' or 'Get direct reports for MGR-992'."
                    }
                },
                "required": ["natural_language_query"]
            }
        }
    }
]

# =====================================================================
# 2. THE SUPERVISOR LOOP
# =====================================================================
def run_intelligent_supervisor(user_prompt: str, model="gpt-4o") -> str:
    """
    The orchestrator that receives Cursor's query, makes a plan, 
    fetches data, filters it, and returns the final answer.
    """
    print(f"\n🚀 [MCP Server] Starting Internal Supervisor for: '{user_prompt}'")
    
    messages = [
        {"role": "system", "content": "You are a Workday Data Analyst. Break down the user's query into steps. If they ask for filtered data (e.g., 'names starting with B'), first use your tool to fetch the broader list, then manually filter the JSON data yourself, and only output the final formatted result to the user."},
        {"role": "user", "content": user_prompt}
    ]

    while True:
        # Ask OpenAI for the next step in the plan
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        response_message = response.choices[0].message
        
        # If OpenAI wants to fetch data:
        if response_message.tool_calls:
            messages.append(response_message)
            
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                # Execute the internal Workday fetch
                function_response = available_functions[function_name](
                    natural_language_query=function_args.get("natural_language_query")
                )
                
                # Feed the raw JSON back to OpenAI so it can read/filter it
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": function_response,
                })
        else:
            # If OpenAI has finished analyzing/filtering, return the final text
            print("\n✅ [Supervisor] Planning and filtering complete. Sending to Cursor.")
            return response_message.content