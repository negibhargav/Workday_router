workday-mcp-router/
├── .env                        # PINECONE_API_KEY, WORKDAY_BASE_URL, etc.
├── swagger/                    # Store your common_v1.json and other categories
│
├── src/
│   ├── server.py               # MCP Server Entry Point (registers the router)
│   │
│   ├── tools/                  # MCP Tools Layer
│   │   ├── __init_ _.py
│   │   └── router_tool.py      # The "Main Tool" the LLM sees (connects to RAG)
│   │
│   ├── rag/                    # The RAG Engine (The "Brain")
│   │   ├── __init_ _.py
│   │   ├── pinecone_store.py   # Pinecone connection and Namespace logic
│   │   ├── embedder.py         # Your lightweight vectorizer logic
│   │   └── dispatcher.py       # Logic to return minified schemas/paths
│   │
│   ├── services/               # The Execution Layer (The "Arms")
│   │   ├── __init_ _.py
│   │   └── workday_client.py   # Handles the actual HTTPS calls to Workday
│   │
│   └── utils/
│       ├── parser.py           # Ingestion script to turn Swagger into vectors
│       └── logger.py           # For debugging routing accuracy
│
├── scripts/
│   └── ingest_workday.py       # CLI script to run Phase 1 (Ingestion)
└── pyproject.toml              # Dependencies (mcp, pinecone-client, etc.)