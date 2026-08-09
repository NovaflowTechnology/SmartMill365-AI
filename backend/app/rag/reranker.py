from functools import lru_cache
from typing import Any, Dict, List

from sentence_transformers import CrossEncoder

from app.rag.rag_config import get_rag_settings


@lru_cache(maxsize=1)
def get_reranker_model() -> CrossEncoder:
    settings = get_rag_settings()
    return CrossEncoder(
        settings["reranker_model_name"],
        device=settings["reranker_device"],
    )


def rerank_chunks(query: str, chunks: List[Dict[str, Any]], top_k: int = 8) -> List[Dict[str, Any]]:
    if not chunks:
        return []

    settings = get_rag_settings()

    if not settings["enable_reranker"]:
        return chunks[:top_k]

    model = get_reranker_model()

    pairs = [
        [
            query,
            chunk.get("search_text") or chunk.get("text") or "",
        ]
        for chunk in chunks
    ]

    scores = model.predict(pairs)

    reranked = []
    for chunk, score in zip(chunks, scores):
        item = dict(chunk)
        item["_rerank_score"] = float(score)
        reranked.append(item)

    reranked.sort(key=lambda x: x.get("_rerank_score", 0.0), reverse=True)

    return reranked[:top_k]
