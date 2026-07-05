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
6. For REST steps set `api_hint`, `query_params`, `path_params` (same as before).
   Leave `soap_args` null.

── SOAP steps ─────────────────────────────────────────────────────────────
7. Use `"api_type": "soap"` and `"service": "get_workers"` when the user's query
   contains ANY of these SOAP trigger keywords or concepts:

   COMPENSATION & PAY:
     compensation, salary, pay, payroll, pay group, pay rate, pay grade

   BENEFITS:
     benefit, benefits, enrollment, eligible, eligibility, insurance, health plan

   SKILLS & QUALIFICATIONS:
     skill, skills, qualification, qualifications, certification, certifications,
     education, degree, language, competency

   TALENT & PERFORMANCE:
     talent, talent assessment, performance, review, employee review, goal, goals,
     development, development items, succession, succession profile

   ORG HIERARCHY FLAGS:
     cost center, pay group, region, supervisory org, matrix org, custom org,
     fund, grant, business unit, program, gift, retiree org

   IDENTIFICATION:
     national ID, SSN, social security, passport, government ID, national id type

   DATE-RANGE / HISTORY:
     updated between, changed between, updated from, updated through,
     effective from, effective through, transaction log, history, audit

   CONTRACTS & LEGAL:
     contract, employee contract, collective agreement, probation, contingent worker tax

   PERSONAL DEEP DATA:
     photo, document, worker document, background check, user account, career,
     account provisioning, related person, feedback, management chain

   ADDITIONAL JOBS:
     additional job, multiple jobs

8. For SOAP steps:
   - Set `"service": "get_workers"`
   - Populate `"soap_args"` with ONLY the relevant keys from this list:
       worker_ids             → list[str]  — employee IDs to fetch
       organization_id        → str        — org reference ID
       include_subordinate_organizations → bool
       country_id             → str        — ISO-2 country code
       position_id            → str
       national_id            → str
       national_id_type       → str        — e.g. "SSN", "Passport"
       national_id_country    → str        — ISO-2
       exclude_inactive_workers → bool
       exclude_employees      → bool
       exclude_contingent_workers → bool
       updated_from           → ISO datetime str
       updated_through        → ISO datetime str
       effective_from         → ISO datetime str
       effective_through      → ISO datetime str
       as_of_effective_date   → ISO date str
       page                   → int
       count                  → int (max 999)
       include_fields         → list[str] from:
           Include_Reference, Include_Personal_Information,
           Show_All_Personal_Information, Include_Additional_Jobs,
           Include_Employment_Information, Include_Compensation,
           Include_Organizations, Exclude_Organization_Support_Role_Data,
           Exclude_Location_Hierarchies, Exclude_Cost_Centers,
           Exclude_Cost_Center_Hierarchies, Exclude_Companies,
           Exclude_Company_Hierarchies, Exclude_Matrix_Organizations,
           Exclude_Pay_Groups, Exclude_Regions, Exclude_Region_Hierarchies,
           Exclude_Supervisory_Organizations, Exclude_Teams,
           Exclude_Custom_Organizations, Include_Roles,
           Include_Management_Chain_Data,
           Include_Multiple_Managers_in_Management_Chain_Data,
           Include_Benefit_Enrollments, Include_Benefit_Eligibility,
           Include_Related_Persons, Include_Qualifications,
           Include_Employee_Review, Include_Goals, Include_Development_Items,
           Include_Skills, Include_Photo, Include_Worker_Documents,
           Include_Transaction_Log_Data, Include_Succession_Profile,
           Include_Talent_Assessment, Include_Employee_Contract_Data,
           Include_Feedback_Received, Include_User_Account, Include_Career,
           Include_Background_Check_Data
   - Leave `api_hint`, `query_params`, `path_params` null for SOAP steps.

── Common rules for ALL steps ───────────────────────────────────────────
9.  `depends_on` is the step `id` this step needs data from (null if independent).
10. `param_map` maps THIS step's inputs to previous step results.
    Format: { "worker_ids": ["step_1.id"] } means "take 'id' from step 1 and use as
    the worker_ids list".
    Use null if this step has no dependencies.
11. `extract_fields` is a list of field names to pull from THIS step's response for
    later steps. Use [] if this is the final step.
12. For "me" / "current user" / "myself" REST queries, step 1 MUST set
    `"path_params": {"ID": "me"}`.
13. `query_params` MUST contain literal URL query parameters from the user prompt
    (e.g. {"search": "B"} for names starting with B). REST only.

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
