from typing import Any, Dict
import json

from app.rag.chunk_loader import load_rca_chunks
from app.rag.embedding_service import embed_texts
from app.rag.vector_db import ensure_collection, upsert_chunks, close_qdrant_client
from app.services.analysis_rule_storage_service import (
    set_index_status,
    set_reference_index_status,
)


def _update_storage_index_status(chunks, *, status: str, error: str | None = None) -> int:
    rule_ids = [str(c.get("rule_id") or "").strip() for c in chunks if str(c.get("rule_id") or "").strip()]
    ref_ids = [
        str(c.get("chunk_id") or "").strip()
        for c in chunks
        if not str(c.get("rule_id") or "").strip() and str(c.get("chunk_id") or "").strip()
    ]
    updated = 0
    if rule_ids:
        updated += set_index_status(rule_ids, status=status, error=error)
    if ref_ids:
        updated += set_reference_index_status(ref_ids, status=status, error=error)
    return updated


def index_rca_chunks(recreate: bool = True) -> Dict[str, Any]:
    """Index the authoritative Supabase knowledge set into Qdrant."""

    chunks = load_rca_chunks()
    searchable_texts = [
        chunk.get("search_text") or chunk.get("text") or ""
        for chunk in chunks
    ]

    try:
        vectors = embed_texts(searchable_texts, batch_size=8)
        ensure_collection(recreate=recreate)
        indexed = upsert_chunks(chunks, vectors)
        status_rows = _update_storage_index_status(chunks, status="indexed")
        return {
            "indexed_chunks": indexed,
            "recreate_collection": recreate,
            "index_status_rows_updated": status_rows,
        }
    except Exception as exc:
        try:
            _update_storage_index_status(chunks, status="failed", error=str(exc))
        except Exception:
            pass
        raise


if __name__ == "__main__":
    try:
        result = index_rca_chunks(recreate=True)
        print(json.dumps(result, indent=2))
    finally:
        close_qdrant_client()
