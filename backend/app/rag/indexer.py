from typing import Any, Dict
import json

from app.rag.chunk_loader import load_rca_chunks
from app.rag.embedding_service import embed_texts
from app.rag.vector_db import ensure_collection, upsert_chunks, close_qdrant_client


def index_rca_chunks(recreate: bool = True) -> Dict[str, Any]:
    chunks = load_rca_chunks()

    searchable_texts = [
        chunk.get("search_text") or chunk.get("text") or ""
        for chunk in chunks
    ]

    vectors = embed_texts(searchable_texts, batch_size=8)

    ensure_collection(recreate=recreate)
    indexed = upsert_chunks(chunks, vectors)

    return {
        "indexed_chunks": indexed,
        "recreate_collection": recreate,
    }


if __name__ == "__main__":
    try:
        result = index_rca_chunks(recreate=True)
        print(json.dumps(result, indent=2))
    finally:
        close_qdrant_client()