"""
executor.py — Sequential API step runner.

Iterates through a Planner-generated step list and:
  1. Resolves parameters from previous step results (param_map)
  2. Uses the RAG dispatcher to find the right Workday API
  3. Calls the Workday API via WorkdayClient
  4. Uses a focused LLM call to extract specific fields from the raw response
  5. Stores everything in a shared context dict

The context is the executor's "memory" — it accumulates results across steps
so later steps can reference earlier ones via "step_N.field_name" notation.
"""

import json
import os
import re
import sys
from openai import OpenAI

from src.rag.dispatcher import WorkdayDispatcher
from src.services.workday_client import WorkdayClient
from src.utils.token_limiter import clean_workday_response

# ---------------------------------------------------------------------------
# Prompt for the inter-step field extractor
# ---------------------------------------------------------------------------
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
    """
    Runs a multi-step plan produced by Planner.

    Returns:
        context dict — { "step_1": {...}, "step_2": {...}, ... }
        Each step entry contains:
            "raw_response": the cleaned Workday API response string
            "extracted":    the specific fields pulled out for downstream steps
            "api_called":   the API route that was used
            "error":        error message if this step failed (optional)
    """

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.llm = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.dispatcher = WorkdayDispatcher()
        self.client = WorkdayClient(
            api_token=os.getenv("WORKDAY_API_TOKEN"),
            base_url=os.getenv("WORKDAY_BASE_URL"),
        )

    # ── Public interface ─────────────────────────────────────────────────────

    def run(self, plan: dict) -> dict:
        """
        Execute all steps in the plan sequentially.

        Args:
            plan: the dict returned by Planner.plan()

        Returns:
            context dict keyed by "step_N"
        """
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
                    # Continue — synthesizer will handle partial data
                else:
                    print(
                        f"[Executor] Step {step['id']} OK | extracted: {result.get('extracted')}",
                        file=sys.stderr,
                    )

            except Exception as e:
                print(f"[Executor] Step {step['id']} raised exception: {e}", file=sys.stderr)
                context[step_key] = {"error": str(e), "extracted": {}, "raw_response": ""}

        return context

    # ── Private helpers ──────────────────────────────────────────────────────

    def _run_step(self, step: dict, context: dict) -> dict:
        """Execute a single step and return its result dict."""

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

        # 3. Inject resolved params + handle "me" / Worker_ID format
        # _build_path now returns 3 values: path, path_params, query_params
        final_path, path_params, query_params = self._build_path(api_path, resolved_params, step)

        # 4. Call Workday API
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
        """
        Resolve a step's param_map against the accumulated context.

        Example:
            param_map = { "worker_id": "step_1.managerId" }
            context   = { "step_1": { "extracted": { "managerId": "abc123" } } }
            → returns { "worker_id": "abc123" }
        """
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
        """
        Resolve a dotted reference like "step_1.managerId" against context.
        Looks in the step's "extracted" dict first, then the full context entry.
        """
        if not ref or "." not in ref:
            return None

        parts = ref.split(".", 1)
        step_key = parts[0]          # e.g. "step_1"
        field    = parts[1]          # e.g. "managerId"

        step_data = context.get(step_key, {})

        # Prefer the extracted dict (clean, field-specific)
        extracted = step_data.get("extracted", {})
        if field in extracted:
            return extracted[field]

        # Fallback: search the raw response string for the field
        raw = step_data.get("raw_response", "")
        if raw:
            try:
                raw_json = json.loads(raw)
                return self._deep_find(raw_json, field)
            except Exception:
                pass

        return None

    def _deep_find(self, obj, key: str):
        """Recursively search for a key in a nested dict/list."""
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
        Determine the final API path, path_params, and query_params for WorkdayClient.execute().

        Handles:
        - "me" / current user shortcut
        - Worker_ID=<value> format that Workday expects
        - Generic {ID} / {subresourceID} template replacement
        - Name-based search fallback: extracts a name from intent and appends ?search=
        """
        path_params = {}
        query_params = {}
        final_path = api_path

        # Check if this step resolves to "current user"
        intent_lower = step.get("intent", "").lower() + step.get("api_hint", "").lower()
        is_self_lookup = any(
            w in intent_lower for w in ["current user", "current worker", "myself", " me ", "my profile"]
        )

        if "{ID}" in final_path:
            # Priority 1: resolved param provides an explicit ID
            raw_id = (
                resolved_params.get("worker_id")
                or resolved_params.get("ID")
                or resolved_params.get("id")
            )

            if raw_id:
                # Strip any existing Worker_ID= prefix to avoid double-prefixing
                bare_id = str(raw_id).removeprefix("Worker_ID=")
                formatted_id = "me" if bare_id == "me" else f"Worker_ID={bare_id}"
                path_params["ID"] = formatted_id
            elif is_self_lookup:
                path_params["ID"] = "me"
            else:
                # No ID — fall back to /workers collection.
                # Try to extract a name from the step intent so we can search by name.
                final_path = "/workers"
                name = self._extract_name_from_intent(
                    step.get("intent", ""), step.get("api_hint", "")
                )
                if name:
                    query_params["search"] = name
                    print(
                        f"[Executor]   No ID resolved — searching /workers?search={name}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "[Executor]   No ID or name resolved, falling back to /workers collection",
                        file=sys.stderr,
                    )

        return final_path, path_params, query_params

    # ---- name extraction helper ------------------------------------------------

    _NAME_PATTERNS = [
        r"for\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",          # "for Joy Banks"
        r"by\s+name\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",    # "by name Joy Banks"
        r"named\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",        # "named Joy Banks"
        r"worker\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",       # "worker Joy Banks"
        r"employee\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",     # "employee Joy Banks"
    ]

    def _extract_name_from_intent(self, intent: str, api_hint: str) -> str | None:
        """Try to pull a proper name (Title Case words) out of intent / api_hint text."""
        import re as _re
        combined = f"{intent} {api_hint}"
        for pattern in self._NAME_PATTERNS:
            m = _re.search(pattern, combined)
            if m:
                # Return only the first word (first name) as the search term
                # — Workday's /workers?search is a prefix/substring match
                return m.group(1).split()[0]
        # Last resort: any two consecutive Title Case words
        m = _re.search(r"([A-Z][a-z]+\s+[A-Z][a-z]+)", combined)
        if m:
            return m.group(1).split()[0]
        return None

    def _extract_fields(self, response_str: str, fields: list[str]) -> dict:
        """
        Use a lightweight LLM call to pull specific named fields out of the
        Workday API response JSON. This handles deeply nested, inconsistent schemas.
        """
        if not fields or not response_str:
            return {}

        print(f"[Executor]   Extracting fields: {fields}", file=sys.stderr)

        prompt = _EXTRACT_PROMPT.format(
            fields=", ".join(fields),
            response=response_str[:4000],   # Limit to avoid token blowout
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
            # Best-effort: try to find fields via deep search in raw JSON
            try:
                raw_json = json.loads(response_str)
                return {f: self._deep_find(raw_json, f) for f in fields}
            except Exception:
                return {f: None for f in fields}
