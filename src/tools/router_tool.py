import json
from rag.dispatcher import WorkdayDispatcher
from services.workday_client import WorkdayClient
import sys

class WorkdayRouterTool:
    def __init__(self):
        print("Initializing Workday Router Tool...", file=sys.stderr)
        self.dispatcher = WorkdayDispatcher()
        self.client = WorkdayClient()

    def get_routing_plan(self, user_question: str):
        """
        Wraps dispatcher.route_query to return a consistent routing plan.
        """
        route = self.dispatcher.route_query(user_question)

        # Rename full_path to path for server.py tool execution
        if "full_path" in route:
            route["path"] = route["full_path"]

        return route

    def execute_query(self, user_question: str, path_params: dict = None) -> str:
        """
        1. Routes the question to find the API.
        2. Executes the API safely.
        """
        route = self.dispatcher.route_query(user_question)

        if "error" in route:
            return json.dumps({
                "error": "Could not find a matching Workday API.",
                "details": route
            })

        api_name = route.get("api_name")
        method = route.get("method")
        full_path = route.get("full_path")

        # Handle instance vs collection path
        if full_path and "{subresourceID}" in full_path and not (path_params or {}).get("subresourceID"):
            full_path = full_path.replace("/{subresourceID}", "")
            if api_name:
                api_name = api_name.replace("_instance_", "_collection_")

        try:
            workday_response = self.client.execute(
                method=method,
                full_path=full_path,
                path_params=path_params
            )

            response_str = json.dumps(workday_response)

            MAX_CHARS = 8000
            if len(response_str) > MAX_CHARS:
                print(f"Truncating Workday response from {len(response_str)} to {MAX_CHARS} characters.", file=sys.stderr)
                response_str = response_str[:MAX_CHARS] + "\n... [DATA TRUNCATED] ..."

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