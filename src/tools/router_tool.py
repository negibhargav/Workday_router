import json
import os
import sys
from urllib.parse import urlencode
from src.rag.dispatcher import WorkdayDispatcher
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

class WorkdayRouterTool:
    def __init__(self):
        print("Initializing Universal Workday Router Tool...", file=sys.stderr)
        self.dispatcher = WorkdayDispatcher()
        
        self.client = WorkdayClient(
            api_token=None,
            base_url=os.getenv("WORKDAY_BASE_URL")
        )

    def execute_query(self, path: str, method: str = "GET", query_params: dict = None, path_params: dict = None, body: dict = None) -> str:
        """
        Universal API Executor: Fires the exact request built by the Planner LLM.
        No regular expressions, no guessing — perfectly scales across all Workday endpoints.
        """
        if not path:
            return json.dumps({
                "error": "No valid API path provided to the execution layer.",
                "details": "The Planner failed to supply an endpoint path."
            })

        try:
            final_path = path
            
            # 1. Universally inject URL path parameters (e.g., /workers/{ID} -> /workers/Worker_ID=abc)
            if path_params:
                for key, val in path_params.items():
                    final_path = final_path.replace(f"{{{key}}}", str(val))
                    
            # 2. Universally append URL query string parameters (e.g., ?search=B&limit=100)
            if query_params:
                query_string = urlencode(query_params)
                final_path = f"{final_path}?{query_string}"

            # 3. Execute the HTTP request via your client
            workday_response = self.client.execute(
                method=method,
                full_path=final_path,
                payload=body
            )
            
            # 4. Clean and truncate response to protect token limits
            response_str = clean_workday_response(workday_response)
            
            return json.dumps({
                "executed_url": f"{method} {final_path}",
                "workday_data": response_str
            }, indent=2)

        except Exception as e:
            return json.dumps({
                "error": "Workday API execution failed.", 
                "details": str(e),
                "attempted_path": path
            })