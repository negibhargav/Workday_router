"""
executor.py — Sequential API step runner.
"""

import json
import os
import sys
from weakref import ref
from openai import OpenAI

from src.rag.dispatcher import WorkdayDispatcher
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

_EXTRACT_PROMPT = """You are a JSON field extractor.
Given a Workday API JSON response, extract ONLY the requested fields.
IMPORTANT — read carefully:
- First, determine if the response represents a SINGLE record or MULTIPLE records
(look for a top-level "data" array, or any array containing multiple objects
that each look like a full record).
- If it is a SINGLE record: return ONE flat JSON object with the requested
field names as keys, e.g. {{"id": "...", "name": "..."}}.
- If it is MULTIPLE records: return a JSON object with exactly one key,
"items", whose value is a JSON ARRAY. Each element of the array is a flat
object containing the requested fields for that record. Include EVERY
record found — do not drop, merge, or summarize any of them.
- If a field is nested, search for it anywhere within each individual record.
- If a field cannot be found for a record, set its value to null.
- No markdown, no explanation, no extra keys — just the JSON object.

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
        print(f"[Executor] Resolved params: {resolved_params}", file=sys.stderr)
        
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
        print(f"[Executor] RAG matched: {method} {api_path}", file=sys.stderr)
        
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
                    f"[Executor] WARNING: Could not resolve param '{param_name}' "
                    f"from ref '{source_ref}'",
                    file=sys.stderr,
                )
        return resolved

    def _resolve_ref(self, ref: str, context: dict):
        if not ref or "." not in ref:
            return None

        parts = ref.split(".", 1)
        step_key = parts[0]
        field = parts[1]
        
        step_data = context.get(step_key, {})
        
        extracted = step_data.get("extracted", {})
        
        # NOTE: if extracted is list-shaped ({"items": [...]}), a plain field
        # lookup won't find anything here — that's expected for now. If you
        # have multi-step plans that need to chain off a list result (e.g.
        # "get the manager of my first direct report"), flag it and we'll
        # extend this to support indexed/aggregate refs like "step_1.items[0].id".
        
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
        Builds the final executable path, path_params, and query_params.
        """
        path_params = dict(step.get("path_params", {}))
        query_params = dict(step.get("query_params", {}))
        final_path = api_path

        # 1. Layer in any parameters resolved from previous steps (param_map)
        if resolved_params:
            for key, val in resolved_params.items():
                if f"{{{key}}}" in final_path or key in ["ID", "id", "subresourceID", "worker_id"]:
                    path_params[key] = val
                else:
                    query_params[key] = val

        # ── 2.5 NEW: Self-Lookup ("me") Safety Net ──
        # If an ID is required but missing, check if the step intent implies the current user
        if "{ID}" in final_path and not any(k in path_params for k in ["ID", "id", "worker_id"]):
            intent_text = (step.get("intent", "") + " " + step.get("api_hint", "")).lower()
            if any(w in intent_text for w in ["current worker", "current user", "my ", "me ", "myself", "own profile"]):
                print("[Executor] Detected self-lookup intent. Auto-binding ID='me'", file=sys.stderr)
                path_params["ID"] = "me"

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

            path_params.pop("id", None)
            path_params.pop("worker_id", None)

        # 4. Universal structural fallback
        if "{ID}" in final_path and "ID" not in path_params:
            print(f"[Executor] WARNING: Unbound ID on {final_path}. Reverting to base collection.", file=sys.stderr)
            final_path = final_path.split("/step")[0].rsplit("/{", 1)[0]

        return final_path, path_params, query_params

    def _extract_fields(self, response_str: str, fields: list[str]) -> dict:
        if not fields or not response_str:
            return {}

        print(f"[Executor] Extracting fields: {fields}", file=sys.stderr)

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

            # Normalize list-shaped results into a consistent structure
            if isinstance(extracted, dict) and isinstance(extracted.get("items"), list):
                items = extracted["items"]
                print(f"[Executor] Extractor returned {len(items)} item(s)", file=sys.stderr)
                return {"items": items, "count": len(items)}

            return extracted

        except Exception as e:
            print(f"[Executor] Field extraction failed: {e}", file=sys.stderr)
            return self._fallback_extract(response_str, fields)

    def _fallback_extract(self, response_str: str, fields: list[str]) -> dict:
        """
        Deterministic fallback if the LLM call fails or returns bad JSON.
        Handles both single-record and list-shaped ("data": [...]) responses.
        """
        try:
            raw_json = json.loads(response_str)
        except Exception:
            return {f: None for f in fields}

        # List-shaped response: top-level "data" array with multiple entries
        if isinstance(raw_json, dict) and isinstance(raw_json.get("data"), list):
            data_list = raw_json["data"]
            if len(data_list) > 1:
                items = [
                    {f: self._deep_find(entry, f) for f in fields}
                    for entry in data_list
                ]
                print(f"[Executor] Fallback extracted {len(items)} item(s)", file=sys.stderr)
                return {"items": items, "count": len(items)}
            elif len(data_list) == 1:
                entry = data_list[0]
                return {f: self._deep_find(entry, f) for f in fields}
            else:
                return {f: None for f in fields}

        # Single-record response
        return {f: self._deep_find(raw_json, f) for f in fields}