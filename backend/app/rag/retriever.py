from typing import Any, Dict, List, Optional
import re

from app.rag.rag_config import get_rag_settings
from app.rag.embedding_service import embed_query
from app.rag.vector_db import search_vectors
from app.rag.reranker import rerank_chunks


def normalise_stage(stage: Optional[str]) -> Optional[str]:
    if not stage:
        return None

    stage = str(stage).upper().strip()

    mapping = {
        "STAGE1": "S1",
        "STAGE 1": "S1",
        "S1": "S1",
        "STAGE2": "S2",
        "STAGE 2": "S2",
        "S2": "S2",
        "STAGE3": "S3",
        "STAGE 3": "S3",
        "S3": "S3",
        "CYCLE": "CY",
        "OVERALL": "CY",
        "CY": "CY",
        "EXHAUST": "EX",
        "EX": "EX",
    }

    return mapping.get(stage, stage)


def pattern_matches(chunk_pattern: str, requested_pattern: Optional[str]) -> bool:
    if not requested_pattern:
        return True

    if not chunk_pattern:
        return False

    requested_parts = set(re.findall(r"[A-F]", str(requested_pattern).upper()))
    chunk_parts = set(re.findall(r"[A-F]", str(chunk_pattern).upper()))

    if not requested_parts:
        return True

    return bool(requested_parts.intersection(chunk_parts))


def metric_matches(chunk: Dict[str, Any], metric: Optional[str]) -> bool:
    if not metric:
        return True

    metric = str(metric).strip()
    related = chunk.get("related_scoring_metrics") or []

    if metric in related:
        return True

    text = (chunk.get("search_text") or chunk.get("text") or "")
    return metric in text


def apply_structured_post_filter(
    chunks: List[Dict[str, Any]],
    stage: Optional[str],
    metric: Optional[str],
    pattern: Optional[str],
) -> List[Dict[str, Any]]:
    stage = normalise_stage(stage)

    filtered = []

    for chunk in chunks:
        if chunk.get("include_for_anomaly_retrieval") is False:
            continue

        if stage and chunk.get("stage") != stage:
            continue

        if metric and not metric_matches(chunk, metric):
            continue

        if pattern and not pattern_matches(chunk.get("pattern", ""), pattern):
            continue

        filtered.append(chunk)

    # If too strict and returns nothing, relax metric/pattern but keep stage.
    if not filtered and (metric or pattern):
        for chunk in chunks:
            if chunk.get("include_for_anomaly_retrieval") is False:
                continue
            if stage and chunk.get("stage") != stage:
                continue
            filtered.append(chunk)

    return filtered


def filter_stale_deleted_qdrant_rules(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    If an original/custom rule was deleted from JSON but an old Qdrant point still
    exists, prevent that stale point from being used by RCA.
    """
    try:
        from app.rag.custom_rule_service import get_active_rule_ids

        active_rule_ids = get_active_rule_ids()
    except Exception:
        return chunks

    output = []
    for chunk in chunks or []:
        rule_id = str(chunk.get("rule_id") or "").strip()
        if rule_id and rule_id not in active_rule_ids:
            continue
        output.append(chunk)
    return output


def append_matching_json_rule_chunks(
    chunks: List[Dict[str, Any]],
    stage: Optional[str],
    metric: Optional[str],
    pattern: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Rules are indexed into Qdrant, but we also append matching rule chunks from
    JSON directly. This guarantees edited/deleted/created rules are reflected
    immediately without requiring the user to run a terminal chunking command.
    """
    try:
        from app.rag.custom_rule_service import get_matching_rule_chunks

        json_chunks = get_matching_rule_chunks(
            stage=stage,
            metric=metric,
            pattern=pattern,
        )
    except Exception:
        json_chunks = []

    output = []
    seen = set()

    for chunk in list(chunks or []) + list(json_chunks or []):
        chunk_id = str(chunk.get("chunk_id") or chunk.get("rule_id") or "")
        if chunk_id and chunk_id in seen:
            continue
        if chunk_id:
            seen.add(chunk_id)
        output.append(chunk)

    return output


def build_retrieval_query(
    affected_stage: Optional[str],
    metric: Optional[str],
    pattern: Optional[str],
    scoring_context: Dict[str, Any],
) -> str:
    parts = [
        "Novaflow RCA V4 sterilizer anomaly",
        f"affected_stage={affected_stage}" if affected_stage else "",
        f"metric={metric}" if metric else "",
        f"pattern={pattern}" if pattern else "",
    ]

    for key in [
        "cycle_score",
        "score_band",
        "s1_score",
        "s2_score",
        "s3_score",
        "MAE_s1_peak",
        "MAE_s1_ramp",
        "MAE_s2_peak",
        "MAE_s2_ramp",
        "MAE_s3_hold",
        "RMSE_s3_hold",
        "combined_s3_hold",
        "osc_ratio_s3_hold",
    ]:
        if key in scoring_context and scoring_context[key] is not None:
            parts.append(f"{key}={scoring_context[key]}")

    return " ".join([p for p in parts if p])


def retrieve_rca_chunks(
    affected_stage: Optional[str],
    metric: Optional[str],
    pattern: Optional[str],
    scoring_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    settings = get_rag_settings()

    stage = normalise_stage(affected_stage)
    query = build_retrieval_query(stage, metric, pattern, scoring_context)
    query_vector = embed_query(query)

    raw_results = search_vectors(
        query_vector=query_vector,
        top_k=settings["vector_top_k"],
        stage=stage,
        metric=metric,
        pattern=pattern,
    )

    raw_results = filter_stale_deleted_qdrant_rules(raw_results)

    filtered = apply_structured_post_filter(
        raw_results,
        stage=stage,
        metric=metric,
        pattern=pattern,
    )

    filtered = append_matching_json_rule_chunks(
        filtered,
        stage=stage,
        metric=metric,
        pattern=pattern,
    )

    reranked = rerank_chunks(
        query=query,
        chunks=filtered,
        top_k=settings["rerank_top_k"],
    )

    return reranked[: settings["final_top_k"]]
