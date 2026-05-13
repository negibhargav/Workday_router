import json
import sys
from rag.embedder import IntentEmbedder
from rag.pinecone_store import PineconeStore


class WorkdayDispatcher:
    def __init__(self):
        print("Initializing Dispatcher...", file=sys.stderr)
        self.embedder = IntentEmbedder()
        self.store = PineconeStore()

    def route_query(self, user_query, namespace="common", top_k=1):
        """
        Takes a human question, searches Pinecone, and returns the API schema.
        """
        print(f"Routing query: '{user_query}'...", file=sys.stderr)

        # 1. Turn the question into a mathematical vector
        query_vector = self.embedder.encode_intents(user_query)[0]

        # 2. Search the Pinecone database
        results = self.store.query_intent(
            query_vector=query_vector,
            namespace=namespace,
            top_k=top_k
        )

        # 3. Parse and return the best match
        if not results['matches']:
            return {"error": "No matching API found."}

        best_match = results['matches'][0]
        score = best_match['score']
        metadata = best_match['metadata']

        print(
            f"Match found with {score:.2f} confidence: {metadata.get('api_name')}",
            file=sys.stderr
        )

        # Fix parameters JSON
        try:
            parameters = json.loads(metadata.get('parameters', '[]'))
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
    # Test the Dispatcher locally
    dispatcher = WorkdayDispatcher()

    print("\n--- Test 1 ---")  # <-- this is okay since you're not in MCP mode
    test_question_1 = "Give me the list of workers."
    result_1 = dispatcher.route_query(test_question_1)
    print(json.dumps(result_1, indent=2))