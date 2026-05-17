import os
import sys
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

# Load configuration environment variables from root directory
load_dotenv()

# Prevent Hugging Face from cluttering stdout; ensures clear MCP communication lines
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# Automatically authenticate if a token is present in the .env file
if os.getenv("HF_TOKEN"):
    os.environ["HF_TOKEN"] = os.getenv("HF_TOKEN")

class IntentEmbedder:
    _cached_model = None  # Singleton pattern prevents reloading model on every tool execution (expensive)

    def __init__(self, model_name=None):
        self.model_name = model_name or os.getenv(
            "EMBEDDING_MODEL",
            "BAAI/bge-small-en-v1.5"
        )

        # Load only once across instances — crucial for MCP server runtime efficiency
        if IntentEmbedder._cached_model is None:
            print(f"[Embedder] Loading model: {self.model_name} ...", file=sys.stderr)
            try:
                IntentEmbedder._cached_model = SentenceTransformer(
                    self.model_name,
                    trust_remote_code=True  # Safely handles modern custom transformer configurations
                )
                print("[Embedder] Model loaded successfully into memory.", file=sys.stderr)
            except Exception as e:
                print(f"[Embedder] Critical Error loading model {self.model_name}: {e}", file=sys.stderr)
                raise

        self.model = IntentEmbedder._cached_model

    def encode_intents(self, intents):
        """
        Accepts a single string query or list of string intents and returns vectors.
        """
        if isinstance(intents, str):
            intents = [intents]

        # Generate Normalized Embeddings
        embeddings = self.model.encode(
            intents,
            normalize_embeddings=True  # Critical mathematical step for accurate Pinecone cosine similarity matching
        )

        # Convert NumPy array configurations to raw Python lists (Pinecone-safe execution payload)
        return embeddings.tolist()


if __name__ == "__main__":
    # Local debugging execution test block
    embedder = IntentEmbedder()
    test_query = "Who is the manager of worker 123?"
    vectorized_payload = embedder.encode_intents(test_query)
    print(f"Test vector generation successful. Dimensions: {len(vectorized_payload[0])}", file=sys.stderr)