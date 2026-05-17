import json
import os
import sys
from src.rag.dispatcher import WorkdayDispatcher
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

class WorkdayRouterTool:
    def __init__(self):
        print("Initializing Workday Router Tool...", file=sys.stderr)
        self.dispatcher = WorkdayDispatcher()
        
        # Initialize client with environment variables if available
        self.client = WorkdayClient(
            api_token=os.getenv("WORKDAY_API_TOKEN"),
            base_url=os.getenv("WORKDAY_BASE_URL")
        )

    def get_routing_plan(self, user_question: str) -> dict:
        """
        Wraps dispatcher.route_query to return a consistent routing plan.
        """
        raw_route = self.dispatcher.route_query(user_question)
        
        # Defensive copy to avoid mutating cache/original structures unintentionally
        route = dict(raw_route) if isinstance(raw_route, dict) else {}

        # Rename full_path to path for server.py tool execution contract
        if "full_path" in route:
            route["path"] = route["full_path"]
        else:
            route["path"] = route.get("path", "")

        return route

    def execute_query(self, user_question: str, path_params: dict = None) -> str:
        """
        1. Routes the question to find the API.
        2. Executes the API safely.
        """
        route = self.dispatcher.route_query(user_question)

        if not route or "error" in route:
            return json.dumps({
                "error": "Could not find a matching Workday API.",
                "details": route
            })

        api_name = route.get("api_name")
        method = route.get("method")
        full_path = route.get("full_path")
        params = path_params if path_params is not None else {}

        # Handle instance vs collection path safely
        if full_path and "{subresourceID}" in full_path and not params.get("subresourceID"):
            full_path = full_path.replace("/{subresourceID}", "")
            if api_name:
                api_name = api_name.replace("_instance_", "_collection_")

        try:
            workday_response = self.client.execute(
                method=method,
                full_path=full_path,
                path_params=params
            )
            
            # Step 3: The Token Limiter (Cleans and Truncates)
            response_str = clean_workday_response(workday_response)
            
            # Package the results
            final_result = {
                "routed_api": api_name,
                "confidence_score": route.get("confidence_score"),
                "workday_data": response_str
            }

            return json.dumps(final_result, indent=2)

        except Exception as e:
            return json.dumps({
                "error": "Failed to execute Workday API.", 
                "details": str(e)
            })