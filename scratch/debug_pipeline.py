"""
debug_pipeline.py -- Runs the full planner->executor pipeline with verbose output.
Set PYTHONUTF8=1 or use ASCII-only output to avoid cp1252 errors on Windows.
"""
import json
import os
import sys
import logging

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brain.planner import Planner
from src.brain.executor import Executor


def run_debug(query: str):
    print(f"\n{'='*60}")
    print(f"QUERY: {query}")
    print(f"{'='*60}\n")

    model = os.getenv("BRAIN_MODEL", "gpt-4o-mini")

    # PHASE 1: Plan
    print("[ PHASE 1: PLANNER ]")
    planner = Planner(model=model)
    plan = planner.plan(query)
    print(f"\nGoal : {plan['goal']}")
    print(f"Steps: {len(plan['steps'])}")
    for s in plan["steps"]:
        print(f"  Step {s['id']}:")
        print(f"    intent        : {s['intent']}")
        print(f"    api_hint      : {s['api_hint']}")
        print(f"    depends_on    : {s['depends_on']}")
        print(f"    param_map     : {s['param_map']}")
        print(f"    extract_fields: {s['extract_fields']}")

    # PHASE 2: Execute step-by-step
    print(f"\n[ PHASE 2: EXECUTOR ]")
    executor = Executor(model=model)
    context = {}

    for step in plan["steps"]:
        step_key = f"step_{step['id']}"
        print(f"\n  -- Step {step['id']}: {step['intent']}")

        resolved = executor._resolve_params(step.get("param_map"), context)
        print(f"    Resolved params : {resolved}")

        route = executor.dispatcher.route_query(step["api_hint"])
        print(f"    RAG match       : {route.get('method')} {route.get('full_path')} (score={route.get('confidence_score')})")

        result = executor._run_step(step, context)
        context[step_key] = result

        print(f"    API called      : {result.get('api_called')}")
        print(f"    Extracted fields: {json.dumps(result.get('extracted', {}))}")

        if result.get("error"):
            print(f"    !! ERROR        : {result['error']}")
        else:
            raw = result.get("raw_response", "")
            preview = raw[:400] if isinstance(raw, str) else str(raw)[:400]
            print(f"    Raw response (first 400 chars):")
            print(f"      {preview}")

    print(f"\n{'='*60}")
    print("FINAL CONTEXT:")
    for k, v in context.items():
        print(f"  {k}: extracted={v.get('extracted')}, error={v.get('error')}")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "who reports to worker Joy Banks"
    run_debug(query)
