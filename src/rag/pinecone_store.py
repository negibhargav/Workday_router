import os, sys
import time
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

load_dotenv()

class PineconeStore:
    def __init__(self):
        self.api_key = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "workday-router")
        self.dimensions = 384  # BGE-small embedding size

        if not self.api_key:
            raise ValueError("PINECONE_API_KEY missing from environment variables!")

        # Initialize SDK client
        self.pc = Pinecone(api_key=self.api_key)

        # Ensure index exists or create it
        self.ensure_index_exists()

        # Get a handle to the index
        try:
            self.index = self.pc.Index(self.index_name)
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Pinecone index: {e}")

# In src/rag/pinecone_store.py

    def ensure_index_exists(self):
        # 1. Get a list of existing indexes
        existing_indexes = [index.name for index in self.pc.list_indexes()]
        
        # 2. Only create if it's not there
        if self.index_name not in existing_indexes:
            print(f"Creating index {self.index_name}...")
            self.pc.create_index(
                name=self.index_name,
                dimension=384, # Ensure this matches your BAAI/bge-small model
                metric="cosine",
                # ... other spec settings ...
            )
        else:
            print(f"Index {self.index_name} already exists. Skipping creation.")

    def upsert_vectors(self, vectors, namespace):
        """Upserts vectors in Pinecone (id, vector, metadata)."""
        try:
            print(f"[Pinecone] Upserting {len(vectors)} vectors → namespace '{namespace}'", file=sys.stderr)
            self.index.upsert(vectors=vectors, namespace=namespace)
        except Exception as e:
            print("[Pinecone] Upsert error:", e, file=sys.stderr)
            raise

    def query_intent(self, query_vector, namespace, top_k=1):
        """Find the most relevant intent/API using vector search."""
        try:
            res = self.index.query(
                vector=query_vector,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace
            )
            return res
        except Exception as e:
            print("[Pinecone] Query error:", e, file=sys.stderr)
            return {"error": str(e)}


if __name__ == "__main__":
    store = PineconeStore()
    print("Pinecone Store initialized.")