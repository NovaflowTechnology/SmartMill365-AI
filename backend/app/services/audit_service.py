"""Supabase-backed append-only audit history for configuration changes.

Authentication is intentionally not implemented yet because the Smart Mill
user-management API is not available in this project version. Events therefore
use ``unauthenticated_local_user`` until a verified identity is integrated.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.supabase_service import get_supabase_client


AUDIT_TABLE = "sterilizer_audit_events"
AUDIT_TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")
DEFAULT_ACTOR = "unauthenticated_local_user"
LOGGER = logging.getLogger("sterilizer_api.audit")


def _json_safe(value: Any) -> Any:
    """Return a JSON-serialisable copy without leaking non-JSON objects."""

    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _event_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Preserve the legacy audit event response shape for callers/UI."""

    return {
        "event_id": row.get("event_id"),
        "timestamp": row.get("created_at"),
        "actor": row.get("actor"),
        "actor_verified": bool(row.get("actor_verified", False)),
        "action": row.get("action"),
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "before": row.get("before_data"),
        "after": row.get("after_data"),
        "details": row.get("details") or {},
    }


def record_audit_event(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    before: Optional[Any] = None,
    after: Optional[Any] = None,
    details: Optional[Dict[str, Any]] = None,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert one immutable audit event into Supabase."""

    now = datetime.now(AUDIT_TIMEZONE).replace(microsecond=0)
    event_id = uuid4().hex
    row = {
        "event_id": event_id,
        "created_at": now.isoformat(),
        "actor": str(actor or DEFAULT_ACTOR),
        "actor_verified": False,
        "action": str(action),
        "entity_type": str(entity_type),
        "entity_id": str(entity_id),
        "before_data": _json_safe(before) if before is not None else None,
        "after_data": _json_safe(after) if after is not None else None,
        "details": _json_safe(details or {}),
    }

    saved = get_supabase_client().insert(AUDIT_TABLE, row)
    if not saved:
        raise RuntimeError("Supabase did not return the saved audit event.")
    return _event_from_row(dict(saved[0]))


def try_record_audit_event(**kwargs: Any) -> Dict[str, Any]:
    """Record an event without undoing an already-completed primary save."""

    try:
        event = record_audit_event(**kwargs)
        return {"status": "recorded", "event_id": event["event_id"]}
    except Exception as exc:
        LOGGER.exception(
            "Audit history write failed for action=%s entity=%s",
            kwargs.get("action"),
            kwargs.get("entity_id"),
        )
        return {"status": "audit_failed", "error": str(exc)}


def list_audit_events(limit: int = 100) -> List[Dict[str, Any]]:
    """Return recent Supabase audit events, newest first."""

    safe_limit = max(1, min(int(limit or 100), 500))
    rows = get_supabase_client().select(
        AUDIT_TABLE,
        columns="*",
        order="created_at.desc",
        limit=safe_limit,
    )
    return [_event_from_row(dict(row)) for row in rows]
