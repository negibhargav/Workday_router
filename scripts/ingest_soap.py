import os
import sys
import json
from pathlib import Path

# Path injection to handle execution from various directories
project_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.rag.embedder import IntentEmbedder
from src.rag.pinecone_store import PineconeStore

def ingest_soap_data(input_file="swagger/soap_specs.json", namespace="workday_soap_specs"):
    print("[Ingest SOAP] Initializing SOAP Ingestion Pipeline...")
    
    embedder = IntentEmbedder()
    store = PineconeStore()
    
    input_path = Path(project_root) / input_file
    if not input_path.exists():
        print(f"[Error] {input_file} not found. Please create it first.")
        return
        
    with open(input_path, "r", encoding="utf-8") as f:
        soap_specs = json.load(f)
        
    print(f"[Ingest SOAP] Loaded {len(soap_specs)} SOAP operations/definitions. Generating vectors...")
    
    vectors_to_upsert = []
    
    for item in soap_specs:
        item_id = item.get("id")
        api_name = item.get("api_name")
        item_type = item.get("type") # "service" or "response_group"
        intents = item.get("intent_triggers", [])
        params = item.get("parameters", [])
        params_str = json.dumps(params)
        
        # Determine the target field if it's a response group
        target_field = item.get("field", "")
        
        for idx, intent in enumerate(intents):
            # Generate the vector embedding using our local embedder
            vector_values = embedder.encode_intents(intent)[0]
            
            # Create a unique ID for Pinecone
            vector_id = f"{item_id}-intent-{idx}"
            
            # Formulate metadata
            metadata = {
                "api_name": api_name,
                "method": "SOAP",
                "api_type": "soap",
                "type": item_type,
                "field": target_field,
                "parameters": params_str,
                "trigger_text": intent
            }
            
            vectors_to_upsert.append((vector_id, vector_values, metadata))
            
    print(f"[Ingest SOAP] Generated {len(vectors_to_upsert)} total intent vectors. Pushing to Pinecone (namespace='{namespace}')...")
    
    batch_size = 50
    for i in range(0, len(vectors_to_upsert), batch_size):
        batch = vectors_to_upsert[i:i + batch_size]
        store.upsert_vectors(batch, namespace=namespace)
        
    print(f"[Ingest SOAP] Success! Ingested {len(vectors_to_upsert)} vectors into '{namespace}' namespace.")

if __name__ == "__main__":
    ingest_soap_data()
