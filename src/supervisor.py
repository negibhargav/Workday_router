"""
supervisor.py — Orchestrates the Planner → Executor → Synthesizer pipeline.

This is the entry point called by server.py's ask_workday_assistant tool.
It replaces the old single-step supervisor with a full multi-step brain.

Flow:
    1. Planner decomposes the user query into ordered steps
    2. Executor runs each step sequentially, piping outputs between them
    3. Synthesizer produces a clean natural-language answer

Falls back gracefully if planning or any step fails.
"""

import json
import os
import sys
from dotenv import load_dotenv

_sup_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
load_dotenv(os.path.join(_sup_root, ".env"))

from src.brain.planner import Planner
from src.brain.executor import Executor
from src.brain.synthesizer import Synthesizer

# Singleton instances — created once, reused across tool calls (expensive to init)
_planner    = None
_executor   = None
_synthesizer = None


def _get_brain():
    """Lazy-initialize the brain components once per server lifetime."""
    global _planner, _executor, _synthesizer

    if _planner is None:
        model = os.getenv("BRAIN_MODEL", "gpt-4o-mini")
        print(f"[Supervisor] Initializing Brain (model={model})...", file=sys.stderr)
        _planner     = Planner(model=model)
        _executor    = Executor(model=model)
        _synthesizer = Synthesizer(model=model)
        print("[Supervisor] Brain ready.", file=sys.stderr)

    return _planner, _executor, _synthesizer


def run_intelligent_supervisor(user_prompt: str) -> str:
    """
    Main entry point — called by server.py's ask_workday_assistant MCP tool.

    Args:
        user_prompt: the user's natural language question

    Returns:
        A natural-language string answering the question,
        or a JSON error object if the pipeline fails.
    """
    print(f"\n[Supervisor] ══════════════════════════════════════", file=sys.stderr)
    print(f"[Supervisor] Query: '{user_prompt}'", file=sys.stderr)

    planner, executor, synthesizer = _get_brain()

    # ── PHASE 1: Plan ────────────────────────────────────────────────────────
    try:
        plan = planner.plan(user_prompt)
    except Exception as e:
        print(f"[Supervisor] Planning failed: {e}", file=sys.stderr)
        return json.dumps({
            "status": "error",
            "phase":  "planning",
            "message": f"Could not decompose query into steps: {str(e)}"
        }, indent=2)

    # ── PHASE 2: Execute ─────────────────────────────────────────────────────
    try:
        context = executor.run(plan)
    except Exception as e:
        print(f"[Supervisor] Execution failed: {e}", file=sys.stderr)
        return json.dumps({
            "status":  "error",
            "phase":   "execution",
            "message": f"Step execution failed: {str(e)}",
            "plan":    plan,
        }, indent=2)

    # Check if ALL steps failed — don't bother synthesizing empty data
    all_failed = all(
        ctx.get("error") for ctx in context.values()
    )
    if all_failed and context:
        errors = {k: v.get("error") for k, v in context.items()}
        return json.dumps({
            "status":  "error",
            "phase":   "execution",
            "message": "All API steps failed. No data was retrieved.",
            "errors":  errors,
        }, indent=2)

    # ── PHASE 3: Synthesize ──────────────────────────────────────────────────
    try:
        answer = synthesizer.synthesize(
            user_query=user_prompt,
            plan=plan,
            context=context,
        )
        return answer

    except Exception as e:
        print(f"[Supervisor] Synthesis failed: {e}", file=sys.stderr)
        # Best-effort: return raw step data as JSON if synthesis blows up
        return json.dumps({
            "status":  "error",
            "phase":   "synthesis",
            "message": f"Could not generate final answer: {str(e)}",
            "raw_context": {
                k: v.get("raw_response", "")[:500]
                for k, v in context.items()
            },
        }, indent=2)
