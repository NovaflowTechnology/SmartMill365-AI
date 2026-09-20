"""Optional cross-encoder reranking with a safe vector-score fallback."""

from functools import lru_cache
import logging
from typing import Any, Dict, List

from app.rag.rag_config import get_rag_settings


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_reranker():
    from sentence_transformers import CrossEncoder

    settings = get_rag_settings()
    return CrossEncoder(
        settings["reranker_model_name"],
        device=settings["reranker_device"],
    )


def _fallback(chunks: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    ordered = sorted(
        (dict(chunk) for chunk in chunks),
        key=lambda item: float(item.get("_vector_score") or 0.0),
        reverse=True,
    )
    for item in ordered:
        item.setdefault("_rerank_score", item.get("_vector_score"))
    return ordered[:top_k]


def rerank_chunks(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """Rerank retrieved chunks; never break retrieval if the model is unavailable."""
    if not chunks or top_k <= 0:
        return []

    settings = get_rag_settings()
    if not settings.get("enable_reranker"):
        return _fallback(chunks, top_k)

    pairs = [
        [query, str(chunk.get("search_text") or chunk.get("text") or "")]
        for chunk in chunks
    ]

    try:
        scores = _get_reranker().predict(pairs, show_progress_bar=False)
        reranked = []
        for chunk, score in zip(chunks, scores):
            item = dict(chunk)
            item["_rerank_score"] = float(score)
            reranked.append(item)
        reranked.sort(key=lambda item: item["_rerank_score"], reverse=True)
        return reranked[:top_k]
    except Exception as exc:  # model download/runtime failures must be non-fatal
        logger.warning("Reranker unavailable; using vector ranking: %s", exc)
        return _fallback(chunks, top_k)
