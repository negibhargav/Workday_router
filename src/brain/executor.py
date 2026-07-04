"""
executor.py — Sequential API step runner.
"""

import json
import os
import sys
from openai import OpenAI

from src.rag.dispatcher import WorkdayDispatcher
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

_EXTRACT_PROMPT = """You are a JSON field extractor. 
Given a Workday API JSON response, extract ONLY the requested fields.
Return ONLY a flat JSON object with the requested field names as keys.
If a field is nested, find it anywhere in the response.
If a field cannot be found, set its value to null.
No markdown, no explanation — just the JSON object.

Fields to extract: {fields}

Response to search:
{response}
"""


class Executor:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.llm = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.dispatcher = WorkdayDispatcher()
        self.client = WorkdayClient(
            api_token=os.getenv("WORKDAY_API_TOKEN"),
            base_url=os.getenv("WORKDAY_BASE_URL"),
        )

    def run(self, plan: dict) -> dict:
        context: dict = {}

        for step in plan.get("steps", []):
            step_key = f"step_{step['id']}"
            print(f"\n[Executor] ── Step {step['id']}: {step['intent']}", file=sys.stderr)

            try:
                result = self._run_step(step, context)
                context[step_key] = result

                if result.get("error"):
                    print(
                        f"[Executor] Step {step['id']} failed: {result['error']}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[Executor] Step {step['id']} OK | extracted: {result.get('extracted')}",
                        file=sys.stderr,
                    )

            except Exception as e:
                print(f"[Executor] Step {step['id']} raised exception: {e}", file=sys.stderr)
                context[step_key] = {"error": str(e), "extracted": {}, "raw_response": ""}

        return context

    def _run_step(self, step: dict, context: dict) -> dict:
        # 1. Resolve parameters from previous step results
        resolved_params = self._resolve_params(step.get("param_map"), context)
        print(f"[Executor]   Resolved params: {resolved_params}", file=sys.stderr)

        # 2. RAG → find the best matching API route for this step's intent
        route = self.dispatcher.route_query(step["api_hint"])

        if "error" in route:
            return {
                "error": f"RAG dispatch failed: {route['error']}",
                "extracted": {},
                "raw_response": "",
                "api_called": None,
            }

        api_path = route.get("full_path", route.get("path", ""))
        method = route.get("method", "GET")
        print(f"[Executor]   RAG matched: {method} {api_path}", file=sys.stderr)

        # 3. Combine Planner parameters + resolved inter-step params
        final_path, path_params, query_params = self._build_path(api_path, resolved_params, step)

        # 4. Call Workday API via Client
        raw_data = self.client.execute(
            method=method,
            full_path=final_path,
            path_params=path_params,
            query_params=query_params if query_params else None,
        )

        # 5. Clean + truncate the response
        response_str = clean_workday_response(raw_data)

        # 6. Extract specific fields the plan says we'll need downstream
        extract_fields = step.get("extract_fields", [])
        extracted = {}
        if extract_fields:
            extracted = self._extract_fields(response_str, extract_fields)

        return {
            "raw_response": response_str,
            "extracted": extracted,
            "api_called": f"{method} {final_path}",
        }

    def _resolve_params(self, param_map: dict | None, context: dict) -> dict:
        if not param_map:
            return {}

        resolved = {}
        for param_name, source_ref in param_map.items():
            value = self._resolve_ref(source_ref, context)
            if value is not None:
                resolved[param_name] = value
            else:
                print(
                    f"[Executor]   WARNING: Could not resolve param '{param_name}' "
                    f"from ref '{source_ref}'",
                    file=sys.stderr,
                )
        return resolved

    def _resolve_ref(self, ref: str, context: dict):
        if not ref or "." not in ref:
            return None

        parts = ref.split(".", 1)
        step_key = parts[0]
        field    = parts[1]

        step_data = context.get(step_key, {})

        extracted = step_data.get("extracted", {})
        if field in extracted:
            return extracted[field]

        raw = step_data.get("raw_response", "")
        if raw:
            try:
                raw_json = json.loads(raw)
                return self._deep_find(raw_json, field)
            except Exception:
                pass

        return None

    def _deep_find(self, obj, key: str):
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                result = self._deep_find(v, key)
                if result is not None:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._deep_find(item, key)
                if result is not None:
                    return result
        return None

    def _build_path(self, api_path: str, resolved_params: dict, step: dict) -> tuple[str, dict, dict]:
        """
        Builds the final executable path, path_params, and query_params
        by combining Planner extractions and resolved inter-step dependencies.
        """
        # 1. Initialize directly with pre-extracted parameters from the Planner LLM
        path_params = dict(step.get("path_params", {}))
        query_params = dict(step.get("query_params", {}))
        final_path = api_path

        # 2. Layer in any parameters resolved from previous steps (param_map)
        if resolved_params:
            for key, val in resolved_params.items():
                # Check if the parameter belongs in the URL path template
                if f"{{{key}}}" in final_path or key in ["ID", "id", "subresourceID", "worker_id"]:
                    path_params[key] = val
                else:
                    query_params[key] = val

        # 3. Apply standard Workday key formatting rules safely
        if "ID" in path_params or "id" in path_params or "worker_id" in path_params:
            raw_id = (
                path_params.get("ID") 
                or path_params.get("id") 
                or path_params.get("worker_id")
            )
            bare_id = str(raw_id).removeprefix("Worker_ID=")
            
            if bare_id.lower() == "me" or bare_id == "":
                path_params["ID"] = "me"
            else:
                path_params["ID"] = f"Worker_ID={bare_id}"
                
            # Clean up alternate casing keys to avoid duplicate binds
            path_params.pop("id", None)
            path_params.pop("worker_id", None)

        # 4. Handle structural fallback if no identifier is bound to a details path
        if "{ID}" in final_path and "ID" not in path_params:
            print("[Executor] WARNING: Details path detected but no ID bound. Falling back to base directory.", file=sys.stderr)
            final_path = "/workers"

        return final_path, path_params, query_params

    def _extract_fields(self, response_str: str, fields: list[str]) -> dict:
        if not fields or not response_str:
            return {}

        print(f"[Executor]   Extracting fields: {fields}", file=sys.stderr)

        prompt = _EXTRACT_PROMPT.format(
            fields=", ".join(fields),
            response=response_str[:4000],
        )

        try:
            resp = self.llm.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            extracted = json.loads(resp.choices[0].message.content)
            return extracted
        except Exception as e:
            print(f"[Executor]   Field extraction failed: {e}", file=sys.stderr)
            try:
                raw_json = json.loads(response_str)
                return {f: self._deep_find(raw_json, f) for f in fields}
            except Exception:
                return {f: None for f in fields}