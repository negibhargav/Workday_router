import os
import sys
import json
import yaml

# Robust path injection to handle execution from both root directory and script directory
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

try:
    from embedder import IntentEmbedder
    from pinecone_store import PineconeStore
except ModuleNotFoundError as e:
    print(f"[Error] Failed to import local modules. Ensure embedder.py and pinecone_store.py are in the same directory as this script. Detail: {e}")
    sys.exit(1)


def parse_and_index_specs():
    # 1. Initialize Pinecone and FORCE a complete wipe of old 384-dim data
    print("[Pipeline] Initializing Pinecone Store with a fresh wipe...")
    db = PineconeStore(force_reset=True)

    # 2. Initialize your local BGE-small transformer embedder
    print("[Pipeline] Initializing local IntentEmbedder...")
    embedder = IntentEmbedder()

    # 3. Load the newly merged OpenAPI YAML specification
    yaml_path = os.path.join(script_dir, "swagger\\merged_workers.yaml")
    if not os.path.exists(yaml_path):
        # Fallback to current working directory if not alongside the script
        yaml_path = "swagger\\merged_workers.yaml"

    try:
        with open(yaml_path, "r") as f:
            spec_data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[Critical Error] 'merged_workers.yaml' not found at {yaml_path}. Place it in the folder and rerun.")
        sys.exit(1)

    paths = spec_data.get("paths", {})
    
    # Structural arrays to support high-efficiency batch embedding processing
    operation_metadata_list = []
    text_payloads_to_embed = []

    print("[Pipeline] Parsing OpenAPI paths into semantic payloads...")
    
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            operation_id = operation.get("operationId")
            summary = operation.get("summary", "")
            description = operation.get("description", "")
            intent_triggers = operation.get("x-intent-triggers", [])
            
            # Construct a clear context block for BGE-small semantic mapping
            triggers_blob = ", ".join(intent_triggers)
            semantic_text_payload = (
                f"Endpoint: {path} | "
                f"Method: {method.upper()} | "
                f"Summary: {summary} | "
                f"Description: {description} | "
                f"User Phrases: {triggers_blob}"
            )
            
            # Create isolated OpenAPI sub-context specifically for this execution tool definition
            isolated_endpoint_spec = {
                "openapi": "3.0.0",
                "paths": {
                    path: {
                        method: operation
                    }
                }
            }
            
            # Hold structured info to align with vectors later
            operation_metadata_list.append({
                "id": operation_id,
                "metadata": {
                    "module": operation.get("x-module", "workers"),
                    "action": operation.get("x-action", "read"),
                    "scope": operation.get("x-scope", "single"),
                    "path": path,
                    "method": method.upper(),
                    "summary": summary,
                    "raw_openapi_spec": json.dumps(isolated_endpoint_spec)
                }
            })
            
            # Append the text to be sent to the transformer model
            text_payloads_to_embed.append(semantic_text_payload)

    if not text_payloads_to_embed:
        print("[Pipeline] No valid path endpoints found inside the spec configuration.")
        return

    # 4. Generate all normalized 384-dimension vectors in one efficient execution block
    print(f"[Pipeline] Batch-encoding {len(text_payloads_to_embed)} endpoints using local BGE transformer...")
    all_vectors = embedder.encode_intents(text_payloads_to_embed)

    # 5. Pair generated vector values back with their parent structural routing layouts
    prepared_vectors = []
    for idx, op_data in enumerate(operation_metadata_list):
        prepared_vectors.append({
            "id": op_data["id"],
            "values": all_vectors[idx],  # Extracted from our safe Python list conversion inside embedder
            "metadata": op_data["metadata"]
        })

    # 6. Perform atomic batch upsert to your freshly cleared namespace/index
    print(f"[Pipeline] Sending structured vector matrix to Pinecone...")
    db.upsert_vectors(vectors=prepared_vectors, namespace="workday_specs")
    print("\n[Success] Database successfully wiped and loaded with clean local router vectors!")


if __name__ == "__main__":
    parse_and_index_specs()