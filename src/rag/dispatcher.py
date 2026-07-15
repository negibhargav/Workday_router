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
        
        api_name = metadata.get('api_name') or best_match.get('id', 'Unknown API')
        method = metadata.get('method', 'GET')
        path = metadata.get('path') or metadata.get('full_path') or ''

        print(
            f"Match found with {score:.2f} confidence: {api_name}",
            file=sys.stderr
        )

        # Extract parameters dynamically out of parameters field or raw_openapi_spec block
        parameters = []
        if "parameters" in metadata:
            try:
                parameters = json.loads(metadata["parameters"])
            except Exception:
                pass

        if not parameters:
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
            "confidence_score": round(score, 4),
            "api_type": metadata.get("api_type", "rest"),
            "type": metadata.get("type"),
            "field": metadata.get("field")
        }

    def route_candidates(self, user_query, namespace="workday_specs", top_k=3):
        """
        Takes a query, normalizes parameter noise, searches Pinecone,
        and returns a list of candidate API schema dictionaries.
        """
        print(f"Routing query for candidates: '{user_query}'...", file=sys.stderr)
        semantic_query = re.sub(r'\d+', 'worker', user_query)

        query_vector = self.embedder.encode_intents(semantic_query)[0]
        
        # Query Pinecone with a larger top_k to find all candidates
        # before de-duplicating by unique path and method.
        search_k = max(15, top_k * 3)
        results = self.store.query_intent(
            query_vector=query_vector,
            namespace=namespace,
            top_k=search_k
        )

        if not results or 'matches' not in results or not results['matches']:
            return []

        candidates = []
        seen_routes = set()
        
        for match in results['matches']:
            score = match.get('score', 0.0)
            metadata = match.get('metadata') or {}
            
            api_name = metadata.get('api_name') or match.get('id', 'Unknown API')
            method = metadata.get('method', 'GET')
            path = metadata.get('path') or metadata.get('full_path') or ''

            # De-duplicate by unique (method, path) to ensure similar/duplicate intent
            # phrasings for the same route do not crowd out other compatible routes.
            route_key = (method.upper(), path.lower())
            if route_key in seen_routes:
                continue
            seen_routes.add(route_key)

            parameters = []
            if "parameters" in metadata:
                try:
                    parameters = json.loads(metadata["parameters"])
                except Exception:
                    pass

            if not parameters:
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

            candidates.append({
                "api_name": api_name,
                "method": method,
                "full_path": path,
                "path": path,
                "parameters": parameters,
                "confidence_score": round(score, 4),
                "api_type": metadata.get("api_type", "rest"),
                "type": metadata.get("type"),
                "field": metadata.get("field")
            })

            if len(candidates) >= top_k:
                break

        return candidates

    def route_soap_service_and_fields(self, query, threshold=0.35):
        """
        Queries Pinecone in the 'workday_soap_specs' namespace to find:
        1. The best matching SOAP service.
        2. Any matching response group flags.
        """
        print(f"Routing SOAP query: '{query}'...", file=sys.stderr)
        candidates = self.route_candidates(query, namespace="workday_soap_specs", top_k=10)
        
        service_name = None
        best_service_score = -1.0
        response_fields = []
        
        for cand in candidates:
            cand_type = cand.get("type")
            score = cand.get("confidence_score", 0.0)
            
            if cand_type == "service":
                if score > best_service_score:
                    best_service_score = score
                    service_name = cand.get("api_name")
            elif cand_type == "response_group":
                if score >= threshold:
                    field = cand.get("field")
                    if field and field not in response_fields:
                        response_fields.append(field)
                        
        print(f"SOAP Routing match: service={service_name} (score={best_service_score:.4f}), response_fields={response_fields}", file=sys.stderr)
        return {
            "service": service_name,
            "include_fields": response_fields,
            "confidence_score": best_service_score,
            "candidates": candidates
        }


if __name__ == "__main__":
    dispatcher = WorkdayDispatcher()
    print("\n--- Test Running ---", file=sys.stderr)
    test_question_1 = "who reports to employee ID : 21008"
    result_1 = dispatcher.route_query(test_question_1)
    print(json.dumps(result_1, indent=2))