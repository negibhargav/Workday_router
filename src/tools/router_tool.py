import json
from src.rag.dispatcher import WorkdayDispatcher
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

class WorkdayRouterTool:
    def __init__(self):
        print("Initializing Workday Router Tool...")
        self.dispatcher = WorkdayDispatcher()
        self.client = WorkdayClient()

    def get_routing_plan(self, user_question: str):
        """
        Wraps dispatcher.route_query to return a consistent routing plan.
        """
        route = self.dispatcher.route_query(user_question)
        # Rename full_path to path for compatibility with app.py
        if "full_path" in route:
            route["path"] = route["full_path"]
        return route

    def execute_query(self, user_question: str, path_params: dict = None) -> str:
        """
        1. Routes the question to find the API.
        2. Executes the API safely.
        """
        # Step 1: Find the right API
        route = self.dispatcher.route_query(user_question)
        if "error" in route:
            return json.dumps({"error": "Could not find a matching Workday API.", "details": route})
            
        api_name = route.get("api_name")
        method = route.get("method")
        full_path = route.get("full_path")
        if full_path and "{subresourceID}" in full_path and not (path_params or {}).get("subresourceID"):
            full_path = full_path.replace("/{subresourceID}", "")
            api_name = api_name.replace("_instance_", "_collection_") if api_name else api_name
        
        # Step 2: Execute the actual Workday API
        try:
            workday_response = self.client.execute(
                method=method, 
                full_path=full_path, 
                path_params=path_params
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
            return json.dumps({"error": "Failed to execute Workday API.", "details": str(e)})