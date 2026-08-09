from typing import Any, Dict, List, Optional
from uuid import uuid5, NAMESPACE_URL
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.rag.rag_config import get_rag_settings
from app.rag.embedding_service import get_embedding_dimension


_CLIENT: Optional[QdrantClient] = None


def get_qdrant_client() -> QdrantClient:
    """
    Important for Qdrant local mode:

    Qdrant local mode locks the storage folder. If we create multiple
    QdrantClient(path=...) instances in the same process, it may cause:

    RuntimeError:
    Storage folder ... is already accessed by another instance of Qdrant client.

    Therefore, we keep one global client instance and reuse it.
    """
    global _CLIENT

    if _CLIENT is not None:
        return _CLIENT

    settings = get_rag_settings()

    if settings["qdrant_mode"] == "local":
        local_path = Path(settings["qdrant_local_path"])
        local_path.mkdir(parents=True, exist_ok=True)

        _CLIENT = QdrantClient(
            path=str(local_path),
            force_disable_check_same_thread=True,
        )
    else:
        _CLIENT = QdrantClient(
            url=settings["qdrant_url"],
            api_key=settings["qdrant_api_key"],
            timeout=60,
        )

    return _CLIENT


def ensure_collection(recreate: bool = False) -> None:
    settings = get_rag_settings()
    client = get_qdrant_client()
    collection = settings["qdrant_collection"]
    vector_size = get_embedding_dimension()

    existing = [c.name for c in client.get_collections().collections]

    if collection in existing and recreate:
        client.delete_collection(collection_name=collection)

    existing = [c.name for c in client.get_collections().collections]

    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    # Payload indexes are useful in Qdrant server mode.
    # In local mode, Qdrant may warn that indexes have no effect.
    # That warning is safe to ignore.
    for field_name in [
        "chunk_type",
        "stage",
        "rule_id",
        "rec_key",
        "attribution",
        "priority",
        "urgency",
        "include_for_anomaly_retrieval",
    ]:
        try:
            client.create_payload_index(
                collection_name=collection,
                field_name=field_name,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass


def deterministic_point_id(chunk_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"novaflow-rca-v4:{chunk_id}"))


def chunk_to_payload(chunk: Dict[str, Any]) -> Dict[str, Any]:
    return dict(chunk)


def upsert_chunks(chunks: List[Dict[str, Any]], vectors: List[List[float]]) -> int:
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors length mismatch.")

    settings = get_rag_settings()
    client = get_qdrant_client()

    points = []

    for chunk, vector in zip(chunks, vectors):
        chunk_id = str(chunk.get("chunk_id"))

        if not chunk_id:
            raise ValueError("Every chunk must have chunk_id.")

        points.append(
            models.PointStruct(
                id=deterministic_point_id(chunk_id),
                vector=vector,
                payload=chunk_to_payload(chunk),
            )
        )

    client.upsert(
        collection_name=settings["qdrant_collection"],
        points=points,
        wait=True,
    )

    return len(points)


def build_filter(
    stage: Optional[str] = None,
    include_for_anomaly_retrieval: bool = True,
) -> Optional[models.Filter]:
    must_conditions = []

    if include_for_anomaly_retrieval:
        must_conditions.append(
            models.FieldCondition(
                key="include_for_anomaly_retrieval",
                match=models.MatchValue(value=True),
            )
        )

    if stage:
        must_conditions.append(
            models.FieldCondition(
                key="stage",
                match=models.MatchValue(value=stage),
            )
        )

    if not must_conditions:
        return None

    return models.Filter(must=must_conditions)


def search_vectors(
    query_vector: List[float],
    top_k: int,
    stage: Optional[str] = None,
    metric: Optional[str] = None,
    pattern: Optional[str] = None,
) -> List[Dict[str, Any]]:
    settings = get_rag_settings()
    client = get_qdrant_client()
    collection = settings["qdrant_collection"]

    query_filter = build_filter(stage=stage)

    try:
        response = client.query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )
        scored_points = response.points
    except Exception:
        scored_points = client.search(
            collection_name=collection,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )

    results = []

    for point in scored_points:
        payload = dict(point.payload or {})
        payload["_vector_score"] = float(point.score)
        results.append(payload)

    return results


def collection_count() -> int:
    settings = get_rag_settings()
    client = get_qdrant_client()

    info = client.get_collection(
        collection_name=settings["qdrant_collection"]
    )

    return int(info.points_count or 0)

def close_qdrant_client() -> None:
    global _CLIENT

    if _CLIENT is not None:
        try:
            _CLIENT.close()
        except Exception:
            pass
        finally:
            _CLIENT = None

def delete_chunks(chunk_ids: List[str]) -> int:
    """Delete Qdrant points by RCA chunk_id. Used when a rule is deleted."""
    if not chunk_ids:
        return 0

    settings = get_rag_settings()
    client = get_qdrant_client()
    point_ids = [deterministic_point_id(str(chunk_id)) for chunk_id in chunk_ids if chunk_id]

    if not point_ids:
        return 0

    client.delete(
        collection_name=settings["qdrant_collection"],
        points_selector=models.PointIdsList(points=point_ids),
        wait=True,
    )

    return len(point_ids)
