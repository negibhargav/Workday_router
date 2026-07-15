"""
api.py — FastAPI HTTP server wrapping the Workday Router brain.

Run with:
    .venv\\Scripts\\python.exe -m uvicorn src.api:app --reload --port 8000

Then open: http://localhost:8000/docs  (Swagger UI)
"""

import json
import os
import sys
import time
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ── Path setup ────────────────────────────────────────────────────────────────
_HERE        = os.path.dirname(os.path.abspath(__file__))        # src/
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, ".."))       # project root

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Non-interactive mode — no browser popups
os.environ["WORKDAY_NON_INTERACTIVE"] = "true"

# ── Brain import ──────────────────────────────────────────────────────────────
from src.supervisor import run_intelligent_supervisor, _get_brain  # noqa: E402


# ── Query logging ─────────────────────────────────────────────────────────────
# Every /ask call gets appended here as one JSON line: query, plan, RAG matches
# (including top-k candidates, not just the winner), answer, and any error.
# This is what you hand back for review instead of pasting responses manually.
LOG_PATH = Path(_PROJECT_ROOT) / "test_logs" / "query_log.jsonl"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _log_query(query, plan=None, rag_matches=None, answer=None, error=None):
    entry = {
        "timestamp": time.time(),
        "query": query,
        "plan": plan,
        "rag_matches": rag_matches or [],
        "answer": answer,
        "error": error,
    }
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as log_exc:
        # Logging should never break the actual request.
        print(f"[API] WARNING: failed to write query log: {log_exc}")


# ── Lifespan: token refresh on startup ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Refresh Workday token on startup, then start 30-min background timer."""
    print("[API] Starting up — refreshing Workday token...")
    try:
        from src.tools.Refresh_token import force_refresh
        force_refresh()
        print("[API] Token refreshed successfully.")
    except Exception as exc:
        print(f"[API] WARNING: Token refresh on startup failed: {exc}")

    # Start the 30-minute background refresh timer
    import threading, time as _time
    def _timer():
        while True:
            _time.sleep(1800)
            try:
                from src.tools.Refresh_token import force_refresh
                force_refresh()
                print("[API] Background token refresh successful.")
            except Exception as exc:
                print(f"[API] Background token refresh failed: {exc}")

    threading.Thread(target=_timer, daemon=True).start()

    yield  # server is running


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Workday Router API",
    description="Natural-language interface to Workday REST & SOAP APIs via a RAG-powered brain.",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow all origins for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "Workday Router API"}


@app.get("/ask", tags=["Read"])
def ask_workday(query: str, debug: bool = False):
    """
    Ask any read-only question about Workday data in plain English.

    Examples:
    - "Who reports to employee 21431?"
    - "What is the job title of John Smith?"
    - "List all workers in the Finance department."

    Query params:
    - debug: if true, includes the full plan and raw execution context
             in the response (useful while testing, noisy otherwise).
    """
    plan = None
    context = None

    try:
        planner, executor, synthesizer = _get_brain()
        plan = planner.plan(query)

        if not plan or "steps" not in plan:
            _log_query(query, plan=plan, error="Planner failed to generate a valid execution plan.")
            raise HTTPException(status_code=400, detail="Planner failed to generate a valid execution plan.")

        # Block write operations from this read-only endpoint
        has_write = any(
            step.get("method", "GET").upper() != "GET"
            for step in plan.get("steps", [])
        )
        if has_write:
            _log_query(query, plan=plan, error="Write operation blocked on read-only endpoint.")
            raise HTTPException(
                status_code=400,
                detail="This query requires a write operation, which is not supported by this API.",
            )

        context = executor.run(plan)
        answer = synthesizer.synthesize(
            user_query=query,
            plan=plan,
            context=context,
        )

        # Collect RAG match details from the execution context.
        # top_k_candidates surfaces the runner-up matches (not just the winner)
        # so low-margin / ambiguous routing decisions are visible in the log
        # instead of only showing a single confidence_score in isolation.
        rag_matches = []
        for step_key, step_result in context.items():
            if isinstance(step_result, dict) and "rag_route" in step_result:
                rag_matches.append({
                    "step": step_key,
                    "matched_route": step_result.get("rag_route"),
                    "api_name": step_result.get("api_name"),
                    "confidence_score": step_result.get("confidence_score"),
                    "top_k_candidates": step_result.get("top_k_candidates"),
                    "executed_url": step_result.get("api_called"),
                })

        # Attempt to parse answer as JSON for clean nesting, else return as string
        try:
            parsed_answer = json.loads(answer)
        except (json.JSONDecodeError, TypeError):
            parsed_answer = answer

        _log_query(query, plan=plan, rag_matches=rag_matches, answer=parsed_answer)

        result = {
            "answer": parsed_answer,
            "rag_matches": rag_matches,
        }
        if debug:
            result["plan"] = plan
            result["raw_context"] = context

        return result

    except HTTPException:
        raise
    except Exception as exc:
        # Log full detail server-side; don't leak internals to the client.
        print(f"[API] /ask failed for query='{query}': {exc}")
        _log_query(query, plan=plan, error=str(exc))
        raise HTTPException(status_code=500, detail="Internal error processing query.")


@app.get("/plan", tags=["Debug"])
def preview_plan(query: str):
    """
    Preview the execution plan for a query without running it.
    Useful for debugging what the Planner LLM will do.
    """
    try:
        planner, _, _ = _get_brain()
        plan = planner.plan(query)
        return plan
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))