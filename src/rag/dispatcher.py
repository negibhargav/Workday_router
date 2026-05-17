import json
import sys

# Correct absolute imports to align with your new src/ directory layout
from src.rag.embedder import IntentEmbedder
from src.rag.pinecone_store import PineconeStore


class WorkdayDispatcher:
    def __init__(self):
        print("Initializing Dispatcher...", file=sys.stderr)
        self.embedder = IntentEmbedder()
        self.store = PineconeStore()

    def route_query(self, user_query, namespace="common", top_k=1):
        """
        Takes a human question, searches Pinecone, and returns the API schema matching template.
        """
        print(f"Routing query: '{user_query}'...", file=sys.stderr)

        # 1. Turn the question into a mathematical vector representation
        query_vector = self.embedder.encode_intents(user_query)[0]

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
        
        # Defensive fallback if metadata block is completely absent or empty
        metadata = best_match.get('metadata') or {}

        print(
            f"Match found with {score:.2f} confidence: {metadata.get('api_name', 'Unknown API')}",
            file=sys.stderr
        )

        # Safely extract and deserialize the embedded JSON string schema parameters
        try:
            param_data = metadata.get('parameters', '[]')
            parameters = json.loads(param_data) if isinstance(param_data, str) else param_data
        except json.JSONDecodeError:
            parameters = []

        return {
            "api_name": metadata.get('api_name'),
            "method": metadata.get('method'),
            "full_path": metadata.get('full_path'),
            "parameters": parameters,
            "confidence_score": round(score, 4)
        }


if __name__ == "__main__":
    # Test the Dispatcher locally using execution context tools
    dispatcher = WorkdayDispatcher()

    print("\n--- Test 1 Running ---", file=sys.stderr)
    test_question_1 = "Give me the list of workers."
    result_1 = dispatcher.route_query(test_question_1)
    print(json.dumps(result_1, indent=2))