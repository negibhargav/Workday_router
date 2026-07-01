import json
import os
import re
import sys
from src.rag.dispatcher import WorkdayDispatcher
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

class WorkdayRouterTool:
    def __init__(self):
        print("Initializing Workday Router Tool...", file=sys.stderr)
        self.dispatcher = WorkdayDispatcher()
        
        self.client = WorkdayClient(
            api_token=os.getenv("WORKDAY_API_TOKEN"),
            base_url=os.getenv("WORKDAY_BASE_URL")
        )

    def _normalize_route(self, raw_route: dict) -> dict:
        if not isinstance(raw_route, dict):
            return {}
        
        route = dict(raw_route)
        path_value = route.get("full_path") or route.get("path", "")
        route["full_path"] = path_value
        route["path"] = path_value
        
        if "method" not in route:
            route["method"] = "GET"
            
        if "api_name" not in route:
            route["api_name"] = route.get("id") or route.get("operationId", "workday_api_call")
            
        return route

    def get_routing_plan(self, user_question: str) -> dict:
        raw_route = self.dispatcher.route_query(user_question)
        route = self._normalize_route(raw_route)
        
        raw_template_path = route.get("path", "")
        api_name = route.get("api_name", "")
        parameters = route.get("parameters", [])
        
        route["template_path"] = raw_template_path
        
        path_params = {}
        path_string = raw_template_path

        numeric_tokens = re.findall(r'\d+', user_question)

        # 1. Handle instance-to-collection down-toggling
        if "{subresourceID}" in path_string:
            if len(numeric_tokens) < 2 and not any(w in user_question.lower() for w in ["single", "specific", "particular"]):
                path_string = path_string.replace("/{subresourceID}", "")
                route["template_path"] = route["template_path"].replace("/{subresourceID}", "")
                if api_name:
                    api_name = api_name.replace("_instance_", "_collection_")
                parameters = [p for p in parameters if p.get("name") != "subresourceID"]

        # 2. Extract and Format Core Worker ID (Using the corrected Worker_ID tag)
        if "{ID}" in path_string:
            resolved_id = None
            if numeric_tokens:
                resolved_id = f"Worker_ID={numeric_tokens[0]}"
                if "{subresourceID}" in path_string and len(numeric_tokens) >= 2:
                    path_params["subresourceID"] = numeric_tokens[1]
                    path_string = path_string.replace("{subresourceID}", numeric_tokens[1])
            elif any(word in user_question.lower() for word in ["me", "my", "i ", "myself", "current user"]):
                resolved_id = "me"
            
            if resolved_id:
                path_params["ID"] = resolved_id
                path_string = path_string.replace("{ID}", resolved_id)
                parameters = [p for p in parameters if p.get("name") != "ID"]
            else:
                path_string = "/workers"
                route["template_path"] = "/workers"
                api_name = "retrieves_a_collection_of_workers"
                parameters = []

        # =====================================================================
        # ADAPTIVE PLANNING LAYER (The "Next Step" Thinker)
        # =====================================================================
        query_params = {}
        next_action = "EXECUTE"
        planning_note = "Route matches perfectly with all required identifiers."

        # Check if we landed on the general directory collection
        if path_string == "/workers":
            # Look for specific name filter keywords
            name_match = re.search(r'(?:name is|named|first name|last name|employee|worker)\s+([a-zA-Z]+)', user_question, re.IGNORECASE)
            
            if name_match:
                query_params["search"] = name_match.group(1).strip()
                planning_note = f"Name filter '{query_params['search']}' detected. Proceeding to execute filtered lookup."
            else:
                # If no name is found, make sure they actually asked for everyone
                if not any(w in user_question.lower() for w in ["all", "list every", "entire", "everyone"]):
                    next_action = "CLARIFY"
                    planning_note = "You are looking for a specific worker by name, but no valid name criteria could be securely extracted."

        route["path"] = path_string
        route["full_path"] = path_string
        route["api_name"] = api_name
        route["parameters"] = parameters
        route["extracted_params"] = path_params
        route["query_params"] = query_params
        route["planning"] = {
            "next_action": next_action,
            "note": planning_note
        }
        
        return route

    def execute_query(self, user_question: str, path_params: dict = None) -> str:
        route = self.get_routing_plan(user_question)

        if not route or "error" in route or not route.get("full_path"):
            return json.dumps({
                "error": "Could not find a valid matching Workday API path.",
                "details": route
            })

        # --- ENFORCE THE PLANNING GUARDRAIL BEFORE EXECUTING ---
        plan_meta = route.get("planning", {})
        if plan_meta.get("next_action") == "CLARIFY":
            return json.dumps({
                "status": "requires_clarification",
                "message": f"Planning Stop: {plan_meta.get('note')} Please provide the exact name or ID of the worker."
            }, indent=2)

        method = route.get("method", "GET")
        api_name = route.get("api_name", "")
        
        execution_params = route.get("extracted_params", {})
        if path_params:
            execution_params.update(path_params)

        try:
            # Format the final path with the search query if it exists
            final_path = route.get("template_path", route["full_path"])
            query_dict = route.get("query_params", {})
            if query_dict and "search" in query_dict:
                final_path = f"{final_path}?search={query_dict['search']}"

            workday_response = self.client.execute(
                method=method,
                full_path=final_path,
                path_params=execution_params
            )
            
            response_str = clean_workday_response(workday_response)
            
            return json.dumps({
                "routed_api": api_name,
                "planning_note": plan_meta.get("note"),
                "workday_data": response_str
            }, indent=2)

        except Exception as e:
            return json.dumps({
                "error": "Failed to execute Workday API.", 
                "details": str(e)
            })