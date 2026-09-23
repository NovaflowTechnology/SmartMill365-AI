"""One-time migration of local Analysis knowledge and audit files to Supabase.

Usage from the backend folder after applying the SQL migration and configuring
SUPABASE_URL/SUPABASE_SECRET_KEY:

    python -m app.scripts.migrate_analysis_storage_to_supabase

By default existing Supabase rule/chunk rows are not overwritten. Add
``--overwrite`` only when you intentionally want the bundled JSON seed to
replace rows that are already present.

This script does not modify or recreate Qdrant.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.rag.custom_rule_service import (
    chunk_to_editable_rule,
    normalise_loaded_chunk,
    rule_to_chunk,
)
from app.services.analysis_rule_storage_service import (
    RULES_TABLE,
    REFERENCE_CHUNKS_TABLE,
    list_reference_chunk_rows,
    list_rule_rows,
    save_reference_chunk,
    save_rule_record,
)
from app.services.audit_service import AUDIT_TABLE
from app.services.supabase_service import get_supabase_client


BACKEND_ROOT = Path(__file__).resolve().parents[2]
BASE_CHUNKS_PATH = BACKEND_ROOT / "knowledge_base" / "chunks" / "rca_v4_chunks.json"
CUSTOM_RULES_PATH = BACKEND_ROOT / "knowledge_base" / "custom_rules" / "custom_rca_rules.json"
CUSTOM_CHUNKS_PATH = BACKEND_ROOT / "knowledge_base" / "custom_rules" / "custom_rca_chunks.json"
AUDIT_FOLDER = BACKEND_ROOT / "audit_history"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield dict(item)


def migrate_original_knowledge(*, overwrite: bool) -> Dict[str, int]:
    chunks = list(_iter_dicts(_read_json(BASE_CHUNKS_PATH, [])))
    existing_rules = {str(row.get("rule_id") or "").strip() for row in list_rule_rows(enabled_only=False)}
    existing_refs = {str(row.get("chunk_id") or "").strip() for row in list_reference_chunk_rows(enabled_only=False)}

    counts = {"rules_imported": 0, "rules_skipped": 0, "reference_chunks_imported": 0, "reference_chunks_skipped": 0}
    for raw_chunk in chunks:
        chunk_id = str(raw_chunk.get("chunk_id") or "").strip()
        rule_id = str(raw_chunk.get("rule_id") or "").strip()
        if not chunk_id:
            continue

        if rule_id:
            if rule_id in existing_rules and not overwrite:
                counts["rules_skipped"] += 1
                continue
            chunk = normalise_loaded_chunk(raw_chunk)
            rule = chunk_to_editable_rule(chunk)
            save_rule_record(
                rule_payload=rule,
                chunk_payload=chunk,
                index_status="pending",
            )
            existing_rules.add(rule_id)
            counts["rules_imported"] += 1
        else:
            if chunk_id in existing_refs and not overwrite:
                counts["reference_chunks_skipped"] += 1
                continue
            save_reference_chunk(raw_chunk, index_status="pending")
            existing_refs.add(chunk_id)
            counts["reference_chunks_imported"] += 1
    return counts


def migrate_custom_rules(*, overwrite: bool) -> Dict[str, int]:
    rules = list(_iter_dicts(_read_json(CUSTOM_RULES_PATH, [])))
    custom_chunks = list(_iter_dicts(_read_json(CUSTOM_CHUNKS_PATH, [])))
    chunks_by_rule = {
        str(chunk.get("rule_id") or "").strip(): chunk
        for chunk in custom_chunks
        if str(chunk.get("rule_id") or "").strip()
    }
    existing = {str(row.get("rule_id") or "").strip() for row in list_rule_rows(enabled_only=False)}
    counts = {"custom_rules_imported": 0, "custom_rules_skipped": 0}

    for raw_rule in rules:
        rule_id = str(raw_rule.get("rule_id") or "").strip()
        if not rule_id:
            continue
        if rule_id in existing and not overwrite:
            counts["custom_rules_skipped"] += 1
            continue
        rule = dict(raw_rule)
        rule["source"] = "custom"
        rule["editable"] = True
        chunk = dict(chunks_by_rule.get(rule_id) or rule_to_chunk(rule))
        save_rule_record(
            rule_payload=rule,
            chunk_payload=chunk,
            index_status="pending",
        )
        existing.add(rule_id)
        counts["custom_rules_imported"] += 1
    return counts


def migrate_audit_history() -> Dict[str, int]:
    """Import legacy audit JSON without updating any existing audit row.

    The company-safe table grants the backend only SELECT + INSERT on audit
    history. Existing event IDs are therefore skipped instead of being upserted.
    This preserves append-only audit semantics and least-privilege DB access.
    """

    client = get_supabase_client()
    counts = {
        "audit_events_imported": 0,
        "audit_events_already_present": 0,
        "audit_files_invalid": 0,
    }
    if not AUDIT_FOLDER.exists():
        return counts

    existing_ids = {
        str(row.get("event_id") or "").strip()
        for row in client.select(AUDIT_TABLE, columns="event_id")
        if str(row.get("event_id") or "").strip()
    }

    for path in sorted(AUDIT_FOLDER.glob("*.json")):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(event, dict) or not event.get("event_id"):
                raise ValueError("invalid audit event")

            event_id = str(event.get("event_id")).strip()
            if event_id in existing_ids:
                counts["audit_events_already_present"] += 1
                continue

            row = {
                "event_id": event_id,
                "created_at": event.get("timestamp"),
                "actor": str(event.get("actor") or "unauthenticated_local_user"),
                "actor_verified": bool(event.get("actor_verified", False)),
                "action": str(event.get("action") or "unknown"),
                "entity_type": str(event.get("entity_type") or "unknown"),
                "entity_id": str(event.get("entity_id") or "unknown"),
                "before_data": event.get("before"),
                "after_data": event.get("after"),
                "details": event.get("details") or {},
            }
            client.insert(AUDIT_TABLE, row)
            existing_ids.add(event_id)
            counts["audit_events_imported"] += 1
        except Exception:
            counts["audit_files_invalid"] += 1
    return counts


def verify_counts() -> Dict[str, int]:
    client = get_supabase_client()
    return {
        "supabase_rule_rows": len(client.select(RULES_TABLE, columns="rule_id")),
        "supabase_reference_chunk_rows": len(client.select(REFERENCE_CHUNKS_TABLE, columns="chunk_id")),
        "supabase_audit_rows": len(client.select(AUDIT_TABLE, columns="event_id")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing rule/reference rows with the local bundled JSON seed.",
    )
    args = parser.parse_args()

    result: Dict[str, Any] = {
        "original": migrate_original_knowledge(overwrite=args.overwrite),
        "custom": migrate_custom_rules(overwrite=args.overwrite),
        "audit": migrate_audit_history(),
        "verification": verify_counts(),
        "qdrant_changed": False,
        "next_step": "Use the existing Retry Search Index action only if you want to refresh/rebuild Qdrant from Supabase.",
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
