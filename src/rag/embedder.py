import os, sys
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

class IntentEmbedder:
    _cached_model = None  # Prevents reloading on every MCP request (expensive)

    def __init__(self, model_name=None):
        self.model_name = model_name or os.getenv(
            "EMBEDDING_MODEL",
            "BAAI/bge-small-en-v1.5"
        )
        print(f"[Embedder] Loading model: {self.model_name}", file=sys.stderr)

        # Load only once — MCP calls this multiple times
        if IntentEmbedder._cached_model is None:
            IntentEmbedder._cached_model = SentenceTransformer(
                self.model_name,
                trust_remote_code=True  # Required for some BGE models
            )
            print("[Embedder] Model loaded successfully.", file=sys.stderr)

        self.model = IntentEmbedder._cached_model

    def encode_intents(self, intents):
        """Accepts a string or list of strings and returns vectors."""
        if isinstance(intents, str):
            intents = [intents]

        embeddings = self.model.encode(
            intents,
            normalize_embeddings=True  # required for cosine similarity
        )

        # Convert to pure Python lists (Pinecone-safe)
        return embeddings.tolist()


if __name__ == "__main__":
    embedder = IntentEmbedder()
    q = "Who is the manager of worker 123?"
    vec = embedder.encode_intents(q)
    print("Dimensions:", len(vec[0]))