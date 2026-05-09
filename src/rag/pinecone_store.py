import os
import time
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

load_dotenv()

class PineconeStore:
    def __init__(self):
        self.api_key = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "workday-router")
        
        # Initialize Pinecone client
        self.pc = Pinecone(api_key=self.api_key)
        
        # Dimensions must be 384 for bge-small-en-v1.5
        self.dimensions = 384 
        self.ensure_index_exists()
        
        # Connect to the specific index
        self.index = self.pc.Index(self.index_name)

    def ensure_index_exists(self):
        """Creates the index if it doesn't already exist."""
        existing_indexes = [index.name for index in self.pc.list_indexes()]
        
        if self.index_name not in existing_indexes:
            print(f"Creating new Pinecone index: {self.index_name}...")
            self.pc.create_index(
                name=self.index_name,
                dimension=self.dimensions,
                metric="cosine", # Best for semantic intent matching
                spec=ServerlessSpec(
                    cloud="aws", # Or "gcp"
                    region="us-east-1" # Choose region closest to you
                )
            )
            # Wait for index to be ready
            while not self.pc.describe_index(self.index_name).status['ready']:
                time.sleep(1)
            print("Index ready.")
        else:
            print(f"Index '{self.index_name}' already exists.")

    def upsert_vectors(self, vectors, namespace):
        """
        Uploads a list of vectors to a specific namespace.
        'vectors' should be a list of tuples: (id, vector_values, metadata)
        """
        print(f"Upserting {len(vectors)} vectors to namespace '{namespace}'...")
        self.index.upsert(vectors=vectors, namespace=namespace)

    def query_intent(self, query_vector, namespace, top_k=1):
        """
        Searches for the most relevant API based on the query vector.
        """
        results = self.index.query(
            vector=query_vector,
            top_k=top_k,
            include_metadata=True,
            namespace=namespace
        )
        return results

if __name__ == "__main__":
    # Quick connectivity test
    store = PineconeStore()
    print("Pinecone Store initialized and connected.")