import json
import re
import sys

# Correct absolute imports to align with your project layout
from src.rag.embedder import IntentEmbedder
from src.rag.pinecone_store import PineconeStore


class WorkdayDispatcher:
    def __init__(self):
        print("Initializing Dispatcher...", file=sys.stderr)
        self.embedder = IntentEmbedder()
        self.store = PineconeStore()

    def route_query(self, user_query, namespace="workday_specs", top_k=1):
        """
        Takes a human question, normalizes parameter noise, searches Pinecone, 
        and returns the correct API schema matching template.
        """
        print(f"Routing query: '{user_query}'...", file=sys.stderr)

        # --- CRITICAL RAG OPTIMIZATION: QUERY DE-NOISING ---
        # Replace specific raw numbers with a uniform token so they don't skew 
        # the embedding vector away from subresource collection intents.
        # Example: "who reports to employee ID : 21008" -> "who reports to employee ID : worker"
        semantic_query = re.sub(r'\d+', 'worker', user_query)
        print(f"De-noised semantic query for embedding match: '{semantic_query}'", file=sys.stderr)

        # 1. Turn the normalized intent question into a mathematical vector representation
        query_vector = self.embedder.encode_intents(semantic_query)[0]

        # 2. Search the vector database
        results = self.store.query_intent(
            query_vector=query_vector,
            namespace=namespace,
            top_k=top_k
        )

        # 3. Parse and return the best match securely
        if not results or 'matches' not in results or not results['matches']:
            return {"error": "No matching API template found in vector database."}

        best_match = results['matches'][0]
        score = best_match.get('score', 0.0)
        metadata = best_match.get('metadata') or {}
        
        api_name = best_match.get('id', 'Unknown API')
        method = metadata.get('method', 'GET')
        path = metadata.get('path', '')

        print(
            f"Match found with {score:.2f} confidence: {api_name}",
            file=sys.stderr
        )

        # Extract parameters dynamically out of the raw_openapi_spec block
        parameters = []
        raw_spec_str = metadata.get('raw_openapi_spec')
        if raw_spec_str:
            try:
                spec_json = json.loads(raw_spec_str)
                paths_obj = spec_json.get("paths", {})
                path_obj = paths_obj.get(path, {})
                method_obj = path_obj.get(method.lower(), {})
                parameters = method_obj.get("parameters", [])
            except Exception as e:
                print(f"Warning parsing parameters from spec: {e}", file=sys.stderr)
                parameters = []

        return {
            "api_name": api_name,
            "method": method,
            "full_path": path,
            "path": path,
            "parameters": parameters,
            "confidence_score": round(score, 4)
        }


if __name__ == "__main__":
    dispatcher = WorkdayDispatcher()
    print("\n--- Test Running ---", file=sys.stderr)
    test_question_1 = "who reports to employee ID : 21008"
    result_1 = dispatcher.route_query(test_question_1)
    print(json.dumps(result_1, indent=2))