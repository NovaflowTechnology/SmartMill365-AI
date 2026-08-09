from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from app.rag.rag_config import get_rag_settings


# =============================================================================
# Purpose
# =============================================================================
# This service powers RCA Rule Management.
#
# New behaviour in this version:
# - Original Excel-derived rules can be edited and deleted.
# - Custom rules can be created, edited, and deleted.
# - Editing/deleting original rules modifies the parsed/chunked knowledge-base
#   JSON, not the original .xlsx file.
# - Every save rebuilds the rule chunk, writes JSON, upserts to Qdrant, and is
#   available immediately to RCA retrieval.
# - Conflict checking warns only for strong duplicates: same stage, same metric,
#   overlapping pattern, and same root cause/attribution.
# - Different root-cause rules for the same pressure feature are allowed because
#   they represent alternative RCA candidates.
# =============================================================================


MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur") if ZoneInfo else None

VALID_STAGES = {"S1", "S2", "S3", "CY", "EX"}
VALID_PRIORITIES = {"P1", "P2", "P3", "P4", "P5"}
VALID_ATTRIBUTIONS = {"Boiler", "BPV", "Competition", "Network", "Local"}
VALID_URGENCIES = {"Low", "Medium", "High", "Critical"}
VALID_SOURCES = {"original", "custom"}

METRIC_OPTIONS = {
    "S1": [
        "MAE_s1_peak",
        "MAE_s1_ramp",
        "MAE_s1_release",
        "combined_s1_full",
        "osc_ratio_s1_full",
    ],
    "S2": [
        "MAE_s2_peak",
        "MAE_s2_ramp",
        "MAE_s2_release",
        "combined_s2_full",
        "osc_ratio_s2_full",
    ],
    "S3": [
        "MAE_s3_hold",
        "combined_s3_hold",
        "RMSE_s3_hold",
        "MAE_s3_ramp",
        "osc_ratio_s3_hold",
    ],
    "CY": [
        "combined_s1_full",
        "combined_s2_full",
        "combined_s3_full",
        "cycle_score",
    ],
    "EX": [
        "exhaust_residual_pressure",
        "exhaust_release_time",
    ],
}


