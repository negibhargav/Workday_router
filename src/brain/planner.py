"""
planner.py — LLM-powered query decomposer.

Takes a user's natural language query and produces an ordered list of
API steps that, when executed sequentially, will answer the question.

Each step knows:
  - What to look for (intent)
  - A hint for the RAG system to find the right API
  - Which previous step's data it needs (depends_on)
  - How to map that data into its own parameters (param_map)
  - Which fields to extract from its own result for future steps (extract_fields)
"""

import json
import os
import sys
from openai import OpenAI

# ---------------------------------------------------------------------------
# System prompt — teaches the LLM how to build a plan
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are an expert API orchestration planner for Workday's REST and SOAP APIs.

Your job is to decompose a user's natural language question into an ordered sequence
of API steps. Each step maps to ONE Workday API call (either REST or SOAP).

Rules:
1. Use as FEW steps as possible. If a single API call answers the question, use 1 step.
2. Maximum 5 steps. If you need more, find a smarter path.
3. Each step must have a clear `intent` — a short English sentence describing what data
   it fetches and WHY it's needed.
4. Every step MUST include `"api_type": "rest"` or `"api_type": "soap"`.

── REST steps ─────────────────────────────────────────────────────────────
5. Use `"api_type": "rest"` for simple lookups and navigation:
   - listing workers, searching by name, getting a worker profile
   - direct reports, org members, manager lookups
   - "who am I / me / current user" queries
   - anything expressible as a GET against /workers, /workers/{ID}, /workers/{ID}/directReports
6. For REST steps set `api_hint` (must be generic, no specific names/IDs), `query_params`, `path_params`.
   Leave `soap_args` null.

── SOAP steps ─────────────────────────────────────────────────────────────
7. Use `"api_type": "soap"` when the user's query contains concepts requiring deep worker details or actions:
   - COMPENSATION & PAY: compensation, salary, pay, payroll, pay grade.
   - BENEFITS: benefit, enrollment, eligible, insurance, health plan.
   - SKILLS & QUALIFICATIONS: skill, qualification, certification, education, language.
   - TALENT & PERFORMANCE: performance review, goal, development, talent assessment.
   - IDENTIFICATION: national ID, SSN, passport, government ID.
   - CONTRACTS & LEGAL: contract, collective agreement, probation.
   - HIRING: mutate/hire a new employee, onboard a candidate.
8. For SOAP steps:
   - Provide a generic, text-only `api_hint` describing the semantic intent (e.g. "get worker compensation details" or "hire a new employee"). Do NOT specify `service` (set it to null).
   - If querying worker data, populate `"soap_args"` with filters from this list (do NOT include `include_fields` response group flags as they will be resolved dynamically via RAG):
       worker_ids             → list[str]  — employee IDs to fetch
       organization_id        → str        — org reference ID
       include_subordinate_organizations → bool
       country_id             → str        — ISO-2 country code
       position_id            → str
       national_id            → str
       exclude_inactive_workers → bool
       page                   → int
       count                  → int (max 999)
   - If hiring/creating a new employee, populate `"soap_args"` with values from this list:
       first_name, last_name, middle_name, organization_id, position_id, job_requisition_id, hire_date, existing_worker_type, existing_worker_id, email_address, phone_number, address_line_1, address_city, address_postal_code, comment, auto_complete, base_pay_amount, base_pay_currency_id, base_pay_frequency_id.
   - Set `query_params` and `path_params` to null for SOAP steps.

── Common rules for ALL steps ───────────────────────────────────────────
9.  `depends_on` is the step `id` this step needs data from (null if independent).
10. `param_map` maps THIS step's inputs to previous step results.
    Format: { "ID": "step_1.id" } or { "worker_ids": ["step_1.id"] }.
    IMPORTANT: You must map dynamic dependencies using standard `<step_key>.<field_name>` syntax (like `step_1.id`).
    NEVER use list indexing or list notation like `step_1.extract_fields[0]`.
    Use null if this step has no dependencies.
11. `extract_fields` is a list of field names to pull from THIS step's response for
    later steps. Use [] if this is the final step.
12. For "me" / "current user" / "myself" REST queries, step 1 MUST set
    `"path_params": {"ID": "me"}`.
