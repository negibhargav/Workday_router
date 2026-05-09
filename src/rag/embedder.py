import os
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

class IntentEmbedder:
    def __init__(self, model_name=None):
        # Pulls the BGE model from your .env file
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        print(f"Loading embedding model: {self.model_name}...")
        
        # BGE v1.5 models include optimizations that work seamlessly here
        self.model = SentenceTransformer(self.model_name)
        print("Model loaded successfully.")

    def encode_intents(self, intents):
        """
        Converts a list of strings (or a single string) into a list of vectors.
        BGE-small outputs 384 dimensions.
        """
        if isinstance(intents, str):
            intents = [intents]
        
        # BGE models work best for retrieval when queries are passed directly
        embeddings = self.model.encode(intents, normalize_embeddings=True)
        
        # Convert from numpy array to a standard Python list for Pinecone
        return embeddings.tolist()

if __name__ == "__main__":
    # Test the BGE embedder
    embedder = IntentEmbedder()
    test_query = "Who reports to worker 123?"
    
    vector = embedder.encode_intents(test_query)
    
    print(f"\nTest Query: '{test_query}'")
    print(f"Vector Length: {len(vector[0])} dimensions")
    print(f"First 5 values: {vector[0][:5]}")