# =============================================================================
# Generic helpers
# =============================================================================
def now_malaysia_iso() -> str:
    if MALAYSIA_TZ:
        return datetime.now(MALAYSIA_TZ).replace(microsecond=0).isoformat()
    return datetime.now().replace(microsecond=0).isoformat()


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def slugify(value: Any) -> str:
    text = clean_text(value).upper()
    text = re.sub(r"[^A-Z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or f"RULE_{uuid4().hex[:8].upper()}"


def normalise_stage(value: Any) -> str:
    text = clean_text(value).upper().replace("STAGE", "S").replace(" ", "")
    mapping = {
        "1": "S1",
        "2": "S2",
        "3": "S3",
        "CYCLE": "CY",
        "OVERALL": "CY",
        "OVERALLCYCLE": "CY",
        "EXHAUST": "EX",
    }
    return mapping.get(text, text)


def normalise_attribution(value: Any) -> str:
    text = clean_text(value).lower()
    if "boiler" in text:
        return "Boiler"
    if "bpv" in text or "valve" in text:
        return "BPV"
    if "competition" in text or "competing" in text:
        return "Competition"
    if "network" in text or "header" in text or "distribution" in text:
        return "Network"
    if "local" in text or "mechanical" in text:
        return "Local"
    return clean_text(value)


def normalise_pattern(value: Any) -> str:
    text = clean_text(value).upper().replace(" ", "")
    parts = re.findall(r"[A-F]", text)
    if not parts:
        return text
    return "+".join(dict.fromkeys(parts))


def to_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except Exception:
        raise ValueError(f"{field_name} must be a valid number.")
    return number


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def pattern_parts(value: Any) -> set[str]:
    return set(re.findall(r"[A-F]", clean_text(value).upper()))


def pattern_overlap(a: Any, b: Any) -> bool:
    a_parts = pattern_parts(a)
    b_parts = pattern_parts(b)
    if not a_parts or not b_parts:
        return clean_text(a).upper() == clean_text(b).upper()
    return bool(a_parts.intersection(b_parts))


def metric_from_rule_like(rule_or_chunk: Dict[str, Any]) -> str:
    related = rule_or_chunk.get("related_scoring_metrics") or []
    if related:
        return clean_text(related[0])
    return clean_text(
        rule_or_chunk.get("metric_name")
        or rule_or_chunk.get("threshold_metric_name")
        or rule_or_chunk.get("target_metric")
    )


def source_from_rule_like(rule_or_chunk: Dict[str, Any], default: str = "original") -> str:
    source = clean_text(rule_or_chunk.get("source") or default).lower()
    return source if source in VALID_SOURCES else default


# =============================================================================
# Paths and JSON helpers
# =============================================================================
def get_knowledge_base_root() -> Path:
    settings = get_rag_settings()
    return Path(settings["knowledge_base_root"])


def get_base_chunks_path() -> Path:
    settings = get_rag_settings()
    return Path(settings["chunks_path"])


def get_custom_rules_dir() -> Path:
    path = get_knowledge_base_root() / "custom_rules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_custom_rules_path() -> Path:
    return get_custom_rules_dir() / "custom_rca_rules.json"


def get_custom_chunks_path() -> Path:
    return get_custom_rules_dir() / "custom_rca_chunks.json"


def get_deleted_original_rules_path() -> Path:
    # This is a small audit log only. Deletion is performed by removing the rule
    # from the parsed chunks JSON and deleting its Qdrant point.
    return get_custom_rules_dir() / "deleted_original_rules.json"


def read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


# =============================================================================
# Conflict warning
# =============================================================================
class RuleConflictWarning(Exception):
    """Raised when a save would create two rules for the same RCA feature."""

    def __init__(self, message: str, conflicts: List[Dict[str, Any]], candidate: Dict[str, Any]):
        super().__init__(message)
        self.message = message
        self.conflicts = conflicts
        self.candidate = candidate

    def to_response(self) -> Dict[str, Any]:
        return {
            "conflict_detected": True,
            "message": self.message,
            "conflicts": self.conflicts,
            "candidate": {
                "rule_id": self.candidate.get("rule_id"),
                "source": self.candidate.get("source"),
                "stage": self.candidate.get("stage"),
                "metric_name": metric_from_rule_like(self.candidate),
                "pattern": self.candidate.get("pattern"),
                "attribution": self.candidate.get("attribution"),
                "warn_low": self.candidate.get("warn_low"),
                "critical_value": self.candidate.get("critical_value"),
            },
        }


# =============================================================================
# Loading original/custom rules
# =============================================================================
def load_base_chunks() -> List[Dict[str, Any]]:
    path = get_base_chunks_path()
    if not path.exists():
        return []
    data = read_json_file(path, [])
    if not isinstance(data, list):
        raise ValueError(f"{path.name} must contain a list of RCA chunks.")
    output = []
    for item in data:
        if isinstance(item, dict):
            item = dict(item)
            item.setdefault("source", "original")
            item.setdefault("editable", True)
            output.append(item)
    return output


def save_base_chunks(chunks: List[Dict[str, Any]]) -> None:
    write_json_file(get_base_chunks_path(), chunks)


def load_custom_rules() -> List[Dict[str, Any]]:
    data = read_json_file(get_custom_rules_path(), [])
    if not isinstance(data, list):
        raise ValueError("custom_rca_rules.json must contain a list.")
    output = []
    for rule in data:
        if isinstance(rule, dict):
            item = dict(rule)
            item.setdefault("source", "custom")
            item.setdefault("editable", True)
            output.append(item)
    return output


def save_custom_rules(rules: List[Dict[str, Any]]) -> None:
    cleaned = []
    for rule in rules:
        item = dict(rule)
        item["source"] = "custom"
        item["editable"] = True
        cleaned.append(item)
    write_json_file(get_custom_rules_path(), cleaned)
    write_json_file(get_custom_chunks_path(), [rule_to_chunk(rule) for rule in cleaned])


def load_deleted_original_rules() -> List[Dict[str, Any]]:
    data = read_json_file(get_deleted_original_rules_path(), [])
    return data if isinstance(data, list) else []


def save_deleted_original_rules(items: List[Dict[str, Any]]) -> None:
    write_json_file(get_deleted_original_rules_path(), items)


def get_custom_chunks() -> List[Dict[str, Any]]:
    # Always rebuild from editable custom rules so chunks stay in sync.
    return [rule_to_chunk(rule) for rule in load_custom_rules()]


def get_all_chunks_for_indexing() -> List[Dict[str, Any]]:
    return load_base_chunks() + get_custom_chunks()


def get_active_rule_ids() -> set[str]:
    ids = set()
    for chunk in load_base_chunks():
        rid = clean_text(chunk.get("rule_id"))
        if rid:
            ids.add(rid)
    for rule in load_custom_rules():
        rid = clean_text(rule.get("rule_id"))
        if rid:
            ids.add(rid)
    return ids


def get_original_rule_ids() -> set[str]:
    return {clean_text(chunk.get("rule_id")) for chunk in load_base_chunks() if chunk.get("rule_id")}


def get_custom_rule_ids() -> set[str]:
    return {clean_text(rule.get("rule_id")) for rule in load_custom_rules() if rule.get("rule_id")}


def get_all_rec_keys() -> set[str]:
    keys = set()
    for chunk in load_base_chunks():
        key = clean_text(chunk.get("rec_key"))
        if key:
            keys.add(key)
    for rule in load_custom_rules():
        key = clean_text(rule.get("rec_key"))
        if key:
            keys.add(key)
    return keys


# =============================================================================
# Rule lookup and conversion
# =============================================================================
def find_original_chunk(rule_id: str) -> Optional[Dict[str, Any]]:
    target = clean_text(rule_id)
    for chunk in load_base_chunks():
        if clean_text(chunk.get("rule_id")) == target:
            return dict(chunk)
    return None


def get_custom_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    target = clean_text(rule_id)
    for rule in load_custom_rules():
        if clean_text(rule.get("rule_id")) == target:
            return dict(rule)
    return None


def get_rule_source(rule_id: str) -> Optional[str]:
    target = clean_text(rule_id)
    if not target:
        return None
    if get_custom_rule(target):
        return "custom"
    if find_original_chunk(target):
        return "original"
    return None


def chunk_to_editable_rule(chunk: Dict[str, Any]) -> Dict[str, Any]:
    metric = metric_from_rule_like(chunk)
    source = source_from_rule_like(chunk, default="original")
    warn_low = chunk.get("warn_low")
    critical_value = chunk.get("critical_value") or chunk.get("warn_high_critical_starts_here")

    return {
        "source": source,
        "editable": True,
        "created_at": chunk.get("created_at"),
        "updated_at": chunk.get("updated_at"),
        "rule_id": clean_text(chunk.get("rule_id")),
        "rec_key": clean_text(chunk.get("rec_key") or f"REC_{chunk.get('rule_id') or ''}"),
        "stage": normalise_stage(chunk.get("stage")),
        "priority": clean_text(chunk.get("priority") or "P5").upper(),
        "attribution": normalise_attribution(chunk.get("attribution") or chunk.get("ml_label") or "Local"),
        "pattern": normalise_pattern(chunk.get("pattern") or "A"),
        "ml_label": normalise_attribution(chunk.get("ml_label") or chunk.get("attribution") or "Local"),
        "metric_name": metric,
        "threshold_metric_name": metric,
        "related_scoring_metrics": [metric] if metric else [],
        "warn_low": safe_float(warn_low, 0.0),
        "warn_high_critical_starts_here": safe_float(critical_value, 0.0),
        "critical_value": safe_float(critical_value, 0.0),
        "threshold_unit": clean_text(chunk.get("threshold_unit") or "normalised 0-1"),
        "score_warn": chunk.get("score_warn", -5),
        "score_crit": chunk.get("score_crit", -10),
        "cap": chunk.get("cap", 25),
        "recommendation_en": clean_text(chunk.get("recommendation_en")),
        "recommendation_bm": clean_text(chunk.get("recommendation_bm")),
        "urgency": clean_text(chunk.get("urgency") or "High").title(),
        "target_metric": clean_text(chunk.get("target_metric") or metric),
        "include_for_anomaly_retrieval": bool(chunk.get("include_for_anomaly_retrieval", True)),
    }


def build_rule_text(rule: Dict[str, Any]) -> Tuple[str, str]:
    rule_id = clean_text(rule.get("rule_id"))
    rec_key = clean_text(rule.get("rec_key") or f"REC_{rule_id}")
    metric = clean_text(rule.get("metric_name") or rule.get("threshold_metric_name"))
    warn_low = rule.get("warn_low")
    critical_value = rule.get("critical_value") or rule.get("warn_high_critical_starts_here")

    text_lines = [
        f"Rule ID: {rule_id}",
        f"Source: {rule.get('source')}",
        f"Priority: {rule.get('priority')}",
        f"Stage: {rule.get('stage')}",
        f"Attribution group: {rule.get('attribution')}",
        f"Pattern: {rule.get('pattern')}",
        f"ML label: {rule.get('ml_label') or rule.get('attribution')}",
        "",
        "Input signal(s):",
        metric,
        "",
        "Executable formula:",
        f"FLAG if {metric} > threshold",
        "",
        "Rule thresholds:",
        f"Warning threshold: {warn_low} < {metric} ≤ {critical_value}",
        f"Critical threshold: {metric} > {critical_value}",
        f"Score deduction when warning: {rule.get('score_warn')}",
        f"Score deduction when critical: {rule.get('score_crit')}",
        f"Attribution cap: {rule.get('cap')}",
        "",
        "Recommendation Matrix reference:",
        f"Recommendation key: {rec_key}",
        f"Root cause: {rule.get('attribution')}",
        f"Urgency: {rule.get('urgency')}",
        f"Target metric: {rule.get('target_metric')}",
        "",
        "Recommendation EN:",
        clean_text(rule.get("recommendation_en")),
    ]

    if clean_text(rule.get("recommendation_bm")):
        text_lines.extend(["", "Recommendation BM:", clean_text(rule.get("recommendation_bm"))])

    text = "\n".join(text_lines).strip()
    search_text = (
        f"RCA V4 Rule {rule_id}\n\n"
        f"Source {rule.get('source')}\n\n"
        f"Stage {rule.get('stage')}\n\n"
        f"Attribution {rule.get('attribution')}\n\n"
        f"Pattern {rule.get('pattern')}\n\n"
        f"Rec key {rec_key}\n\n"
        f"Related scoring metrics: {metric}\n\n"
        f"{text}"
    )
    return text, search_text


def rule_to_chunk(rule: Dict[str, Any], preserve_chunk: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = source_from_rule_like(rule, default="custom")
    rule_id = clean_text(rule.get("rule_id"))
    rec_key = clean_text(rule.get("rec_key") or f"REC_{rule_id}")
    metric = clean_text(rule.get("metric_name") or rule.get("threshold_metric_name"))
    critical_value = rule.get("critical_value") or rule.get("warn_high_critical_starts_here")

    text, search_text = build_rule_text(rule)

    if preserve_chunk and preserve_chunk.get("chunk_id"):
        chunk_id = clean_text(preserve_chunk.get("chunk_id"))
    elif source == "custom":
        chunk_id = f"custom_rca_rule_{slugify(rule_id).lower()}"
    else:
        chunk_id = f"original_rca_rule_{slugify(rule_id).lower()}"

    chunk = dict(preserve_chunk or {})
    chunk.update(
        {
            "chunk_id": chunk_id,
            "chunk_type": chunk.get("chunk_type") or "rca_rule_joined",
            "retrieval_scope": chunk.get("retrieval_scope") or "primary_rca_rule",
            "include_for_anomaly_retrieval": bool(rule.get("include_for_anomaly_retrieval", True)),
            "source": source,
            "editable": True,
            "source_file_name": chunk.get("source_file_name") or ("custom_rca_rules.json" if source == "custom" else get_base_chunks_path().name),
            "rule_id": rule_id,
            "priority": rule.get("priority"),
            "stage": rule.get("stage"),
            "attribution": rule.get("attribution"),
            "pattern": rule.get("pattern"),
            "ml_label": rule.get("ml_label") or rule.get("attribution"),
            "rec_key": rec_key,
            "related_scoring_metrics": [metric] if metric else [],
            "warn_threshold": f"{rule.get('warn_low')} < {metric} ≤ {critical_value}",
            "critical_threshold": f"{metric} > {critical_value}",
            "threshold_metric_name": metric,
            "threshold_unit": rule.get("threshold_unit") or "normalised 0-1",
            "warn_low": rule.get("warn_low"),
            "warn_high_critical_starts_here": critical_value,
            "critical_value": critical_value,
            "score_warn": rule.get("score_warn", -5),
            "score_crit": rule.get("score_crit", -10),
            "cap": rule.get("cap", 25),
            "recommendation_en": rule.get("recommendation_en"),
            "recommendation_bm": rule.get("recommendation_bm"),
            "urgency": rule.get("urgency"),
            "target_metric": rule.get("target_metric") or metric,
            "created_at": rule.get("created_at"),
            "updated_at": rule.get("updated_at"),
            "conflict_count_at_save": rule.get("conflict_count_at_save", 0),
            "conflict_acknowledged": rule.get("conflict_acknowledged", False),
            "text": text,
            "search_text": search_text,
        }
    )
    return chunk


def to_rule_summary(chunk: Dict[str, Any], *, source: Optional[str] = None) -> Dict[str, Any]:
    source_value = source or source_from_rule_like(chunk, default="original")
    metric = metric_from_rule_like(chunk)
    critical_value = chunk.get("critical_value") or chunk.get("warn_high_critical_starts_here")
    return {
        "source": source_value,
        "editable": True,
        "rule_id": chunk.get("rule_id"),
        "rec_key": chunk.get("rec_key"),
        "stage": chunk.get("stage"),
        "priority": chunk.get("priority"),
        "attribution": chunk.get("attribution"),
        "pattern": chunk.get("pattern"),
        "metric_name": metric,
        "warn_low": chunk.get("warn_low"),
        "critical_value": critical_value,
        "urgency": chunk.get("urgency"),
        "target_metric": chunk.get("target_metric"),
        "recommendation_en": chunk.get("recommendation_en"),
        "recommendation_bm": chunk.get("recommendation_bm"),
        "include_for_anomaly_retrieval": chunk.get("include_for_anomaly_retrieval", True),
        "created_at": chunk.get("created_at"),
        "updated_at": chunk.get("updated_at"),
        "conflict_count_at_save": chunk.get("conflict_count_at_save", 0),
        "conflict_acknowledged": chunk.get("conflict_acknowledged", False),
    }


# =============================================================================
# Validation and conflict detection
# =============================================================================
def _normalise_rule_id_for_source(rule_id_value: Any, source: str) -> str:
    if source == "custom":
        rule_id = slugify(rule_id_value)
        return rule_id if rule_id.startswith("CUSTOM_") else f"CUSTOM_{rule_id}"
    # Original IDs such as S2-001 should remain readable and stable.
    return clean_text(rule_id_value).upper()


def _normalise_rec_key_for_source(rec_key_value: Any, rule_id: str, source: str) -> str:
    if source == "custom":
        rec_key = slugify(rec_key_value or f"CUSTOM_REC_{rule_id.replace('CUSTOM_', '')}")
        return rec_key if rec_key.startswith("CUSTOM_") else f"CUSTOM_{rec_key}"
    return clean_text(rec_key_value or f"REC_{rule_id}").upper()


def normalise_rule_payload(
    payload: Dict[str, Any],
    *,
    source: str,
    existing_rule_id: Optional[str] = None,
    old_rule: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if source not in VALID_SOURCES:
        raise ValueError("Rule source must be original or custom.")

    data = dict(payload or {})
    now = now_malaysia_iso()

    rule_id_raw = existing_rule_id or data.get("rule_id")
    rule_id = _normalise_rule_id_for_source(rule_id_raw, source)

    stage = normalise_stage(data.get("stage") or (old_rule or {}).get("stage"))
    priority = clean_text(data.get("priority") or (old_rule or {}).get("priority") or "P5").upper()
    attribution = normalise_attribution(data.get("attribution") or (old_rule or {}).get("attribution") or "Local")
    pattern = normalise_pattern(data.get("pattern") or (old_rule or {}).get("pattern") or "A")
    metric_name = clean_text(
        data.get("metric_name")
        or data.get("threshold_metric_name")
        or (old_rule or {}).get("metric_name")
        or (old_rule or {}).get("threshold_metric_name")
    )

    warn_low = to_float(data.get("warn_low", (old_rule or {}).get("warn_low")), "Warning threshold")
    critical_value = to_float(
        data.get(
            "critical_value",
            (old_rule or {}).get("critical_value") or (old_rule or {}).get("warn_high_critical_starts_here"),
        ),
        "Critical threshold",
    )

    recommendation_en = clean_text(data.get("recommendation_en") or (old_rule or {}).get("recommendation_en"))
    recommendation_bm = clean_text(data.get("recommendation_bm") or (old_rule or {}).get("recommendation_bm"))
    urgency = clean_text(data.get("urgency") or (old_rule or {}).get("urgency") or "High").title()
    target_metric = clean_text(data.get("target_metric") or (old_rule or {}).get("target_metric") or metric_name)
    include_for_anomaly_retrieval = bool(data.get("include_for_anomaly_retrieval", (old_rule or {}).get("include_for_anomaly_retrieval", True)))
    rec_key = _normalise_rec_key_for_source(data.get("rec_key") or (old_rule or {}).get("rec_key"), rule_id, source)

    if not rule_id:
        raise ValueError("Rule ID is required.")
    if stage not in VALID_STAGES:
        raise ValueError("Stage must be one of S1, S2, S3, CY, or EX.")
    if priority not in VALID_PRIORITIES:
        raise ValueError("Priority must be one of P1, P2, P3, P4, or P5.")
    if attribution not in VALID_ATTRIBUTIONS:
        raise ValueError("Attribution must be Boiler, BPV, Competition, Network, or Local.")
    if urgency not in VALID_URGENCIES:
        raise ValueError("Urgency must be Low, Medium, High, or Critical.")
    if not pattern or not re.fullmatch(r"[A-F](\+[A-F])*", pattern):
        raise ValueError("Pattern must be A, B, C, D, E, F, or a combination such as A+F.")

    allowed_metrics = set(METRIC_OPTIONS.get(stage, []))
    if metric_name not in allowed_metrics:
        raise ValueError(
            f"Metric name is not valid for {stage}. Please choose one of: "
            + ", ".join(METRIC_OPTIONS.get(stage, []))
        )

    if warn_low < 0 or critical_value < 0:
        raise ValueError("Warning and critical thresholds must be 0 or above.")
    if critical_value <= warn_low:
        raise ValueError("Critical threshold must be greater than warning threshold.")
    if not recommendation_en:
        raise ValueError("Recommendation EN is required.")

    # Duplicates are still blocked. Conflict warning is separate from duplicate ID.
    for existing_id in get_active_rule_ids():
        if existing_rule_id and clean_text(existing_id) == clean_text(existing_rule_id):
            continue
        if clean_text(existing_id) == rule_id:
            raise ValueError("This Rule ID already exists.")

    for existing_key in get_all_rec_keys():
        old_key = clean_text((old_rule or {}).get("rec_key"))
        if old_key and clean_text(existing_key) == old_key:
            continue
        if clean_text(existing_key) == rec_key:
            raise ValueError("This Rec Key already exists.")

    return {
        "source": source,
        "editable": True,
        "created_at": (old_rule or {}).get("created_at") or now,
        "updated_at": now,
        "rule_id": rule_id,
        "rec_key": rec_key,
        "stage": stage,
        "priority": priority,
        "attribution": attribution,
        "pattern": pattern,
        "ml_label": attribution,
        "metric_name": metric_name,
        "threshold_metric_name": metric_name,
        "related_scoring_metrics": [metric_name],
        "warn_low": warn_low,
        "warn_high_critical_starts_here": critical_value,
        "critical_value": critical_value,
        "threshold_unit": clean_text(data.get("threshold_unit") or (old_rule or {}).get("threshold_unit") or "normalised 0-1"),
        "score_warn": data.get("score_warn", (old_rule or {}).get("score_warn", -5)),
        "score_crit": data.get("score_crit", (old_rule or {}).get("score_crit", -10)),
        "cap": data.get("cap", (old_rule or {}).get("cap", 25)),
        "recommendation_en": recommendation_en,
        "recommendation_bm": recommendation_bm,
        "urgency": urgency,
        "target_metric": target_metric,
        "include_for_anomaly_retrieval": include_for_anomaly_retrieval,
    }


def _rule_conflict_item(existing: Dict[str, Any], candidate: Dict[str, Any], *, source: str) -> Optional[Dict[str, Any]]:
    """Return a conflict only for a strong duplicate rule.

    Strong conflict means:
    - same stage
    - same scoring metric
    - overlapping pattern, for example A conflicts with A+F
    - same attribution/root cause

    Rules with the same pressure feature but different root causes are not treated
    as conflicts because they are valid alternative RCA candidates.
    """
    existing_rule_id = clean_text(existing.get("rule_id"))
    candidate_rule_id = clean_text(candidate.get("rule_id"))
    if existing_rule_id and candidate_rule_id and existing_rule_id == candidate_rule_id:
        return None

    existing_stage = normalise_stage(existing.get("stage"))
    candidate_stage = normalise_stage(candidate.get("stage"))
    if existing_stage != candidate_stage:
        return None

    existing_metric = metric_from_rule_like(existing)
    candidate_metric = metric_from_rule_like(candidate)
    if not existing_metric or not candidate_metric or existing_metric != candidate_metric:
        return None

    if not pattern_overlap(existing.get("pattern"), candidate.get("pattern")):
        return None

    existing_attr = normalise_attribution(existing.get("attribution") or existing.get("ml_label"))
    candidate_attr = normalise_attribution(candidate.get("attribution") or candidate.get("ml_label"))
    if existing_attr != candidate_attr:
        return None

    return {
        "rule_id": existing_rule_id,
        "source": source,
        "stage": existing_stage,
        "metric_name": existing_metric,
        "pattern": existing.get("pattern"),
        "candidate_pattern": candidate.get("pattern"),
        "attribution": existing_attr,
        "same_attribution": True,
        "warning_threshold": existing.get("warn_low"),
        "critical_threshold": existing.get("critical_value") or existing.get("warn_high_critical_starts_here"),
        "recommendation_en": existing.get("recommendation_en"),
        "conflict_type": "strong_same_feature_same_root_cause",
    }


def find_rule_conflicts(candidate: Dict[str, Any], *, existing_rule_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    skip_id = clean_text(existing_rule_id)

    for chunk in load_base_chunks():
        if skip_id and clean_text(chunk.get("rule_id")) == skip_id:
            continue
        conflict = _rule_conflict_item(chunk, candidate, source="original")
        if conflict:
            conflicts.append(conflict)

    for rule in load_custom_rules():
        if skip_id and clean_text(rule.get("rule_id")) == skip_id:
            continue
        conflict = _rule_conflict_item(rule, candidate, source="custom")
        if conflict:
            conflicts.append(conflict)

    conflicts.sort(
        key=lambda item: (
            1 if clean_text(item.get("pattern")) == clean_text(candidate.get("pattern")) else 0,
            str(item.get("source")),
            str(item.get("rule_id")),
        ),
        reverse=True,
    )
    return conflicts


def build_conflict_warning_message(conflicts: List[Dict[str, Any]]) -> str:
    if not conflicts:
        return ""
    first = conflicts[0]
    if len(conflicts) == 1:
        return (
            "Another RCA rule already uses the same stage feature and same root cause: "
            f"{first.get('stage')} / {first.get('metric_name')} / pattern {first.get('pattern')} / root cause {first.get('attribution')}."
        )
    return (
        f"{len(conflicts)} RCA rules already use the same stage feature and same root cause. "
        "Saving another duplicate may create competing recommendations."
    )


def apply_conflict_policy(candidate: Dict[str, Any], *, existing_rule_id: Optional[str], allow_conflict: bool) -> Dict[str, Any]:
    conflicts = find_rule_conflicts(candidate, existing_rule_id=existing_rule_id)
    candidate["conflict_count_at_save"] = len(conflicts)
    candidate["conflict_acknowledged"] = bool(conflicts and allow_conflict)

    if conflicts and not allow_conflict:
        raise RuleConflictWarning(
            message=build_conflict_warning_message(conflicts),
            conflicts=conflicts,
            candidate=candidate,
        )
    return candidate


# =============================================================================
# CRUD operations
# =============================================================================
def list_rules(include_original: bool = True) -> Dict[str, Any]:
    original_chunks = load_base_chunks() if include_original else []
    custom_rules = load_custom_rules()
    custom_chunks = [rule_to_chunk(rule) for rule in custom_rules]

    original_items = [to_rule_summary(chunk, source="original") for chunk in original_chunks if chunk.get("rule_id")]
    custom_items = [to_rule_summary(chunk, source="custom") for chunk in custom_chunks if chunk.get("rule_id")]

    return {
        "rules": original_items + custom_items,
        "original_count": len(original_items),
        "custom_count": len(custom_items),
        "metric_options": METRIC_OPTIONS,
        "stage_options": sorted(VALID_STAGES),
        "priority_options": sorted(VALID_PRIORITIES),
        "attribution_options": sorted(VALID_ATTRIBUTIONS),
        "urgency_options": sorted(VALID_URGENCIES),
        "original_rules_editable": True,
        "custom_rules_editable": True,
    }


def create_custom_rule(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # Creating new rules always creates a custom rule. Original rules already exist.
    candidate = normalise_rule_payload(payload, source="custom")
    candidate = apply_conflict_policy(
        candidate,
        existing_rule_id=None,
        allow_conflict=bool((payload or {}).get("allow_conflict")),
    )

    rules = load_custom_rules()
    rules.append(candidate)
    save_custom_rules(rules)
    reindex_result = upsert_rule_chunks_to_qdrant([rule_to_chunk(candidate)])
    return candidate, reindex_result


def update_custom_rule(rule_id: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # Function name kept for backward compatibility with main.py. It now updates
    # either original or custom rules.
    return update_rule(rule_id, payload)


def update_rule(rule_id: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    target = clean_text(rule_id)
    source = get_rule_source(target)

    if source == "custom":
        rules = load_custom_rules()
        idx = next((i for i, item in enumerate(rules) if clean_text(item.get("rule_id")) == target), None)
        if idx is None:
            raise ValueError("Custom rule not found.")

        old_rule = dict(rules[idx])
        merged = {**old_rule, **dict(payload or {})}
        merged["rule_id"] = target
        candidate = normalise_rule_payload(merged, source="custom", existing_rule_id=target, old_rule=old_rule)
        candidate = apply_conflict_policy(
            candidate,
            existing_rule_id=target,
            allow_conflict=bool((payload or {}).get("allow_conflict")),
        )
        rules[idx] = candidate
        save_custom_rules(rules)
        reindex_result = upsert_rule_chunks_to_qdrant([rule_to_chunk(candidate)])
        return candidate, reindex_result

    if source == "original":
        chunks = load_base_chunks()
        idx = next((i for i, item in enumerate(chunks) if clean_text(item.get("rule_id")) == target), None)
        if idx is None:
            raise ValueError("Original rule not found.")

        old_chunk = dict(chunks[idx])
        old_rule = chunk_to_editable_rule(old_chunk)
        merged = {**old_rule, **dict(payload or {})}
        merged["rule_id"] = target
        candidate = normalise_rule_payload(merged, source="original", existing_rule_id=target, old_rule=old_rule)
        candidate = apply_conflict_policy(
            candidate,
            existing_rule_id=target,
            allow_conflict=bool((payload or {}).get("allow_conflict")),
        )
        updated_chunk = rule_to_chunk(candidate, preserve_chunk=old_chunk)
        chunks[idx] = updated_chunk
        save_base_chunks(chunks)
        reindex_result = upsert_rule_chunks_to_qdrant([updated_chunk])
        return candidate, reindex_result

    raise ValueError("Rule not found.")


def delete_custom_rule(rule_id: str) -> Dict[str, Any]:
    # Function name kept for backward compatibility with main.py. It now deletes
    # either original or custom rules.
    return delete_rule(rule_id)


def delete_rule(rule_id: str) -> Dict[str, Any]:
    target = clean_text(rule_id)
    source = get_rule_source(target)

    if source == "custom":
        rules = load_custom_rules()
        removed_rule = next((rule for rule in rules if clean_text(rule.get("rule_id")) == target), None)
        kept = [rule for rule in rules if clean_text(rule.get("rule_id")) != target]
        if removed_rule is None:
            raise ValueError("Custom rule not found.")
        save_custom_rules(kept)
        chunk_id = rule_to_chunk(removed_rule).get("chunk_id")
        qdrant = delete_chunks_from_qdrant([chunk_id])
        return {
            "status": "deleted",
            "source": "custom",
            "rule_id": target,
            "deleted_chunks": [chunk_id],
            "qdrant_delete": qdrant,
        }

    if source == "original":
        chunks = load_base_chunks()
        removed_chunk = next((chunk for chunk in chunks if clean_text(chunk.get("rule_id")) == target), None)
        kept = [chunk for chunk in chunks if clean_text(chunk.get("rule_id")) != target]
        if removed_chunk is None:
            raise ValueError("Original rule not found.")
        save_base_chunks(kept)

        deleted_log = load_deleted_original_rules()
        deleted_log.append(
            {
                "deleted_at": now_malaysia_iso(),
                "rule_id": target,
                "rec_key": removed_chunk.get("rec_key"),
                "chunk_id": removed_chunk.get("chunk_id"),
                "stage": removed_chunk.get("stage"),
                "metric_name": metric_from_rule_like(removed_chunk),
                "pattern": removed_chunk.get("pattern"),
                "recommendation_en": removed_chunk.get("recommendation_en"),
            }
        )
        save_deleted_original_rules(deleted_log)

        qdrant = delete_chunks_from_qdrant([removed_chunk.get("chunk_id")])
        return {
            "status": "deleted",
            "source": "original",
            "rule_id": target,
            "deleted_chunks": [removed_chunk.get("chunk_id")],
            "qdrant_delete": qdrant,
        }

    raise ValueError("Rule not found.")


# =============================================================================
# Qdrant indexing helpers
# =============================================================================
def upsert_rule_chunks_to_qdrant(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not chunks:
        return {"indexed_chunks": 0, "status": "no_rules"}

    try:
        from app.rag.embedding_service import embed_texts
        from app.rag.vector_db import ensure_collection, upsert_chunks

        texts = [chunk.get("search_text") or chunk.get("text") or "" for chunk in chunks]
        vectors = embed_texts(texts, batch_size=8)
        ensure_collection(recreate=False)
        indexed = upsert_chunks(chunks, vectors)
        return {
            "status": "indexed",
            "indexed_chunks": indexed,
            "chunk_ids": [chunk.get("chunk_id") for chunk in chunks],
        }
    except Exception as exc:
        return {
            "status": "index_failed",
            "indexed_chunks": 0,
            "error": str(exc),
            "chunk_ids": [chunk.get("chunk_id") for chunk in chunks],
        }


def upsert_custom_rules_to_qdrant(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    return upsert_rule_chunks_to_qdrant([rule_to_chunk(rule) for rule in rules])


def reindex_all_custom_rules_to_qdrant() -> Dict[str, Any]:
    return upsert_custom_rules_to_qdrant(load_custom_rules())


def reindex_all_rules_to_qdrant() -> Dict[str, Any]:
    return upsert_rule_chunks_to_qdrant(get_all_chunks_for_indexing())


def delete_chunks_from_qdrant(chunk_ids: List[Any]) -> Dict[str, Any]:
    clean_ids = [clean_text(chunk_id) for chunk_id in chunk_ids if clean_text(chunk_id)]
    if not clean_ids:
        return {"status": "no_chunk_ids", "deleted_chunks": 0, "chunk_ids": []}

    try:
        from app.rag.vector_db import delete_chunks

        deleted = delete_chunks(clean_ids)
        return {"status": "deleted_from_qdrant", "deleted_chunks": deleted, "chunk_ids": clean_ids}
    except Exception as exc:
        return {"status": "delete_from_qdrant_failed", "deleted_chunks": 0, "chunk_ids": clean_ids, "error": str(exc)}


def delete_custom_rule_from_qdrant(rule_id: str) -> Dict[str, Any]:
    # Kept for old imports. Prefer delete_rule().
    target = clean_text(rule_id)
    source = get_rule_source(target)
    if source == "custom":
        rule = get_custom_rule(target)
        chunk_id = rule_to_chunk(rule).get("chunk_id") if rule else f"custom_rca_rule_{slugify(target).lower()}"
    else:
        chunk = find_original_chunk(target)
        chunk_id = chunk.get("chunk_id") if chunk else f"original_rca_rule_{slugify(target).lower()}"
    return delete_chunks_from_qdrant([chunk_id])


# =============================================================================
# Verification helpers
# =============================================================================
def verify_rule_installation(rule_id: str) -> Dict[str, Any]:
    target = clean_text(rule_id)
    source = get_rule_source(target)

    result: Dict[str, Any] = {
        "rule_id": target,
        "source": source,
        "base_chunk_file": str(get_base_chunks_path()),
        "custom_rule_file": str(get_custom_rules_path()),
        "custom_chunk_file": str(get_custom_chunks_path()),
        "rule_saved_in_json": False,
        "chunk_saved_in_json": False,
        "direct_retrieval_ready": False,
        "qdrant_indexed": False,
        "qdrant_status": "not_checked",
        "qdrant_error": None,
        "chunk_id": None,
    }

    if source == "custom":
        rule = get_custom_rule(target)
        if not rule:
            result.update({"status": "missing_rule_json", "ready_for_immediate_rca": False})
            return result
        chunk = rule_to_chunk(rule)
        result["rule_saved_in_json"] = True
        chunk_file = get_custom_chunks_path()
        try:
            chunk_data = read_json_file(chunk_file, [])
            result["chunk_saved_in_json"] = any(clean_text(item.get("chunk_id")) == clean_text(chunk.get("chunk_id")) for item in chunk_data if isinstance(item, dict))
        except Exception as exc:
            result["chunk_json_error"] = str(exc)
    elif source == "original":
        chunk = find_original_chunk(target)
        if not chunk:
            result.update({"status": "missing_original_chunk", "ready_for_immediate_rca": False})
            return result
        result["rule_saved_in_json"] = True
        result["chunk_saved_in_json"] = True
    else:
        result.update({"status": "missing_rule", "ready_for_immediate_rca": False})
        return result

    chunk_id = chunk.get("chunk_id")
    result["chunk_id"] = chunk_id

    try:
        matching = get_matching_rule_chunks(
            stage=chunk.get("stage"),
            metric=metric_from_rule_like(chunk),
            pattern=chunk.get("pattern"),
        )
        result["direct_retrieval_ready"] = any(clean_text(item.get("rule_id")) == target for item in matching)
        result["matching_rule_chunk_count"] = len(matching)
    except Exception as exc:
        result["direct_retrieval_error"] = str(exc)

    try:
        from app.rag.rag_config import get_rag_settings
        from app.rag.vector_db import get_qdrant_client, deterministic_point_id

        settings = get_rag_settings()
        client = get_qdrant_client()
        point_id = deterministic_point_id(str(chunk_id))
        points = client.retrieve(
            collection_name=settings["qdrant_collection"],
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        result["qdrant_indexed"] = bool(points)
        result["qdrant_status"] = "indexed" if points else "missing"
    except Exception as exc:
        result["qdrant_status"] = "check_failed"
        result["qdrant_error"] = str(exc)

    result["ready_for_immediate_rca"] = bool(
        result["rule_saved_in_json"]
        and result["chunk_saved_in_json"]
        and result["direct_retrieval_ready"]
    )

    if result["ready_for_immediate_rca"] and result["qdrant_indexed"]:
        result["status"] = "fully_ready"
    elif result["ready_for_immediate_rca"]:
        result["status"] = "ready_json_direct_qdrant_pending"
    else:
        result["status"] = "not_ready"

    return result


def verify_custom_rule_installation(rule_id: str) -> Dict[str, Any]:
    # Kept for backward compatibility with main.py imports.
    return verify_rule_installation(rule_id)


# =============================================================================
# Matching helpers used by retriever.py
# =============================================================================
def pattern_matches(chunk_pattern: str, requested_pattern: Optional[str]) -> bool:
    if not requested_pattern:
        return True
    requested_parts = pattern_parts(requested_pattern)
    chunk_parts = pattern_parts(chunk_pattern)
    if not requested_parts:
        return True
    if not chunk_parts:
        return False
    return bool(requested_parts.intersection(chunk_parts))


def metric_matches(chunk: Dict[str, Any], metric: Optional[str]) -> bool:
    if not metric:
        return True
    metric = clean_text(metric)
    related = chunk.get("related_scoring_metrics") or []
    if metric in related:
        return True
    text = chunk.get("search_text") or chunk.get("text") or ""
    return metric in str(text)


def get_matching_rule_chunks(
    *,
    stage: Optional[str] = None,
    metric: Optional[str] = None,
    pattern: Optional[str] = None,
) -> List[Dict[str, Any]]:
    stage_norm = normalise_stage(stage) if stage else None
    output: List[Dict[str, Any]] = []

    for chunk in load_base_chunks() + get_custom_chunks():
        if chunk.get("include_for_anomaly_retrieval") is False:
            continue
        if stage_norm and chunk.get("stage") != stage_norm:
            continue
        if metric and not metric_matches(chunk, metric):
            continue
        if pattern and not pattern_matches(chunk.get("pattern", ""), pattern):
            continue
        output.append(chunk)

    # If too strict, relax metric/pattern but keep stage for testing newly edited rules.
    if not output and stage_norm and (metric or pattern):
        for chunk in load_base_chunks() + get_custom_chunks():
            if chunk.get("include_for_anomaly_retrieval") is False:
                continue
            if chunk.get("stage") == stage_norm:
                output.append(chunk)

    return output


def get_matching_custom_chunks(
    *,
    stage: Optional[str] = None,
    metric: Optional[str] = None,
    pattern: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # Backward compatibility for older retriever.py. New retriever.py uses
    # get_matching_rule_chunks() so edited original rules are also immediately
    # available from JSON.
    stage_norm = normalise_stage(stage) if stage else None
    output: List[Dict[str, Any]] = []
    for chunk in get_custom_chunks():
        if chunk.get("include_for_anomaly_retrieval") is False:
            continue
        if stage_norm and chunk.get("stage") != stage_norm:
            continue
        if metric and not metric_matches(chunk, metric):
            continue
        if pattern and not pattern_matches(chunk.get("pattern", ""), pattern):
            continue
        output.append(chunk)
    return output
