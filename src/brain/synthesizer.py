"""
synthesizer.py — Final answer generator.

Takes the original user query, the execution plan, and all step results from the
executor's context, then calls OpenAI to produce a clean natural-language answer.

The synthesizer is the last stage of the Planner → Executor → Synthesizer pipeline.
"""

import json
import os
import sys
from openai import OpenAI

_SYSTEM_PROMPT = """You are a helpful Workday assistant.

The user asked a question. To answer it, a backend system made one or more Workday API 
calls. You are given the results of those calls.

Your job:
1. Read all the step results carefully.
2. Answer the user's original question directly and clearly.
3. Present data in a readable format — use bullet points or a table when listing people.
4. If some steps failed or returned no data, acknowledge the gap honestly.
5. Do NOT mention APIs, steps, JSON, or technical details. 
   The user just wants their answer.
6. Be concise — don't pad with filler phrases.
"""


class Synthesizer:
    """Produces a clean natural-language answer from multi-step execution results."""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def synthesize(self, user_query: str, plan: dict, context: dict) -> str:
        """
        Generate the final answer.

        Args:
            user_query: the original user question
            plan:       the plan dict from Planner (for goal context)
            context:    the accumulated step results from Executor

        Returns:
            A natural-language string answering the user's question.
        """
        print(f"[Synthesizer] Generating final answer...", file=sys.stderr)

        # Build a structured summary of what each step returned
        step_summaries = self._build_step_summaries(plan, context)

        user_message = (
            f"User question: {user_query}\n\n"
            f"Goal of the lookup: {plan.get('goal', 'answer the user question')}\n\n"
            f"Data collected from Workday:\n{step_summaries}"
        )

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,    # Slight creativity for readable prose, but mostly factual
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
        )

        answer = response.choices[0].message.content.strip()
        print(f"[Synthesizer] Answer generated ({len(answer)} chars)", file=sys.stderr)
        return answer

    def _build_step_summaries(self, plan: dict, context: dict) -> str:
        """
        Build a concise text block describing what each step fetched.
        Keeps the prompt focused — only sends what the synthesizer actually needs.
        """
        parts = []

        for step in plan.get("steps", []):
            step_key = f"step_{step['id']}"
            step_data = context.get(step_key, {})

            header = f"Step {step['id']} — {step['intent']}"
            api_called = step_data.get("api_called", "unknown")

            if step_data.get("error"):
                parts.append(
                    f"{header}\n"
                    f"  Status: FAILED — {step_data['error']}"
                )
                continue

            raw = step_data.get("raw_response", "")
            extracted = step_data.get("extracted", {})

            # Prefer the clean extracted fields if available, otherwise use raw (truncated)
            if extracted:
                data_block = json.dumps(extracted, indent=2)
            elif raw:
                # Trim raw to a reasonable size for the synthesis prompt
                data_block = raw[:3000] + ("..." if len(raw) > 3000 else "")
            else:
                data_block = "(no data returned)"

            parts.append(
                f"{header}\n"
                f"  API: {api_called}\n"
                f"  Data:\n{data_block}"
            )

        return "\n\n".join(parts) if parts else "No step data available."
