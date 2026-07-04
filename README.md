# Workday MCP Router

A **Model Context Protocol (MCP) server** that lets any MCP-compatible AI client (Claude Desktop, Cursor, etc.) query and interact with the Workday REST API using plain natural language.

Instead of knowing which API endpoint to call, the AI simply asks — the router figures out the right endpoint, executes it, and returns a clean answer.

---

## How It Works

```
User Query (natural language)
        │
        ▼
  ┌─────────────┐
  │   Planner   │  GPT-4o-mini decomposes the query into ordered API steps
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  Dispatcher │  Semantic search (Pinecone + BGE embeddings) finds the right
  │   (RAG)     │  Workday REST endpoint for each step
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  Executor   │  Calls the Workday API with OAuth 2.0 auto-refresh
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │ Synthesizer │  GPT-4o-mini produces a clean natural-language answer
  └─────────────┘
```

The system is split into two layers:

| Layer | File | Role |
|---|---|---|
| **MCP Tools** | `src/server.py` | Exposes `ask_workday` (GET) and `execute_workday_action` (POST) to AI clients |
| **Brain** | `src/supervisor.py` | Orchestrates Planner → Executor → Synthesizer pipeline |
| **RAG Engine** | `src/rag/` | Embeds queries and routes them to the correct Workday API |
| **API Client** | `src/services/workday_client.py` | Makes authenticated HTTPS calls to Workday with token auto-refresh |

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.13 |
| [uv](https://github.com/astral-sh/uv) | latest |
| Pinecone account | Free tier works |
| OpenAI API key | Required for Planner & Synthesizer |
| Workday tenant | With an API Client registered for OAuth 2.0 |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-org/workday-mcp-router.git
cd workday-mcp-router
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment variables

Copy the example and fill in your values:

```bash
cp .env.example .env
```

Open `.env` and set:

```env
# ── Pinecone ──────────────────────────────────────────────────────────
PINECONE_API_KEY="your_pinecone_api_key"
PINECONE_INDEX_NAME="workday-router"

# ── Embedding Model ───────────────────────────────────────────────────
EMBEDDING_MODEL="BAAI/bge-small-en-v1.5"

# ── Workday ───────────────────────────────────────────────────────────
WORKDAY_BASE_URL="https://<tenant>-services1.wd101.myworkday.com/api/common/v1/<tenant>"

# OAuth 2.0 client credentials (preferred — enables auto token refresh)
WORKDAY_TOKEN_URL="https://<tenant>-services1.wd101.myworkday.com/ccx/oauth2/<tenant>/token"
WORKDAY_CLIENT_ID="your_client_id"
WORKDAY_CLIENT_SECRET="your_client_secret"

# Fallback: static bearer token (used only if OAuth creds above are not set)
WORKDAY_API_TOKEN=""

# ── OpenAI ────────────────────────────────────────────────────────────
OPENAI_API_KEY="sk-proj-..."

# ── Brain Model (optional) ────────────────────────────────────────────
BRAIN_MODEL="gpt-4o-mini"
```

> **Where to find Workday OAuth credentials:**  
> Workday tenant → **System** → **API Clients for Integrations** → select your registered client app.

---

## Setup: Ingest Workday API Specs into Pinecone

Before the router can answer questions, you must vectorize your Workday OpenAPI specs and load them into Pinecone. This only needs to be done once (or whenever your specs change).

```bash
uv run python scripts/ingest_workday.py
```

This will:
1. Load your parsed API JSON from `data/`
2. Generate semantic embedding vectors using `BAAI/bge-small-en-v1.5`
3. Upsert all vectors into your Pinecone index under the `workday_specs` namespace

---

## Running the MCP Server

### Option A — MCP Inspector (recommended for testing)

Launches an interactive browser UI at `http://localhost:5173`:

```bash
uv run --with mcp mcp dev src/server.py
```

### Option B — Direct server (for production / Claude Desktop)

```bash
uv run python -m src.server
```

The server communicates over **stdin/stdout** (STDIO transport) — this is standard MCP behaviour. It won't print anything on startup; it waits for JSON-RPC messages from a client.

---

## Connecting to Claude Desktop

Add this block to your Claude Desktop config file:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`  
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "workday-router": {
      "command": "C:\\path\\to\\workday-mcp-router\\.venv\\Scripts\\python.exe",
      "args": ["-m", "src.server"],
      "cwd": "C:\\path\\to\\workday-mcp-router"
    }
  }
}
```

Restart Claude Desktop — the tools will appear automatically.

---

## MCP Tools Reference

The server exposes two tools to AI clients:

### `ask_workday`
**Purpose:** Answer read-only (GET) questions about Workday data.

```
natural_language_query: str  →  str (JSON)
```

**Example queries:**
- `"Show me all workers in the Engineering department"`
- `"Who reports to employee 21008?"`
- `"What is Benny Smith's job profile?"`

**Guardrails built in:**
- Blocks write operations (routes them to `execute_workday_action` instead)
- Requires a clear subject — refuses vague queries like "show history"
- Never guesses IDs or fills in blanks

---

### `execute_workday_action`
**Purpose:** Perform write operations (POST/PUT) on Workday data.

```
natural_language_query: str
confirmed: bool = False       →  str (JSON)
```

Requires `confirmed=True` to actually execute — the first call returns a confirmation prompt with the full routing plan so the user can review before committing.

---

## Project Structure

```
workday-mcp-router/
│
├── .env                          # Environment variables (never commit this)
├── pyproject.toml                # Dependencies and project metadata
│
├── swagger/                      # Your Workday OpenAPI spec files (JSON)
│
├── data/                         # Processed API intent files for ingestion
│
├── scripts/
│   └── ingest_workday.py         # One-time ingestion: specs → Pinecone vectors
│
└── src/
    ├── server.py                 # MCP server entry point — registers tools
    ├── supervisor.py             # Orchestrates Planner → Executor → Synthesizer
    │
    ├── brain/                    # Multi-step AI pipeline
    │   ├── planner.py            # LLM decomposes query into ordered API steps
    │   ├── executor.py           # Runs each step, pipes data between them
    │   └── synthesizer.py        # LLM produces final natural-language answer
    │
    ├── rag/                      # Retrieval-Augmented Generation engine
    │   ├── embedder.py           # Encodes queries using BGE embeddings
    │   ├── pinecone_store.py     # Pinecone index connection and namespace logic
    │   └── dispatcher.py        # Routes a query to the best-matching API path
    │
    ├── services/
    │   └── workday_client.py     # HTTPS calls to Workday with OAuth auto-refresh
    │
    └── utils/
        ├── parser.py             # Parses Swagger JSON into vectorizable intent records
        └── logger.py             # Debug logging for routing accuracy
```

---

## Authentication: OAuth 2.0 Auto-Refresh

The Workday client automatically manages your OAuth token:

| Behaviour | Detail |
|---|---|
| **Proactive refresh** | Token is refreshed 60 seconds before it expires |
| **Reactive refresh** | A `401 Unauthorized` response immediately triggers a re-fetch and one retry |
| **Static fallback** | If `WORKDAY_TOKEN_URL`/`CLIENT_ID`/`CLIENT_SECRET` are not set, `WORKDAY_API_TOKEN` is used as-is |

Tokens are fetched using the **OAuth 2.0 client_credentials grant** with credentials sent as **HTTP Basic Auth** (as required by Workday).

---

## Development

### Run a quick smoke test

```bash
uv run python -c "from src.services.workday_client import WorkdayClient; print('OK')"
```

### Test the RAG dispatcher in isolation

```bash
uv run python -m src.rag.dispatcher
```

### Test the full pipeline without MCP

```bash
uv run python -c "
from src.supervisor import run_intelligent_supervisor
print(run_intelligent_supervisor('show me all workers'))
"
```

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `401 Unauthorized` | Expired or invalid token | Fill in `WORKDAY_CLIENT_ID` + `WORKDAY_CLIENT_SECRET` for auto-refresh |
| `400 Bad Request` on token URL | Wrong OAuth credentials | Check your Workday API Client registration; regenerate the client secret if needed |
| `No matching API template found` | Query not covered by ingested specs | Re-run `ingest_workday.py` or add the missing OpenAPI spec to `swagger/` |
| `Port 6277 is in use` | Old MCP Inspector process still running | Run `netstat -ano \| findstr :6277` then `taskkill /PID <pid> /F` |
| `ModuleNotFoundError: src` | Running from wrong directory | Always run commands from the project root, not from `src/` |

---

## Dependencies

| Package | Purpose |
|---|---|
| `mcp` | MCP server framework |
| `openai` | Planner and Synthesizer LLM calls |
| `pinecone` | Vector database for API routing |
| `sentence-transformers` | Local BGE embedding model |
| `fastapi` / `uvicorn` | HTTP layer (if serving via SSE transport) |
| `python-dotenv` | Environment variable loading |
| `requests` | Workday API HTTP calls |

---

## License

MIT