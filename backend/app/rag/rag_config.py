from functools import lru_cache
from pathlib import Path
import os


BACKEND_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_BASE_ROOT = BACKEND_ROOT / "knowledge_base"

CHUNKS_PATH = KNOWLEDGE_BASE_ROOT / "chunks" / "rca_v4_chunks.json"

QDRANT_MODE = os.getenv("QDRANT_MODE", "local").lower()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "novaflow_rca_v4")

QDRANT_LOCAL_PATH = os.getenv(
    "QDRANT_LOCAL_PATH",
    str(KNOWLEDGE_BASE_ROOT / "qdrant_local"),
)

EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL_NAME", "BAAI/bge-reranker-v2-m3")

EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "cpu")

VECTOR_TOP_K = int(os.getenv("VECTOR_TOP_K", "20"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "8"))
FINAL_TOP_K = int(os.getenv("FINAL_TOP_K", "5"))

ENABLE_RERANKER = os.getenv("ENABLE_RERANKER", "true").lower() == "true"


@lru_cache(maxsize=1)
def get_rag_settings():
    return {
        "backend_root": BACKEND_ROOT,
        "knowledge_base_root": KNOWLEDGE_BASE_ROOT,
        "chunks_path": CHUNKS_PATH,

        "qdrant_mode": QDRANT_MODE,
        "qdrant_url": QDRANT_URL,
        "qdrant_api_key": QDRANT_API_KEY,
        "qdrant_collection": QDRANT_COLLECTION,
        "qdrant_local_path": Path(QDRANT_LOCAL_PATH),

        "embedding_model_name": EMBEDDING_MODEL_NAME,
        "reranker_model_name": RERANKER_MODEL_NAME,
        "embedding_device": EMBEDDING_DEVICE,
        "reranker_device": RERANKER_DEVICE,

        "vector_top_k": VECTOR_TOP_K,
        "rerank_top_k": RERANK_TOP_K,
        "final_top_k": FINAL_TOP_K,
        "enable_reranker": ENABLE_RERANKER,
    }