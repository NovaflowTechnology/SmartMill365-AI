"""Supabase persistence for Analysis/RCA rules and reference chunks.

Supabase is the authoritative store for editable Analysis rules. Original
non-rule knowledge chunks (stage definitions, attribution references, safety
checks, etc.) are also stored in Supabase so Qdrant can be rebuilt without
falling back to local JSON. Qdrant remains a separate search index.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from app.services.supabase_service import get_supabase_client, postgres_in_filter


RULES_TABLE = "sterilizer_analysis_rules"
REFERENCE_CHUNKS_TABLE = "sterilizer_analysis_reference_chunks"
MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")
VALID_INDEX_STATUSES = {"pending", "indexed", "failed"}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(MALAYSIA_TZ).replace(microsecond=0).isoformat()


def _validate_status(index_status: str) -> None:
    if index_status not in VALID_INDEX_STATUSES:
        raise ValueError("Invalid Analysis knowledge index status.")


def _record_from_payloads(
    *,
    rule_payload: Dict[str, Any],
    chunk_payload: Dict[str, Any],
    index_status: str = "pending",
    indexed_at: Optional[str] = None,
    index_error: Optional[str] = None,
) -> Dict[str, Any]:
    rule = dict(rule_payload or {})
    chunk = dict(chunk_payload or {})
    source = _clean(rule.get("source") or chunk.get("source") or "custom").lower()
    rule_id = _clean(rule.get("rule_id") or chunk.get("rule_id"))
    chunk_id = _clean(chunk.get("chunk_id"))
    if not rule_id:
        raise ValueError("Analysis rule storage requires rule_id.")
    if not chunk_id:
        raise ValueError("Analysis rule storage requires chunk_id.")
    if source not in {"original", "custom"}:
        raise ValueError("Analysis rule source must be original or custom.")
    _validate_status(index_status)

    created_at = _clean(rule.get("created_at") or chunk.get("created_at")) or _now_iso()
    updated_at = _clean(rule.get("updated_at") or chunk.get("updated_at")) or created_at

    return {
        "rule_id": rule_id,
        "chunk_id": chunk_id,
        "source": source,
        "stage": _clean(rule.get("stage") or chunk.get("stage")) or None,
        "metric_name": _clean(
            rule.get("metric_name")
            or rule.get("threshold_metric_name")
            or chunk.get("metric_name")
            or chunk.get("threshold_metric_name")
        ) or None,
        "metric_description": _clean(rule.get("metric_description") or chunk.get("metric_description")) or None,
        "pattern": _clean(rule.get("pattern") or chunk.get("pattern")) or None,
        "attribution": _clean(rule.get("attribution") or chunk.get("attribution")) or None,
        "priority": _clean(rule.get("priority") or chunk.get("priority")) or None,
        "urgency": _clean(rule.get("urgency") or chunk.get("urgency")) or None,
        "rec_key": _clean(rule.get("rec_key") or chunk.get("rec_key")) or None,
        "warn_low": rule.get("warn_low", chunk.get("warn_low")),
        "critical_value": rule.get(
            "critical_value",
            chunk.get("critical_value", chunk.get("warn_high_critical_starts_here")),
        ),
        "recommendation_en": _clean(rule.get("recommendation_en") or chunk.get("recommendation_en")) or None,
        "recommendation_bm": _clean(rule.get("recommendation_bm") or chunk.get("recommendation_bm")) or None,
        "include_for_anomaly_retrieval": bool(
            rule.get(
                "include_for_anomaly_retrieval",
                chunk.get("include_for_anomaly_retrieval", True),
            )
        ),
        "enabled": bool(rule.get("enabled", chunk.get("enabled", True))),
        "rule_payload": _json_safe(rule),
        "chunk_payload": _json_safe(chunk),
        "index_status": index_status,
        "indexed_at": indexed_at,
        "index_error": _clean(index_error)[:4000] or None,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _reference_record(
    chunk_payload: Dict[str, Any],
    *,
    index_status: str = "pending",
    indexed_at: Optional[str] = None,
    index_error: Optional[str] = None,
) -> Dict[str, Any]:
    chunk = dict(chunk_payload or {})
    chunk_id = _clean(chunk.get("chunk_id"))
    if not chunk_id:
        raise ValueError("Analysis reference chunk storage requires chunk_id.")
    _validate_status(index_status)
    created_at = _clean(chunk.get("created_at")) or _now_iso()
    updated_at = _clean(chunk.get("updated_at")) or created_at
    return {
        "chunk_id": chunk_id,
        "chunk_type": _clean(chunk.get("chunk_type")) or None,
        "retrieval_scope": _clean(chunk.get("retrieval_scope")) or None,
        "stage": _clean(chunk.get("stage")) or None,
        "include_for_anomaly_retrieval": bool(chunk.get("include_for_anomaly_retrieval", False)),
        "enabled": bool(chunk.get("enabled", True)),
        "chunk_payload": _json_safe(chunk),
        "index_status": index_status,
        "indexed_at": indexed_at,
        "index_error": _clean(index_error)[:4000] or None,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def list_rule_rows(*, source: Optional[str] = None, enabled_only: bool = True) -> List[Dict[str, Any]]:
    filters: Dict[str, str] = {}
    if source:
        filters["source"] = f"eq.{_clean(source).lower()}"
    if enabled_only:
        filters["enabled"] = "eq.true"
    return get_supabase_client().select(
        RULES_TABLE,
        columns="*",
        filters=filters,
        order="rule_id.asc",
    )


def list_reference_chunk_rows(*, enabled_only: bool = True) -> List[Dict[str, Any]]:
    filters = {"enabled": "eq.true"} if enabled_only else {}
    return get_supabase_client().select(
        REFERENCE_CHUNKS_TABLE,
        columns="*",
        filters=filters,
        order="chunk_id.asc",
    )


def get_rule_row(rule_id: str) -> Optional[Dict[str, Any]]:
    rows = get_supabase_client().select(
        RULES_TABLE,
        columns="*",
        filters={"rule_id": f"eq.{_clean(rule_id)}"},
        limit=1,
    )
    return dict(rows[0]) if rows else None


def save_rule_record(
    *,
    rule_payload: Dict[str, Any],
    chunk_payload: Dict[str, Any],
    index_status: str = "pending",
    indexed_at: Optional[str] = None,
    index_error: Optional[str] = None,
) -> Dict[str, Any]:
    record = _record_from_payloads(
        rule_payload=rule_payload,
        chunk_payload=chunk_payload,
        index_status=index_status,
        indexed_at=indexed_at,
        index_error=index_error,
    )
    rows = get_supabase_client().upsert(
        RULES_TABLE,
        record,
        on_conflict="rule_id",
    )
    if not rows:
        raise RuntimeError("Supabase did not return the saved Analysis rule.")
    return dict(rows[0])


def save_reference_chunk(
    chunk_payload: Dict[str, Any],
    *,
    index_status: str = "pending",
    indexed_at: Optional[str] = None,
    index_error: Optional[str] = None,
) -> Dict[str, Any]:
    record = _reference_record(
        chunk_payload,
        index_status=index_status,
        indexed_at=indexed_at,
        index_error=index_error,
    )
    rows = get_supabase_client().upsert(
        REFERENCE_CHUNKS_TABLE,
        record,
        on_conflict="chunk_id",
    )
    if not rows:
        raise RuntimeError("Supabase did not return the saved Analysis reference chunk.")
    return dict(rows[0])


def delete_rule_record(rule_id: str) -> Optional[Dict[str, Any]]:
    rows = get_supabase_client().delete(
        RULES_TABLE,
        filters={"rule_id": f"eq.{_clean(rule_id)}"},
    )
    return dict(rows[0]) if rows else None


def set_index_status(
    rule_ids: Iterable[Any],
    *,
    status: str,
    error: Optional[str] = None,
) -> int:
    _validate_status(status)
    ids = [_clean(value) for value in rule_ids if _clean(value)]
    if not ids:
        return 0
    values: Dict[str, Any] = {
        "index_status": status,
        "index_error": _clean(error)[:4000] or None,
        "indexed_at": _now_iso() if status == "indexed" else None,
    }
    rows = get_supabase_client().update(
        RULES_TABLE,
        values,
        filters={"rule_id": postgres_in_filter(ids)},
    )
    return len(rows)


def set_reference_index_status(
    chunk_ids: Iterable[Any],
    *,
    status: str,
    error: Optional[str] = None,
) -> int:
    _validate_status(status)
    ids = [_clean(value) for value in chunk_ids if _clean(value)]
    if not ids:
        return 0
    values: Dict[str, Any] = {
        "index_status": status,
        "index_error": _clean(error)[:4000] or None,
        "indexed_at": _now_iso() if status == "indexed" else None,
    }
    rows = get_supabase_client().update(
        REFERENCE_CHUNKS_TABLE,
        values,
        filters={"chunk_id": postgres_in_filter(ids)},
    )
    return len(rows)
