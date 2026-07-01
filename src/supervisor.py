import os
import json
from openai import OpenAI
from pinecone import Pinecone
from dotenv import load_dotenv

# Import the existing router for REST execution
from src.tools.router_tool import WorkdayRouterTool

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))

# Connect to your Pinecone Index
index = pc.Index("workday-router") 
router = WorkdayRouterTool()

# =====================================================================
# 1. THE RETRIEVER (Finds the API schema)
# =====================================================================
def retrieve_api_schema(search_query: str) -> str:
    """Queries Pinecone RAG to find the correct Workday REST API schema."""
    print(f"\n[Retriever] Searching Pinecone RAG for: '{search_query}'")
    
    try:
        response = client.embeddings.create(input=search_query, model="text-embedding-3-small")
        vector = response.data[0].embedding
        
        results = index.query(vector=vector, top_k=2, include_metadata=True)
        
        if not results.matches:
            return "No relevant Workday API found. Ask the user for clarification."
            
        schemas = [match.metadata['text'] for match in results.matches if 'text' in match.metadata]
        return "\n\n---\n\n".join(schemas)
    except Exception as e:
        return f"Error searching Pinecone: {str(e)}"

# =====================================================================
# 2. THE EXECUTOR (Runs the REST API)
# =====================================================================
def execute_rest_api(endpoint: str, parameters: dict) -> str:
    """Executes the Workday REST API based on the retrieved schema."""
    print(f"\n[Executor] Running REST API: '{endpoint}' with params: {parameters}")
    
    try:
        # Route REST back through your existing WorkdayRouterTool
        # We convert the params to a string so your existing router can parse it
        query_string = json.dumps(parameters)
        return router.execute_query(user_question=f"Execute REST endpoint {endpoint} with {query_string}")
    except Exception as e:
        return json.dumps({"error": f"REST Execution failed: {str(e)}"})

# =====================================================================
# 3. OPENAI TOOL SCHEMAS (The Brain's interface)
# =====================================================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "retrieve_api_schema",
            "description": "RETRIEVER STEP: Always use this FIRST after planning. Searches the Workday Pinecone database to find the correct REST API endpoint and required parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "e.g., 'business title REST' or 'worker location'"}
                },
                "required": ["search_query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_rest_api",
            "description": "EXECUTOR STEP: Executes the Workday REST API. You must strictly follow the parameter schema returned by the retrieve_api_schema tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "endpoint": {"type": "string", "description": "e.g., 'workers' or 'organizations'"},
                    "parameters": {"type": "object", "description": "A JSON dictionary of the parameters required by the API."}
                },
                "required": ["endpoint", "parameters"]
            }
        }
    }
]

# =====================================================================
# 4. THE MASTER ORCHESTRATOR LOOP (Brain -> Planner -> Action -> Evaluate)
# =====================================================================
def run_intelligent_supervisor(user_prompt: str, model="gpt-4o") -> str:
    """
    The main agentic loop that forces the LLM to Plan, Retrieve, Execute, and Re-evaluate.
    """
    print(f"\n [Brain] Initiated for query: '{user_prompt}'")
    
    # System prompt strictly enforces the Cyclic Graph Architecture
    system_instruction = """
    You are the central Brain of a Workday Agentic Architecture. 
    You must follow this exact execution cycle for every query:
    1. PLANNER: First, output a numbered list of the execution steps required.
    2. RETRIEVER: Call the 'retrieve_api_schema' tool to get the REST API details.
    3. EXECUTOR: Call the 'execute_rest_api' tool using the exact schema parameters.
    4. EVALUATOR (Planner): Look at the executor's data. Ask yourself: "Do I need more steps to answer the user?" 
       - If YES: Repeat steps 2 and 3.
       - If NO: Provide the final answer and format the data cleanly.
       
    NOTE: We are ONLY using REST APIs. Do not attempt to use SOAP.
    """

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": user_prompt}
    ]

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
        
        # Capture the Brain's "Planning" thoughts if it output text before calling a tool
        if response_message.content:
            print(f"\n[Planner] Brain's logic/steps:\n{response_message.content}")

        if response_message.tool_calls:
            messages.append(response_message)
            
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                if function_name == "retrieve_api_schema":
                    function_response = retrieve_api_schema(function_args["search_query"])
                elif function_name == "execute_rest_api":
                    function_response = execute_rest_api(
                        endpoint=function_args["endpoint"],
                        parameters=function_args.get("parameters", {})
                    )
                
                # Feed the tool data back into the loop for the Evaluator step
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": function_response,
                })
        else:
            # If no tools are called, the Evaluator decided "No more steps needed"
            print("\n [Planner] Evaluation complete. No further steps required. Returning final answer.")
            return response_message.content
            
    return "The system reached the maximum number of reasoning steps without a final answer. Please refine your query."