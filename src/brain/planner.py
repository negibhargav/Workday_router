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
_SYSTEM_PROMPT = """You are an expert API orchestration planner for Workday's REST API.

Your job is to decompose a user's natural language question into an ordered sequence
of API steps. Each step maps to ONE Workday REST API call.

Rules:
1. Use as FEW steps as possible. If a single API call answers the question, use 1 step.
2. Maximum 5 steps. If you need more, find a smarter path.
3. Each step must have a clear `intent` — a short English sentence describing what data
   it fetches and WHY it's needed.
4. `api_hint` is a short phrase the RAG system will use to find the right API endpoint.
   Make it descriptive: e.g. "get direct reports for a worker", "get worker profile by id",
   "get organization members", "get worker supervisory organization".
5. `depends_on` is the step `id` this step needs data from (null if independent).
6. `param_map` maps THIS step's input parameters to previous step results.
   Format: { "worker_id": "step_1.id" } means "take 'id' from step 1's extracted data".
   Use null if this step has no dependencies.
7. `extract_fields` is a list of field names to pull out of THIS step's API response
   for use by later steps. Only include fields that future steps actually need.
   Use an empty list [] if this is the final step.
8. For "me" / "current user" / "myself" queries, step 1 should use api_hint
   "get current worker profile" with param_map null (the executor will resolve "me").

ALWAYS return ONLY valid JSON in this exact schema. No markdown, no explanation:
{
  "goal": "<one sentence description of what the chain achieves>",
  "steps": [
    {
      "id": 1,
      "intent": "<what this step fetches and why>",
      "api_hint": "<short phrase for RAG search>",
      "depends_on": null,
      "param_map": null,
      "extract_fields": ["field1", "field2"]
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
            step.setdefault("depends_on", None)
            step.setdefault("param_map", None)
            step.setdefault("extract_fields", [])

        n = len(plan["steps"])
        print(f"[Planner] Plan created: '{plan.get('goal')}' ({n} step{'s' if n != 1 else ''})", file=sys.stderr)
        for s in plan["steps"]:
            print(
                f"  Step {s['id']}: {s['intent']} | api_hint='{s['api_hint']}'",
                file=sys.stderr,
            )

        return plan