13. `query_params` and `path_params` MUST contain only literal values.
    NEVER put reference strings like `"step_1.id"` or `"step_1.extract_fields[0]"` inside `path_params` or `query_params` directly.
    Instead, leave those dynamic variables out of path_params/query_params and define them in `param_map`.
14. CRITICAL: The `api_hint` MUST be generic (e.g. use "search for worker", "worker profile", "worker direct reports").
    NEVER include specific names (like "Betty Liu") or IDs (like "21431") in the `api_hint`, as they skew embedding matches in RAG.
15. IMPORTANT: You CANNOT query a worker's profile or subresources (like `/workers/{ID}`) directly using a person's name (e.g., `path_params: {"ID": "Betty Liu"}` is INVALID).
    Instead, to lookup a worker by name, you MUST use a multi-step plan:
    - Step 1: Search for the worker on the collection endpoint using `"api_hint": "search for worker"` and `"query_params": {"search": "<name>"}`. Set `"extract_fields": ["id"]`.
    - Subsequent steps: Retrieve the profile or subresources mapping `"ID": "step_1.id"` in `"param_map"`.

ALWAYS return ONLY valid JSON in this exact schema. No markdown, no explanation:
{
  "goal": "<one sentence>",
  "steps": [
    {
      "id": 1,
      "intent": "<what this step fetches and why>",
      "api_type": "rest" | "soap",

      // REST-only fields (set to null for SOAP steps):
      "api_hint": "<short phrase for RAG search>",
      "query_params": {},
      "path_params": {},

      // SOAP-only fields (set to null for REST steps):
      "service": "get_workers",
      "soap_args": {
        "include_fields": ["Include_Compensation"]
      },

      // Common fields:
      "depends_on": null,
      "param_map": null,
      "extract_fields": []
    }
  ]
}
"""

# ---------------------------------------------------------------------------
# Planner class
# ---------------------------------------------------------------------------

MAX_STEPS = 5


class Planner:
    """Decomposes a user query into a structured multi-step execution plan."""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def plan(self, user_query: str) -> dict:
        """
        Given a user query, return a structured plan dict.

        Returns:
            {
                "goal": str,
                "steps": [
                    {
                        "id": int,
                        "intent": str,
                        "api_hint": str,
                        "depends_on": int | None,
                        "param_map": dict | None,
                        "extract_fields": list[str]
                    },
                    ...
                ]
            }

        Raises:
            ValueError: if the LLM returns unparseable JSON or violates the schema.
        """
        print(f"[Planner] Planning query: '{user_query}'", file=sys.stderr)

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,          # deterministic — planning must be stable
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": f"User query: {user_query}"},
            ],
        )

        raw = response.choices[0].message.content
        plan = json.loads(raw)

        # ── Validate + sanitize ──────────────────────────────────────────────
        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError(f"[Planner] LLM returned invalid plan schema: {raw}")

        steps = plan["steps"]

        # Cap at MAX_STEPS
        if len(steps) > MAX_STEPS:
            print(
                f"[Planner] Warning: plan had {len(steps)} steps, capping at {MAX_STEPS}",
                file=sys.stderr,
            )
            plan["steps"] = steps[:MAX_STEPS]

        # Ensure required fields exist on every step
        for i, step in enumerate(plan["steps"]):
            step.setdefault("id", i + 1)
            step.setdefault("api_type", "rest")     # default to REST
            step.setdefault("depends_on", None)
            step.setdefault("param_map", None)
            step.setdefault("query_params", {})
            step.setdefault("path_params", {})
            step.setdefault("api_hint", "")
            step.setdefault("service", None)         # e.g. "get_workers" for SOAP
            step.setdefault("soap_args", None)       # dict of SOAP args (SOAP only)
            step.setdefault("extract_fields", [])

        n = len(plan["steps"])
        print(f"[Planner] Plan created: '{plan.get('goal')}' ({n} step{'s' if n != 1 else ''})", file=sys.stderr)
        for s in plan["steps"]:
            api_type = s.get("api_type", "rest")
            if api_type == "soap":
                print(
                    f"  Step {s['id']}: {s['intent']} | api_type=SOAP service={s.get('service')} | soap_args={s.get('soap_args')}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  Step {s['id']}: {s['intent']} | api_type=REST api_hint='{s['api_hint']}' | query_params={s.get('query_params')}",
                    file=sys.stderr,
                )

        return plan
