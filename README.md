# Workday MCP Router

A **Model Context Protocol (MCP) server** that lets any MCP-compatible AI client (Claude Desktop, Cursor, etc.) query and interact with the Workday REST and SOAP APIs using plain natural language.

Instead of knowing which API endpoint to call, the AI simply asks — the router figures out the right endpoint/service, executes it, and returns a clean answer.

---

## How It Works

```
User Query (natural language)
        │
        ▼
  ┌─────────────┐
  │   Planner   │  Decomposes the query into ordered REST and SOAP API steps
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  Dispatcher │  Queries Pinecone database to dynamically locate endpoints
  │   (RAG)     │  for REST (workday_specs) and SOAP (workday_soap_specs)
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  Executor   │  Runs REST calls (WorkdayClient) or SOAP services (Zeep wrappers),
  │             │  handling parameter mapping and OAuth token auto-refresh
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │ Synthesizer │  GPT-4o-mini produces a clean natural-language answer
  └─────────────┘
```

The system is split into multiple layers:

| Layer | File / Directory | Role |
|---|---|---|
| **MCP Tools** | `src/server.py` | Exposes `ask_workday` and `execute_workday_action` to AI clients |
| **Brain** | `src/supervisor.py` | Orchestrates Planner → Executor → Synthesizer pipeline |
| **RAG Engine** | `src/rag/` | Embeds queries and routes them to correct REST/SOAP endpoints |
| **API Client** | `src/services/` | REST HTTP calls (`workday_client.py`) and SOAP clients (`Worker.py`, `hire.py`) |
| **Log Folder** | `src/log/` | Centralized project logs (`workday_refresh.log`) |

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.13 |
| [uv](https://github.com/astral-sh/uv) or pip | Latest |
| Pinecone account | Free tier works |
| OpenAI API key | Required for Planner & Synthesizer |
| Workday tenant | With an API Client registered for OAuth 2.0 (Authorization Code Flow) |

---

## Installation & Setup

### 1. Clone the repository
```bash
git clone https://github.com/your-org/workday-mcp-router.git
cd workday-mcp-router
```

### 2. Install dependencies
Using `uv` (recommended):
```bash
uv sync
```
Using standard `pip`:
```bash
pip install -r requirements.txt
```

### 3. Configure environment variables
Copy the example file and fill in your details:
```bash
cp .env.example .env
```
Open `.env` and set:
```env
# Pinecone Configuration
PINECONE_API_KEY="your_pinecone_api_key"
PINECONE_INDEX_NAME="workday-router"

# Workday OAuth configuration (Authorization Code flow)
WORKDAY_BASE_URL="https://<tenant>-services1.wd101.myworkday.com"
WORKDAY_TOKEN_URL="https://<tenant>-services1.wd101.myworkday.com/ccx/oauth2/<tenant>/token"
WORKDAY_AUTH_URL="https://<tenant>.wd101.myworkday.com/wday/authgwy/<tenant>/authorize"
WORKDAY_CLIENT_ID="your_client_id"
WORKDAY_CLIENT_SECRET="your_client_secret"
WORKDAY_REDIRECT_URI="http://localhost:8742/callback"

# SOAP Credentials (ISU Account)
WORKDAY_ISU_USERNAME="soap_user@<tenant>"
WORKDAY_ISU_PASSWORD="your_password_here"

# OpenAI API Configuration
OPENAI_API_KEY="sk-proj-..."
BRAIN_MODEL="gpt-4o-mini"
```

### 4. Interactive OAuth Login (First-Time Setup)
Since Workday OAuth uses the **Authorization Code Flow**, you must execute the login tool interactively to authenticate for the first time:
```bash
uv run python src/tools/Refresh_token.py login
```
*This command launches a local web browser page, captures the callback authentication code, and creates a local credentials cache file `.workday_tokens.json` in the root of the project.*

* **Checking Token Info:**
  ```bash
  uv run python src/tools/Refresh_token.py info
  ```
* **Forced Token Refresh:**
  ```bash
  uv run python src/tools/Refresh_token.py refresh
  ```

---

## Vector Spec Ingestion (RAG)

Before routing queries, you must vectorize your REST OpenAPI and SOAP specifications and load them into Pinecone.

### Ingest REST APIs (namespace: `workday_specs`)
```bash
uv run python scripts/ingest_workday.py
```

### Ingest SOAP APIs (namespace: `workday_soap_specs`)
```bash
uv run python scripts/ingest_soap.py
```

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
*The server communicates over stdin/stdout (STDIO transport) waiting for JSON-RPC messages from the client.*

---

## Connecting to Claude Desktop

Add this block to your Claude Desktop config file:

* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

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
Restart Claude Desktop to load the tools.

---

## Project Structure

```
workday-mcp-router/
│
├── .env                          # Environment variables
├── pyproject.toml                # Dependencies configuration (uv project)
├── requirements.txt              # Standard python requirements file
├── .workday_tokens.json          # Cached active OAuth tokens (never commit this)
│
├── swagger/                      
│   ├── common_v1.json            # Raw REST specification
│   └── soap_specs.json           # Defined SOAP operations & intent triggers
│
├── scripts/
│   ├── ingest_workday.py         # Ingestion script for REST specs -> Pinecone
│   └── ingest_soap.py            # Ingestion script for SOAP specs -> Pinecone
│
└── src/
    ├── server.py                 # MCP server entry point
    ├── supervisor.py             # Orchestrates Planner -> Executor pipeline
    │
    ├── brain/                    
    │   ├── planner.py            # Planner (RAG-agnostic REST/SOAP decomposition)
    │   ├── executor.py           # Runs Rest/Soap calls and performs parameter mappings
    │   └── synthesizer.py        # Synthesizes execution context into answers
    │
    ├── rag/                      
    │   ├── embedder.py           # BGE embedding generator
    │   ├── pinecone_store.py     # Pinecone index interface
    │   └── dispatcher.py         # Routes query to REST or SOAP namespaces in Pinecone
    │
    ├── services/                 
    │   ├── workday_client.py     # REST client wrapper
    │   ├── Worker.py             # Worker SOAP Service wrapper
    │   └── hire.py               # Hire SOAP Service wrapper
    │
    ├── log/                      
    │   └── workday_refresh.log   # Token auto-refresh action logs
    │
    └── utils/
        └── token_limiter.py      # Cleans payload values to save token limits
```

---

## License

MIT