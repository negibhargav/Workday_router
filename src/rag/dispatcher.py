import json
from src.rag.embedder import IntentEmbedder
from src.rag.pinecone_store import PineconeStore

class WorkdayDispatcher:
    def __init__(self):
        print("Initializing Dispatcher...")
        self.embedder = IntentEmbedder()
        self.store = PineconeStore()
        
    def route_query(self, user_query, namespace="common", top_k=1):
        """
        Takes a human question, searches Pinecone, and returns the API schema.
        """
        print(f"Routing query: '{user_query}'...")
        
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
        
        print(f"Match found with {score:.2f} confidence: {metadata.get('api_name')}")
        
        # Re-parse the parameters string back into a Python list
        try:
            parameters = json.loads(metadata.get('parameters', '[]'))
        except json.JSONDecodeError:
            parameters = []
            
        # The clean, minified payload for the LLM
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
    
    print("\n--- Test 1 ---")
    test_question_1 = "Give me the list of workers."
    result_1 = dispatcher.route_query(test_question_1)
    print(json.dumps(result_1, indent=2))

    # print("\n--- Test 2 ---")
    # test_question_2 = "Who reports to worker ID 12345?"
    # result_2 = dispatcher.route_query(test_question_2)
    # print(json.dumps(result_2, indent=2))
    
    # print("\n--- Test 3 ---")
    # test_question_3 = "Who is reporting to me?"
    # result_3 = dispatcher.route_query(test_question_3)
    # print(json.dumps(result_3, indent=2))
    
    # print("\n--- Test 4 ---")
    # test_question_4 = "what is my costcenter?"
    # result_4 = dispatcher.route_query(test_question_4)
    # print(json.dumps(result_4, indent=2))
    
    # print("\n--- Test 5 ---")
    # test_question_5 = "who is reporting to steven?"
    # result_5 = dispatcher.route_query(test_question_5)
    # print(json.dumps(result_5, indent=2))
    
    # print("\n--- Test 6 ---")
    # test_question_6 = "what are pending task for me?"
    # result_6 = dispatcher.route_query(test_question_6)
    # print(json.dumps(result_6, indent=2))
    
    # print("\n--- Test 7 ---")
    # test_question_7 = "am I part of Global Modern company?"
    # result_7 = dispatcher.route_query(test_question_7)
    # print(json.dumps(result_7, indent=2))
    