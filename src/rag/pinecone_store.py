import os
import sys
import time
import json
import yaml
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv
from openai import OpenAI  # Used for generating schema vectors

_pc_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
load_dotenv(os.path.join(_pc_root, ".env"))

class PineconeStore:
    def __init__(self, force_reset=False):
        self.api_key    = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "workday-router")
        
        # NOTE: If using OpenAI text-embedding-3-small, set dimensions to 1536.
        # If using HuggingFace BGE-small local embeddings, keep it at 384.
        self.dimensions = 384  

        if not self.api_key:
            raise ValueError("PINECONE_API_KEY missing from environment variables!")

        self.pc = Pinecone(api_key=self.api_key)
        
        if force_reset:
            self.wipe_and_reset_index()
        else:
            self.ensure_index_exists()

        try:
            self.index = self.pc.Index(self.index_name)
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Pinecone index: {e}")

    def wipe_and_reset_index(self):
        """Completely deletes the index if it exists and creates it fresh."""
        existing_indexes = [index.name for index in self.pc.list_indexes()]
        
        if self.index_name in existing_indexes:
            print(f"[Pinecone] Deleting existing index '{self.index_name}' to clear all data...")
            self.pc.delete_index(self.index_name)
            
            # Wait for deletion to clear completely on serverless cloud routers
            while self.index_name in [index.name for index in self.pc.list_indexes()]:
                print("Waiting for old index deletion to finalize...")
                time.sleep(2)
            print("[Pinecone] Old database dropped successfully.")
        
        self.ensure_index_exists()

    def ensure_index_exists(self):
        existing_indexes = [index.name for index in self.pc.list_indexes()]

        if self.index_name not in existing_indexes:
            print(f"[Pinecone] Creating fresh index '{self.index_name}'...")
            self.pc.create_index(
                name=self.index_name,
                dimension=self.dimensions,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )
            # Wait until active
            while not self.pc.describe_index(self.index_name).status["ready"]:
                print("Waiting for fresh index to initialize and get ready...")
                time.sleep(2)
            print(f"Index '{self.index_name}' is ready for new schema inputs.")
        else:
            print(f"Index '{self.index_name}' already exists. Skipping creation.")

    def upsert_vectors(self, vectors, namespace=""):
        try:
            print(f"[Pinecone] Upserting {len(vectors)} vectors → namespace '{namespace}'", file=sys.stderr)
            self.index.upsert(vectors=vectors, namespace=namespace)
        except Exception as e:
            print("[Pinecone] Upsert error:", e, file=sys.stderr)
            raise

    def query_intent(self, query_vector, namespace="", top_k=1, filter_dict=None):
        try:
            res = self.index.query(
                vector=query_vector,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace,
                filter=filter_dict or {}
            )
            return res
        except Exception as e:
            print("[Pinecone] Query error:", e, file=sys.stderr)
            return {"error": str(e)}