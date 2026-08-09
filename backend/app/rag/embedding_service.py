from functools import lru_cache
from typing import Iterable, List

import numpy as np
from sentence_transformers import SentenceTransformer

from app.rag.rag_config import get_rag_settings


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    settings = get_rag_settings()
    return SentenceTransformer(
        settings["embedding_model_name"],
        device=settings["embedding_device"],
    )


def embed_texts(texts: Iterable[str], batch_size: int = 16) -> List[List[float]]:
    """
    Embed text using BGE-M3.

    normalize_embeddings=True is important because Qdrant can then use cosine
    similarity reliably for retrieval.
    """
    model = get_embedding_model()
    texts = [str(t or "") for t in texts]

    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    return np.asarray(vectors, dtype=np.float32).tolist()


def embed_query(query: str) -> List[float]:
    return embed_texts([query], batch_size=1)[0]


def get_embedding_dimension() -> int:
    model = get_embedding_model()
    return int(model.get_sentence_embedding_dimension())
