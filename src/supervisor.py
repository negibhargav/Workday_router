import os
import json
from openai import OpenAI
from pinecone import Pinecone
from dotenv import load_dotenv

# Import Zeep for SOAP operations
from zeep import Client
from zeep.wsse.username import UsernameToken

# Keep your existing router for REST fallback operations
from src.tools.router_tool import WorkdayRouterTool

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))

# Connect to your Pinecone Index (Ensure this matches your actual index name)
index = pc.Index("workday-router") 
router = WorkdayRouterTool()

# =====================================================================
# 1. THE INTERNAL TOOLS (The Brain's Hands)
# =====================================================================

def search_api_library(search_query: str) -> str:
    """Queries Pinecone RAG to find the correct Workday API schema."""
    print(f"\n[Supervisor] 📚 Searching Pinecone RAG for: '{search_query}'")
    
    try:
        # Generate vector for the Brain's search query
        response = client.embeddings.create(input=search_query, model="text-embedding-3-small")
        vector = response.data[0].embedding
        
        # Query Pinecone
        results = index.query(vector=vector, top_k=2, include_metadata=True)
        
        if not results.matches:
            return "No relevant Workday API found in the database. Ask the user for clarification."
            
        # Return the exact schema descriptions back to the OpenAI Brain
        schemas = [match.metadata['text'] for match in results.matches if 'text' in match.metadata]
        return "\n\n---\n\n".join(schemas)
    except Exception as e:
        return f"Error searching Pinecone: {str(e)}"

def execute_workday_api(api_type: str, endpoint: str, parameters: dict) -> str:
    """The Universal Executor. The Brain passes dynamic parameters here after reading Pinecone."""
    print(f"\n[Supervisor] ⚙️ Executing {api_type} API: '{endpoint}' with params: {parameters}")
    
    if api_type.upper() == "SOAP":
        return _handle_dynamic_soap(endpoint, parameters)
    elif api_type.upper() == "REST":
        # For now, we route REST back through your existing WorkdayRouterTool
        query_string = json.dumps(parameters)
        return router.execute_query(user_question=f"Execute {endpoint} with {query_string}")
    else:
        return json.dumps({"error": f"Unknown API type: {api_type}"})

def _handle_dynamic_soap(endpoint: str, parameters: dict) -> str:
    """Future-proof SOAP handler using Zeep."""
    WD_USERNAME = os.getenv("WD_USERNAME")
    WD_PASSWORD = os.getenv("WD_PASSWORD")
    
    # Update this to your exact Workday tenant WSDL URL
    WSDL_URL = "https://wd2-impl-services1.workday.com/ccx/service/your_tenant/Human_Resources/v39.2?wsdl"
    
    try:
        zeep_client = Client(WSDL_URL, wsse=UsernameToken(WD_USERNAME, WD_PASSWORD))
        
        # DYNAMIC EXECUTION: Finds the method name on the fly
        soap_method = getattr(zeep_client.service, endpoint)
        
        # Pass the Brain's dictionary directly into the Zeep method
        response = soap_method(**parameters)
        
        return str(response) 
    except Exception as e:
        return json.dumps({"error": f"SOAP Execution failed: {str(e)}"})


available_functions = {
    "search_api_library": search_api_library,
    "execute_workday_api": execute_workday_api
}

# =====================================================================
# 2. OPENAI TOOL SCHEMAS
# =====================================================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_api_library",
            "description": "Always use this FIRST. Searches the Workday Pinecone database to find the correct API endpoint and its required parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "e.g., 'direct reports manager SOAP' or 'business title REST'"}
                },
                "required": ["search_query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_workday_api",
            "description": "Executes the Workday API. You must strictly follow the parameter schema returned by the search_api_library tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_type": {"type": "string", "enum": ["REST", "SOAP"]},
                    "endpoint": {"type": "string", "description": "e.g., 'Get_Workers'"},
                    "parameters": {"type": "object", "description": "A JSON dictionary of the parameters required by the API."}
                },
                "required": ["api_type", "endpoint", "parameters"]
            }
        }
    }
]

# =====================================================================
# 3. THE MASTER ORCHESTRATOR LOOP
# =====================================================================
def run_intelligent_supervisor(user_prompt: str, model="gpt-4o") -> str:
    """
    The orchestrator that handles dynamic RAG searching and API execution.
    """
    print(f"\n🚀 [Backend] Supervisor handling query: '{user_prompt}'")
    
    messages = [
        {"role": "system", "content": "You are an autonomous Workday Agent. For every query: 1. Search the Pinecone API library. 2. Read the returned schema. 3. Execute the API with the exact parameters specified. 4. Filter the raw XML/JSON data yourself to answer the user's specific question."},
        {"role": "user", "content": user_prompt}
    ]

    # Added a loop counter safety mechanism to prevent infinite loops and token burn
    loop_count = 0
    max_loops = 5

    while loop_count < max_loops:
        loop_count += 1
        
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        response_message = response.choices[0].message
        
        if response_message.tool_calls:
            messages.append(response_message)
            
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                if function_name == "search_api_library":
                    function_response = search_api_library(function_args["search_query"])
                elif function_name == "execute_workday_api":
                    function_response = execute_workday_api(
                        api_type=function_args["api_type"],
                        endpoint=function_args["endpoint"],
                        parameters=function_args.get("parameters", {})
                    )
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": function_response,
                })
        else:
            print("\n✅ [Backend] Supervisor complete. Returning final answer to Cursor.")
            return response_message.content
            
    return "The system reached the maximum number of reasoning steps without a final answer. Please refine your query."