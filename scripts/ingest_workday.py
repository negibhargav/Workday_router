import json
import os
from pathlib import Path

# Import your core RAG modules
from src.rag.embedder import IntentEmbedder
from src.rag.pinecone_store import PineconeStore

def ingest_data(input_file="data/processed_intents/workers_apis.json", namespace="workday_specs"):
    print("Initializing Ingestion Pipeline...")
    
    # 1. Boot up the brain and the database
    embedder = IntentEmbedder()
    store = PineconeStore()
    
    # 2. Load the parsed APIs
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: {input_file} not found. Please run src/utils/parser.py first.")
        return
        
    with open(input_path, "r", encoding="utf-8") as f:
        apis = json.load(f)
        
    print(f"Loaded {len(apis)} API schemas. Generating vectors...")
    
    vectors_to_upsert = []
    
    # 3. Iterate over every API and its Intents
    for api in apis:
        api_id = api.get("id", "unknown_id")
        api_name = api.get("api_name", "unknown_api")
        intents = api.get("intent_triggers", [])
        
        # Pinecone requires metadata values to be strings, numbers, or lists of strings.
        # We must stringify the nested parameter JSON so Pinecone accepts it.
        params_str = json.dumps(api.get("parameters", []))
        
        for idx, intent in enumerate(intents):
            # Embed the human-readable sentence
            vector_values = embedder.encode_intents(intent)[0]
            
            # Create a unique ID for this specific intent vector
            vector_id = f"{api_id}-intent-{idx}"
            
            # The Minified Payload (This is what the LLM will eventually read)
            metadata = {
                "api_name": api_name,
                "method": api.get("method", "GET"),
                "path": api.get("full_path", ""),
                "full_path": api.get("full_path", ""),
                "parameters": params_str,
                "trigger_text": intent  # Helpful for debugging later
            }
            
            # Add to our staging list
            vectors_to_upsert.append((vector_id, vector_values, metadata))
            
    print(f"Generated {len(vectors_to_upsert)} total intent vectors. Pushing to Pinecone...")
            
    # 4. Upsert in batches (Pinecone best practice: max 100-200 per batch)
    batch_size = 100
    for i in range(0, len(vectors_to_upsert), batch_size):
        batch = vectors_to_upsert[i:i + batch_size]
        store.upsert_vectors(batch, namespace=namespace)
        
    print(f"Success! Ingested {len(vectors_to_upsert)} vectors into the '{namespace}' namespace.")

if __name__ == "__main__":
    ingest_data()