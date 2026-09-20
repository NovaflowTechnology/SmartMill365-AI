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
from app.services.audit_service import record_audit_event
from app.services.analysis_rule_storage_service import (
    delete_rule_record,
    get_rule_row,
    list_reference_chunk_rows,
    list_rule_rows,
    save_reference_chunk,
    save_rule_record,
    set_index_status,
    set_reference_index_status,
)


# =============================================================================
# Purpose
# =============================================================================
# This service powers RCA Rule Management.
#
# New behavior in this version:
# - Original Excel-derived rules can be edited and deleted.
# - Custom rules can be created, edited, and deleted.
# - Supabase is authoritative for original and custom rules; bundled JSON/XLSX
#   files are migration/backup sources only.
# - Every save writes Supabase first, then upserts the rebuildable Qdrant index,
#   and remains directly available to RCA retrieval even if indexing fails.
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
        "MAE_s1_full",
        "RMSE_s1_full",
        "combined_s1_full",
        "osc_ratio_s1_full",
    ],
    "S2": [
        "MAE_s2_peak",
        "MAE_s2_ramp",
        "MAE_s2_release",
        "MAE_s2_full",
        "RMSE_s2_full",
        "combined_s2_full",
        "osc_ratio_s2_full",
    ],
    "S3": [
        "MAE_s3_hold",
        "RMSE_s3_hold",
        "MAE_s3_ramp",
        "MAE_s3_full",
        "RMSE_s3_full",
        "combined_s3_hold",
        "combined_s3_full",
        "osc_ratio_s3_hold",
    ],
    "CY": [
        "boiler_recur",
        "bpv_recur",
        "comp_recur",
        "net_recur",
        "local_recur",
        "Boiler rule trigger rate",
        "BPV rule trigger rate",
        "Competition trigger rate",
        "Network trigger rate",
        "Local trigger rate",
        "maintenance history",
        "duration_error_ratio",
        "duration_ratio",
        "cycle_score",
    ],
    "EX": [
        "exhaust_residual_pressure",
        "exhaust_release_time",
    ],
}

CY_RULE_METRIC_MAP = {
    "CY-001": {
        "metric": "boiler_recur",
        "display": "Boiler rule trigger rate",
        "description": "Measures the proportion of recent cycles where boiler-related analysis rules were triggered. Higher value means boiler-related steam supply problems are recurring across cycles.",
        "target_metric": "Boiler rule trigger rate",
        "supporting_context": "rolling last 10 cycles",
    },
    "CY-002": {
        "metric": "bpv_recur",
        "display": "BPV rule trigger rate",
        "description": "Measures the proportion of recent cycles where BPV or steam pressure control valve analysis rules were triggered. Higher value means BPV-related pressure control problems are recurring across cycles.",
        "target_metric": "BPV rule trigger rate",
        "supporting_context": "rolling last 10 cycles",
    },
    "CY-003": {
        "metric": "comp_recur",
        "display": "Competition trigger rate",
        "description": "Measures the proportion of recent cycles where steam competition analysis rules were triggered. Higher value means scheduling or concurrent sterilizer demand issues are recurring.",
        "target_metric": "Competition trigger rate",
        "supporting_context": "rolling last 10 cycles",
    },
    "CY-004": {
        "metric": "net_recur",
        "display": "Network trigger rate",
        "description": "Measures the proportion of recent cycles where steam network or distribution analysis rules were triggered. Higher value means steam distribution problems are recurring in the header or branch network.",
        "target_metric": "Network trigger rate",
        "supporting_context": "rolling last 10 cycles",
    },
    "CY-005": {
        "metric": "local_recur",
        "display": "Local trigger rate",
        "description": "Measures the proportion of recent cycles where local sterilizer analysis rules were triggered for the selected unit. Higher value suggests recurring local mechanical or equipment-condition problems. Maintenance history is supporting context, not the numeric scoring metric.",
        "target_metric": "Local trigger rate",
        "supporting_context": "maintenance history",
    },
}

CY_RECUR_WARN_LOW = 0.20
CY_RECUR_CRITICAL_VALUE = 0.40


METRIC_DESCRIPTIONS = {
    # Stage 1 pressure-shape metrics
    "MAE_s1_peak": "Measures the pressure difference at the first pressurization peak. Higher value means the Stage 1 peak is further from the normal reference peak.",
    "MAE_s1_ramp": "Measures how different the Stage 1 pressure rising section is from the normal reference. Higher value usually means the pressure rises too slowly or has an abnormal ramp shape.",
    "MAE_s1_release": "Measures how different the Stage 1 pressure release section is from the normal reference. Higher value means the pressure release between phases is abnormal.",
    "MAE_s1_full": "Measures the overall pressure-profile difference across the whole first pressurization phase. Higher value means Stage 1 as a whole does not follow the normal operating profile.",
    "combined_s1_full": "Combined Stage 1 full-profile error used for scoring. It summarizes the overall Stage 1 pressure behavior into one score-related metric.",
    "osc_ratio_s1_full": "Measures pressure fluctuation or instability during Stage 1. Higher value means the Stage 1 pressure movement is more unstable than expected.",

    # Stage 2 pressure-shape metrics
    "MAE_s2_peak": "Measures the pressure difference at the second pressurization peak. Higher value means the Stage 2 peak is further from the normal reference peak.",
    "MAE_s2_ramp": "Measures how different the Stage 2 pressure rising section is from the normal reference. Higher value usually means the pressure rises too slowly or has an abnormal ramp shape.",
    "MAE_s2_release": "Measures how different the Stage 2 pressure release section is from the normal reference. Higher value means the pressure release between phases is abnormal.",
    "MAE_s2_full": "Measures the overall pressure-profile difference across the whole second pressurization phase. Higher value means Stage 2 as a whole does not follow the normal operating profile.",
    "combined_s2_full": "Combined Stage 2 full-profile error used for scoring. It summarizes the overall Stage 2 pressure behavior into one score-related metric.",
    "osc_ratio_s2_full": "Measures pressure fluctuation or instability during Stage 2. Higher value means the Stage 2 pressure movement is more unstable than expected.",

    # Stage 3 holding metrics
    "MAE_s3_hold": "Measures how different the pressure holding level is during Stage 3. Higher value means the holding pressure is further from the normal reference level.",
    "RMSE_s3_hold": "Measures the strength of pressure instability during the Stage 3 holding period. Higher value means the holding pressure is less stable.",
    "MAE_s3_ramp": "Measures how different the pressure rise into Stage 3 is from the normal reference. Higher value means the Stage 3 ramp-up behavior is abnormal.",
    "MAE_s3_full": "Measures the overall pressure-profile difference across the full pressure holding phase. Higher value means Stage 3 as a whole does not follow the normal operating profile.",
    "combined_s3_hold": "Combined Stage 3 holding error used for scoring. It summarizes holding pressure level and stability into one score-related metric.",
    "combined_s3_full": "Combined Stage 3 full-profile error used for scoring. It summarizes the overall Stage 3 pressure behavior into one score-related metric.",
    "osc_ratio_s3_hold": "Measures oscillation or fluctuation during the Stage 3 holding period. Higher value means the holding pressure is unstable.",

    # Overall cycle / scoring summary metrics
    "cycle_score": "Final overall cycle score calculated from the pressure-profile and cycle-duration components. Lower score means the cycle needs more attention.",
    "duration_error_ratio": "Absolute cycle-duration difference relative to the selected benchmark. For example, 0.20 means the cycle duration differs from the benchmark by 20%.",
    "duration_ratio": "Actual cycle duration divided by benchmark duration. A value of 1.00 matches the benchmark; values below or above 1.00 indicate a shorter or longer cycle.",

    # Exhaust/release metrics
    "exhaust_residual_pressure": "Measures remaining pressure after the exhaust or release phase. Higher value may mean pressure was not fully released.",
    "exhaust_release_time": "Measures how long pressure release takes during the exhaust phase. Higher value may mean the pressure release is too slow or restricted.",

    # RCA trigger-rate/supporting metrics
    "Boiler rule trigger rate": "Measures how often boiler-related analysis rules are triggered in the analyzed data. Higher value suggests repeated or shared steam supply problems related to boiler output or steam demand.",
    "BPV rule trigger rate": "Measures how often BPV or steam pressure control valve analysis rules are triggered. Higher value suggests recurring pressure control or valve regulation problems.",
    "Competition trigger rate": "Measures how often steam competition analysis rules are triggered. Higher value suggests multiple sterilizers may be demanding steam at the same time and competing for available steam supply.",
    "Network trigger rate": "Measures how often steam network or distribution analysis rules are triggered. Higher value suggests uneven steam distribution through the main steam line or header network.",
    "Local trigger rate": "Measures how often local sterilizer analysis rules are triggered. Higher value suggests the selected sterilizer may have its own local mechanical or equipment condition issue.",
    "maintenance history": "Represents recent maintenance records or known equipment history used as supporting analysis context. It helps users check whether the abnormal pressure behavior may be related to past servicing, faults, or unresolved maintenance issues.",

    # Common machine-readable aliases for the same supporting metrics
    "boiler_rule_trigger_rate": "Measures how often boiler-related analysis rules are triggered in the analyzed data. Higher value suggests repeated or shared steam supply problems related to boiler output or steam demand.",
    "bpv_rule_trigger_rate": "Measures how often BPV or steam pressure control valve analysis rules are triggered. Higher value suggests recurring pressure control or valve regulation problems.",
    "competition_trigger_rate": "Measures how often steam competition analysis rules are triggered. Higher value suggests multiple sterilizers may be demanding steam at the same time and competing for available steam supply.",
    "network_trigger_rate": "Measures how often steam network or distribution analysis rules are triggered. Higher value suggests uneven steam distribution through the main steam line or header network.",
    "local_trigger_rate": "Measures how often local sterilizer analysis rules are triggered. Higher value suggests the selected sterilizer may have its own local mechanical or equipment condition issue.",
    "maintenance_history": "Represents recent maintenance records or known equipment history used as supporting analysis context. It helps users check whether the abnormal pressure behavior may be related to past servicing, faults, or unresolved maintenance issues.",

    # Chronic recurrence metrics used by CY-001~CY-005. These are the numeric
    # metrics from the executable formulas in the Excel Rules Master sheet.
    "boiler_recur": "Measures the proportion of recent cycles where boiler-related analysis rules were triggered. Warning starts at 20%, and critical starts above 40% recurrence.",
    "bpv_recur": "Measures the proportion of recent cycles where BPV-related analysis rules were triggered. Warning starts at 20%, and critical starts above 40% recurrence.",
    "comp_recur": "Measures the proportion of recent cycles where steam competition analysis rules were triggered. Warning starts at 20%, and critical starts above 40% recurrence.",
    "net_recur": "Measures the proportion of recent cycles where steam network or distribution analysis rules were triggered. Warning starts at 20%, and critical starts above 40% recurrence.",
    "local_recur": "Measures the proportion of recent cycles where local sterilizer analysis rules were triggered for the selected unit. Warning starts at 20%, and critical starts above 40% recurrence. Maintenance history is supporting context, not the numeric metric.",
    "Local trigger rate, maintenance history": "Local trigger rate measures recurring local analysis triggers for the selected sterilizer. Maintenance history is supporting context used to check whether past faults or servicing may explain the recurring local issue.",
}

CUSTOM_METRIC_DEFAULT_DESCRIPTION = (
    "Custom metric. Please make sure this exact metric name exists in the scoring output; "
    "otherwise the rule can be saved but it will not trigger during analysis evaluation."
)


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
    raw_metric = ""
    if related:
        raw_metric = clean_text(related[0])
    else:
        raw_metric = clean_text(
            rule_or_chunk.get("metric_name")
            or rule_or_chunk.get("threshold_metric_name")
            or rule_or_chunk.get("target_metric")
        )

    return canonical_metric_name(
        raw_metric,
        rule_id=rule_or_chunk.get("rule_id"),
        stage=rule_or_chunk.get("stage"),
        attribution=rule_or_chunk.get("attribution"),
    )


def source_from_rule_like(rule_or_chunk: Dict[str, Any], default: str = "original") -> str:
    source = clean_text(rule_or_chunk.get("source") or default).lower()
    return source if source in VALID_SOURCES else default



def normalise_metric_key(value: Any) -> str:
    # Keep the user-facing metric name, but remove accidental surrounding spaces.
    return clean_text(value)


def metric_lookup_key(value: Any) -> str:
    """Normalise metric names for alias lookup.

    This allows these to match the same known metric:
    - MAE_S1_FULL / mae_s1_full / MAE s1 full
    - Boiler rule trigger rate / boiler_rule_trigger_rate
    - Local trigger rate, maintenance history / local_recur
    """
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[,/]+", " ", text)
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


METRIC_ALIAS_TO_CANONICAL = {
    "boiler_rule_trigger_rate": "boiler_recur",
    "boiler_trigger_rate": "boiler_recur",
    "boiler_recurrence_rate": "boiler_recur",
    "bpv_rule_trigger_rate": "bpv_recur",
    "bpv_trigger_rate": "bpv_recur",
    "bpv_recurrence_rate": "bpv_recur",
    "competition_rule_trigger_rate": "comp_recur",
    "competition_trigger_rate": "comp_recur",
    "competition_recurrence_rate": "comp_recur",
    "comp_trigger_rate": "comp_recur",
    "network_rule_trigger_rate": "net_recur",
    "network_trigger_rate": "net_recur",
    "network_recurrence_rate": "net_recur",
    "net_trigger_rate": "net_recur",
    "local_rule_trigger_rate": "local_recur",
    "local_trigger_rate": "local_recur",
    "local_recurrence_rate": "local_recur",
    "local_trigger_rate_maintenance_history": "local_recur",
    "maintenance_history_local_trigger_rate": "local_recur",
}


METRIC_CANONICAL_TO_DISPLAY = {
    # Stage 1 pressure-shape metrics
    "MAE_s1_peak": "Stage 1 peak difference",
    "MAE_s1_ramp": "Stage 1 ramp difference",
    "MAE_s1_release": "Stage 1 release difference",
    "MAE_s1_full": "Stage 1 full-profile difference",
    "RMSE_s1_full": "Stage 1 full-profile instability",
    "combined_s1_full": "Stage 1 combined full-profile error",
    "osc_ratio_s1_full": "Stage 1 oscillation ratio",

    # Stage 2 pressure-shape metrics
    "MAE_s2_peak": "Stage 2 peak difference",
    "MAE_s2_ramp": "Stage 2 ramp difference",
    "MAE_s2_release": "Stage 2 release difference",
    "MAE_s2_full": "Stage 2 full-profile difference",
    "RMSE_s2_full": "Stage 2 full-profile instability",
    "combined_s2_full": "Stage 2 combined full-profile error",
    "osc_ratio_s2_full": "Stage 2 oscillation ratio",

    # Stage 3 holding metrics
    "MAE_s3_hold": "Stage 3 holding-level difference",
    "RMSE_s3_hold": "Stage 3 holding instability",
    "MAE_s3_ramp": "Stage 3 ramp difference",
    "MAE_s3_full": "Stage 3 full-profile difference",
    "RMSE_s3_full": "Stage 3 full-profile instability",
    "combined_s3_hold": "Stage 3 combined holding error",
    "combined_s3_full": "Stage 3 combined full-profile error",
    "osc_ratio_s3_hold": "Stage 3 holding oscillation ratio",

    # Overall / exhaust metrics
    "cycle_score": "Overall cycle score",
    "exhaust_residual_pressure": "Exhaust residual pressure",
    "exhaust_release_time": "Exhaust release time",

    # Chronic recurrence metrics
    "boiler_recur": "Boiler rule trigger rate",
    "bpv_recur": "BPV rule trigger rate",
    "comp_recur": "Competition trigger rate",
    "net_recur": "Network trigger rate",
    "local_recur": "Local trigger rate",
}


def canonical_metric_name(value: Any, *, rule_id: Any = None, stage: Any = None, attribution: Any = None) -> str:
    """Return the machine-readable metric used by rule evaluation.

    For most pressure-shape metrics the canonical value is unchanged. For CY
    chronic rules, the Excel uses human-readable target metrics such as
    "Local trigger rate, maintenance history" while the executable formula uses
    a recurrence metric such as local_recur. This function makes that conversion
    explicit.
    """
    rid = clean_text(rule_id).upper()
    if rid in CY_RULE_METRIC_MAP:
        return CY_RULE_METRIC_MAP[rid]["metric"]

    raw = clean_text(value)
    if not raw:
        return ""

    lookup = metric_lookup_key(raw)
    if lookup in METRIC_ALIAS_TO_CANONICAL:
        return METRIC_ALIAS_TO_CANONICAL[lookup]

    # Allow the user to type the friendly display label shown in the UI.
    for canonical_name, display_name in METRIC_CANONICAL_TO_DISPLAY.items():
        if metric_lookup_key(display_name) == lookup:
            return canonical_name

    # Handle comma-separated helper context, e.g. "Local trigger rate,
    # maintenance history". The numeric metric is the first measurable metric;
    # maintenance history remains supporting context.
    parts = [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]
    for part in parts:
        part_lookup = metric_lookup_key(part)
        if part_lookup == "maintenance_history":
            continue
        if part_lookup in METRIC_ALIAS_TO_CANONICAL:
            return METRIC_ALIAS_TO_CANONICAL[part_lookup]
        for canonical_name, display_name in METRIC_CANONICAL_TO_DISPLAY.items():
            if metric_lookup_key(display_name) == part_lookup:
                return canonical_name
        for known_metric in METRIC_DESCRIPTIONS.keys():
            if metric_lookup_key(known_metric) == part_lookup:
                return known_metric

    return raw


def display_metric_name(value: Any, *, rule_id: Any = None) -> str:
    rid = clean_text(rule_id).upper()
    if rid in CY_RULE_METRIC_MAP:
        return CY_RULE_METRIC_MAP[rid]["display"]

    canonical = canonical_metric_name(value, rule_id=rule_id)
    return METRIC_CANONICAL_TO_DISPLAY.get(canonical, clean_text(value) or canonical)


def split_metric_parts(value: Any) -> List[str]:
    raw = clean_text(value)
    if not raw:
        return []
    return [part.strip() for part in re.split(r"[,;]+", raw) if part.strip()]


def _is_default_custom_metric_description(value: Any) -> bool:
    """
    Older saved chunks may already contain the generic custom-metric message.
    That message should not override the built-in description for known metrics.
    """
    text = clean_text(value).lower()
    if not text:
        return False
    return (
        text == CUSTOM_METRIC_DEFAULT_DESCRIPTION.lower()
        or text.startswith("custom metric. please make sure this exact metric name exists")
        or text.startswith("custom or unlisted metric")
    )


def _known_metric_description(metric_name: Any) -> str:
    metric = normalise_metric_key(metric_name)
    if not metric:
        return ""

    canonical = canonical_metric_name(metric)
    candidates = [metric, canonical, display_metric_name(metric)]

    # Exact/case-insensitive/space-underscore-insensitive lookup.
    for candidate in candidates:
        if not candidate:
            continue
        if candidate in METRIC_DESCRIPTIONS:
            return METRIC_DESCRIPTIONS[candidate]
        candidate_lower = candidate.lower()
        candidate_key = metric_lookup_key(candidate)
        for known_metric, description in METRIC_DESCRIPTIONS.items():
            if known_metric.lower() == candidate_lower:
                return description
            if metric_lookup_key(known_metric) == candidate_key:
                return description

    # Composite labels such as "Local trigger rate, maintenance history" should
    # not be treated as unknown. Return a combined explanation.
    parts = split_metric_parts(metric)
    if len(parts) > 1:
        descriptions: List[str] = []
        for part in parts:
            part_desc = _known_metric_description(part)
            if part_desc:
                label = display_metric_name(part)
                descriptions.append(f"{label}: {part_desc}")
        if descriptions:
            return " ".join(descriptions)

    return ""


def is_known_metric(metric_name: Any) -> bool:
    return bool(_known_metric_description(metric_name))


def is_valid_metric_name(metric_name: Any) -> bool:
    metric = normalise_metric_key(metric_name)
    # Flexible metric input: existing/new metrics may use underscores or spaces
    # such as MAE_s2_peak, Boiler rule trigger rate, or maintenance history.
    # Still block HTML/script-like text, punctuation-heavy sentences, and very
    # long values.
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_ \-]{1,80}", metric))

def metric_description_for_metric(metric_name: Any, custom_description: Any = None) -> str:
    known_description = _known_metric_description(metric_name)
    custom = clean_text(custom_description)

    # If the user/admin has written a real custom explanation, keep it.
    # But if the saved value is only the old generic fallback message, ignore it
    # and show the built-in description for known metrics.
    if custom and not _is_default_custom_metric_description(custom):
        return custom

    if known_description:
        return known_description

    # A description for a new/unlisted metric is optional. Do not save the
    # generic metric-safety reminder as though it describes what the metric
    # measures; keep an omitted custom description empty instead.
    return ""


def metric_description_for_rule_like(rule_or_chunk: Dict[str, Any]) -> str:
    metric = metric_from_rule_like(rule_or_chunk)
    return metric_description_for_metric(
        metric,
        rule_or_chunk.get("metric_description") or rule_or_chunk.get("metric_explanation"),
    )


def metric_catalog_entries() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()

    for stage in sorted(METRIC_OPTIONS.keys()):
        for metric in METRIC_OPTIONS.get(stage, []):
            canonical = canonical_metric_name(metric)
            display = display_metric_name(metric)
            key = (stage, canonical or metric)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "stage": stage,
                    "metric_name": display,
                    "metric_internal_name": canonical or metric,
                    "metric_display_name": display,
                    "description": metric_description_for_metric(metric),
                    "known": True,
                }
            )

    return entries




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
    """Load original rule chunks plus original reference chunks from Supabase."""

    output: List[Dict[str, Any]] = []
    for row in list_rule_rows(source="original", enabled_only=True):
        chunk = row.get("chunk_payload")
        if not isinstance(chunk, dict):
            continue
        item = dict(chunk)
        item.setdefault("source", "original")
        item.setdefault("editable", True)
        output.append(normalise_loaded_chunk(item))

    for row in list_reference_chunk_rows(enabled_only=True):
        chunk = row.get("chunk_payload")
        if isinstance(chunk, dict):
            output.append(dict(chunk))
    return output


def save_base_chunks(chunks: List[Dict[str, Any]]) -> None:
    """Compatibility helper: persist supplied original chunks to Supabase."""

    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            continue
        if clean_text(chunk.get("rule_id")):
            normalised = normalise_loaded_chunk(dict(chunk))
            rule = chunk_to_editable_rule(normalised)
            save_rule_record(
                rule_payload=rule,
                chunk_payload=normalised,
                index_status="pending",
            )
        elif clean_text(chunk.get("chunk_id")):
            save_reference_chunk(dict(chunk), index_status="pending")


def load_custom_rules() -> List[Dict[str, Any]]:
    """Load active custom rules from Supabase."""

    output: List[Dict[str, Any]] = []
    for row in list_rule_rows(source="custom", enabled_only=True):
        payload = row.get("rule_payload")
        if not isinstance(payload, dict):
            continue
        item = dict(payload)
        item.setdefault("source", "custom")
        item.setdefault("editable", True)
        output.append(item)
    return output


def save_custom_rules(rules: List[Dict[str, Any]]) -> None:
    """Compatibility helper: persist supplied custom rules to Supabase."""

    for rule in rules or []:
        if not isinstance(rule, dict) or not clean_text(rule.get("rule_id")):
            continue
        item = dict(rule)
        item["source"] = "custom"
        item["editable"] = True
        save_rule_record(
            rule_payload=item,
            chunk_payload=rule_to_chunk(item),
            index_status="pending",
        )


def load_deleted_original_rules() -> List[Dict[str, Any]]:
    data = read_json_file(get_deleted_original_rules_path(), [])
    return data if isinstance(data, list) else []


def save_deleted_original_rules(items: List[Dict[str, Any]]) -> None:
    write_json_file(get_deleted_original_rules_path(), items)


def normalise_loaded_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Apply safe display/evaluation fixes to chunks loaded from JSON.

    This is important for CY-001~CY-005 because the Excel stores human-readable
    thresholds like "recur 20–40%" in the Rules Master sheet, while the numeric
    threshold library defines 0.20 and 0.40. The UI and evaluator need the
    numeric values.
    """
    item = dict(chunk)
    item.setdefault("source", "original")
    item.setdefault("editable", True)

    rule_id = clean_text(item.get("rule_id")).upper()

    if rule_id in CY_RULE_METRIC_MAP:
        cy = CY_RULE_METRIC_MAP[rule_id]
        metric = cy["metric"]
        display = cy["display"]

        item["stage"] = "CY"
        item["metric_name"] = metric
        item["metric_display_name"] = display
        item["threshold_metric_name"] = metric
        item["related_scoring_metrics"] = [metric]
        item["metric_description"] = cy["description"]
        item["metric_is_known"] = True
        item["warn_low"] = CY_RECUR_WARN_LOW
        item["warn_high_critical_starts_here"] = CY_RECUR_CRITICAL_VALUE
        item["critical_value"] = CY_RECUR_CRITICAL_VALUE
        item["warn_threshold"] = "0.20 < recurrence rate ≤ 0.40"
        item["critical_threshold"] = "recurrence rate > 0.40"
        item["threshold_unit"] = "fraction"
        item["target_metric"] = cy.get("target_metric") or display
        item["supporting_context"] = cy.get("supporting_context") or ""
        return item

    metric = metric_from_rule_like(item)
    if metric:
        item["metric_name"] = canonical_metric_name(metric, rule_id=rule_id)
        item.setdefault("metric_display_name", display_metric_name(metric, rule_id=rule_id))
        item["threshold_metric_name"] = item.get("threshold_metric_name") or item["metric_name"]
        item["related_scoring_metrics"] = [item["metric_name"]]
        item["metric_description"] = metric_description_for_rule_like(item)
        item["metric_is_known"] = is_known_metric(metric)

    return item




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
    row = get_rule_row(target) if target else None
    if not row or clean_text(row.get("source")).lower() != "original" or not row.get("enabled", True):
        return None
    chunk = row.get("chunk_payload")
    return normalise_loaded_chunk(dict(chunk)) if isinstance(chunk, dict) else None


def get_custom_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    target = clean_text(rule_id)
    row = get_rule_row(target) if target else None
    if not row or clean_text(row.get("source")).lower() != "custom" or not row.get("enabled", True):
        return None
    payload = row.get("rule_payload")
    return dict(payload) if isinstance(payload, dict) else None


def get_rule_source(rule_id: str) -> Optional[str]:
    target = clean_text(rule_id)
    if not target:
        return None
    row = get_rule_row(target)
    if not row or not row.get("enabled", True):
        return None
    source = clean_text(row.get("source")).lower()
    return source if source in VALID_SOURCES else None


def chunk_to_editable_rule(chunk: Dict[str, Any]) -> Dict[str, Any]:
    metric = metric_from_rule_like(chunk)
    display_metric = display_metric_name(chunk.get("metric_display_name") or chunk.get("target_metric") or metric, rule_id=chunk.get("rule_id"))
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
        "metric_display_name": display_metric,
        "metric_internal_name": metric,
        "metric_description": metric_description_for_rule_like(chunk),
        "metric_is_known": is_known_metric(metric),
        "supporting_context": clean_text(chunk.get("supporting_context")),
        "threshold_metric_name": metric,
        "related_scoring_metrics": [metric] if metric else [],
        "warn_low": safe_float(warn_low, 0.0),
        "warn_high_critical_starts_here": safe_float(critical_value, 0.0),
        "critical_value": safe_float(critical_value, 0.0),
        "threshold_unit": clean_text(chunk.get("threshold_unit") or "normalized 0-1"),
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
    metric = canonical_metric_name(
        rule.get("metric_name") or rule.get("threshold_metric_name"),
        rule_id=rule.get("rule_id"),
        stage=rule.get("stage"),
        attribution=rule.get("attribution"),
    )
    metric_display = display_metric_name(rule.get("metric_display_name") or rule.get("target_metric") or metric, rule_id=rule.get("rule_id"))
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
        "Metric explanation:",
        metric_description_for_rule_like(rule),
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
    metric = canonical_metric_name(
        rule.get("metric_name") or rule.get("threshold_metric_name"),
        rule_id=rule.get("rule_id"),
        stage=rule.get("stage"),
        attribution=rule.get("attribution"),
    )
    metric_display = display_metric_name(rule.get("metric_display_name") or rule.get("target_metric") or metric, rule_id=rule.get("rule_id"))
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
            "metric_name": metric,
            "metric_display_name": metric_display,
            "metric_internal_name": metric,
            "metric_description": metric_description_for_rule_like(rule),
            "metric_is_known": is_known_metric(metric),
            "warn_threshold": f"{rule.get('warn_low')} < {metric} ≤ {critical_value}",
            "critical_threshold": f"{metric} > {critical_value}",
            "threshold_metric_name": metric,
            "threshold_unit": rule.get("threshold_unit") or "normalized 0-1",
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
    display_metric = display_metric_name(
        chunk.get("metric_display_name") or chunk.get("target_metric") or metric,
        rule_id=chunk.get("rule_id"),
    )
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
        # metric_name is kept user-facing in the API response.
        "metric_name": display_metric,
        # metric_internal_name is the machine-readable value used by RCA evaluation.
        "metric_internal_name": metric,
        "threshold_metric_name": metric,
        "metric_display_name": display_metric,
        "metric_description": metric_description_for_rule_like(chunk),
        "metric_is_known": is_known_metric(metric) or is_known_metric(display_metric),
        "supporting_context": clean_text(chunk.get("supporting_context")),
        "warn_low": chunk.get("warn_low"),
        "critical_value": critical_value,
        "warn_threshold": chunk.get("warn_threshold"),
        "critical_threshold": chunk.get("critical_threshold"),
        "threshold_unit": chunk.get("threshold_unit"),
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
    raw_metric_name = clean_text(
        data.get("metric_name")
        or data.get("threshold_metric_name")
        or (old_rule or {}).get("metric_name")
        or (old_rule or {}).get("threshold_metric_name")
        or (old_rule or {}).get("target_metric")
    )
    metric_name = canonical_metric_name(
        raw_metric_name,
        rule_id=rule_id,
        stage=stage,
        attribution=attribution,
    )
    metric_display_name = display_metric_name(raw_metric_name or metric_name, rule_id=rule_id)
    if "metric_description" in data:
        supplied_metric_description = data.get("metric_description")
    elif "metric_explanation" in data:
        supplied_metric_description = data.get("metric_explanation")
    else:
        supplied_metric_description = (old_rule or {}).get(
            "metric_description"
        ) or (old_rule or {}).get("metric_explanation")

    metric_description = metric_description_for_metric(
        raw_metric_name or metric_name,
        supplied_metric_description,
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
    target_metric = clean_text(data.get("target_metric") or (old_rule or {}).get("target_metric") or metric_display_name or metric_name)
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

    if not metric_name:
        raise ValueError("Related scoring metric is required.")
    if not is_valid_metric_name(metric_name):
        raise ValueError(
            "Metric name must start with a letter and only contain letters, numbers, underscores, spaces, or hyphens. "
            "Example: MAE_s2_peak, MAE_s1_full, Boiler rule trigger rate, or custom_pressure_drop_score."
        )

    if warn_low < 0 or critical_value < 0:
        raise ValueError("Warning and critical thresholds must be 0 or above.")
    if critical_value <= warn_low:
        raise ValueError("Critical threshold must be greater than warning threshold.")
    if not recommendation_en:
        raise ValueError("Recommendation EN is required.")
    if not recommendation_bm:
        raise ValueError(
            "Recommendation BM is required. Enter the Malay recommendation action before saving the rule."
        )

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
        "metric_display_name": metric_display_name,
        "metric_internal_name": metric_name,
        "metric_description": metric_description,
        "metric_is_known": is_known_metric(metric_name) or is_known_metric(metric_display_name),
        "threshold_metric_name": metric_name,
        "related_scoring_metrics": [metric_name],
        "warn_low": warn_low,
        "warn_high_critical_starts_here": critical_value,
        "critical_value": critical_value,
        "threshold_unit": clean_text(data.get("threshold_unit") or (old_rule or {}).get("threshold_unit") or "normalized 0-1"),
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
            "Another analysis rule already uses the same stage feature and same root cause: "
            f"{first.get('stage')} / {first.get('metric_name')} / pattern {first.get('pattern')} / root cause {first.get('attribution')}."
        )
    return (
        f"{len(conflicts)} analysis rules already use the same stage feature and same root cause. "
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
        "metric_descriptions": METRIC_DESCRIPTIONS,
        "metric_catalog": metric_catalog_entries(),
        "custom_metric_default_description": CUSTOM_METRIC_DEFAULT_DESCRIPTION,
        "stage_options": sorted(VALID_STAGES),
        "priority_options": sorted(VALID_PRIORITIES),
        "attribution_options": sorted(VALID_ATTRIBUTIONS),
        "urgency_options": sorted(VALID_URGENCIES),
        "original_rules_editable": True,
        "custom_rules_editable": True,
    }


def _persist_rule_then_index(rule: Dict[str, Any], chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Save the authoritative rule first, then update the rebuildable Qdrant index."""

    save_rule_record(
        rule_payload=rule,
        chunk_payload=chunk,
        index_status="pending",
    )
    reindex_result = upsert_rule_chunks_to_qdrant([chunk])
    target_status = "indexed" if reindex_result.get("status") == "indexed" else "failed"
    try:
        set_index_status(
            [rule.get("rule_id")],
            status=target_status,
            error=reindex_result.get("error"),
        )
        reindex_result["index_status"] = target_status
    except Exception as exc:
        # The rule itself is already safely stored in Supabase. A later Retry
        # Search Index can reconcile the status if this metadata update fails.
        reindex_result["index_status"] = "pending"
        reindex_result["index_status_update_error"] = str(exc)
    return reindex_result


def create_custom_rule(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # Creating a new rule always creates a custom rule. Original rules are
    # imported into Supabase by the one-time migration utility.
    candidate = normalise_rule_payload(payload, source="custom")
    candidate = apply_conflict_policy(
        candidate,
        existing_rule_id=None,
        allow_conflict=bool((payload or {}).get("allow_conflict")),
    )

    chunk = rule_to_chunk(candidate)
    reindex_result = _persist_rule_then_index(candidate, chunk)
    try:
        record_audit_event(
            action="analysis_rule_created",
            entity_type="analysis_rule",
            entity_id=candidate.get("rule_id") or "unknown",
            after=candidate,
            details={
                "storage": "supabase",
                "search_index_status": reindex_result.get("status"),
                "index_status": reindex_result.get("index_status"),
            },
        )
        reindex_result["audit_status"] = "recorded"
    except Exception as exc:
        reindex_result["audit_status"] = "audit_failed"
        reindex_result["audit_error"] = str(exc)
    return candidate, reindex_result


def update_custom_rule(rule_id: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # Function name kept for backward compatibility with main.py. It now updates
    # either original or custom rules.
    return update_rule(rule_id, payload)


def update_rule(rule_id: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    target = clean_text(rule_id)
    source = get_rule_source(target)

    if source == "custom":
        old_rule = get_custom_rule(target)
        if old_rule is None:
            raise ValueError("Custom rule not found.")

        merged = {**old_rule, **dict(payload or {})}
        merged["rule_id"] = target
        candidate = normalise_rule_payload(
            merged,
            source="custom",
            existing_rule_id=target,
            old_rule=old_rule,
        )
        candidate = apply_conflict_policy(
            candidate,
            existing_rule_id=target,
            allow_conflict=bool((payload or {}).get("allow_conflict")),
        )
        updated_chunk = rule_to_chunk(candidate)
        reindex_result = _persist_rule_then_index(candidate, updated_chunk)
        try:
            record_audit_event(
                action="analysis_rule_updated",
                entity_type="analysis_rule",
                entity_id=target,
                before=old_rule,
                after=candidate,
                details={
                    "source": "custom",
                    "storage": "supabase",
                    "search_index_status": reindex_result.get("status"),
                    "index_status": reindex_result.get("index_status"),
                },
            )
            reindex_result["audit_status"] = "recorded"
        except Exception as exc:
            reindex_result["audit_status"] = "audit_failed"
            reindex_result["audit_error"] = str(exc)
        return candidate, reindex_result

    if source == "original":
        old_chunk = find_original_chunk(target)
        if old_chunk is None:
            raise ValueError("Original rule not found.")

        old_rule = chunk_to_editable_rule(old_chunk)
        merged = {**old_rule, **dict(payload or {})}
        merged["rule_id"] = target
        candidate = normalise_rule_payload(
            merged,
            source="original",
            existing_rule_id=target,
            old_rule=old_rule,
        )
        candidate = apply_conflict_policy(
            candidate,
            existing_rule_id=target,
            allow_conflict=bool((payload or {}).get("allow_conflict")),
        )
        updated_chunk = rule_to_chunk(candidate, preserve_chunk=old_chunk)
        reindex_result = _persist_rule_then_index(candidate, updated_chunk)
        try:
            record_audit_event(
                action="analysis_rule_updated",
                entity_type="analysis_rule",
                entity_id=target,
                before=old_rule,
                after=candidate,
                details={
                    "source": "original",
                    "storage": "supabase",
                    "search_index_status": reindex_result.get("status"),
                    "index_status": reindex_result.get("index_status"),
                },
            )
            reindex_result["audit_status"] = "recorded"
        except Exception as exc:
            reindex_result["audit_status"] = "audit_failed"
            reindex_result["audit_error"] = str(exc)
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
        removed_rule = get_custom_rule(target)
        if removed_rule is None:
            raise ValueError("Custom rule not found.")
        chunk_id = rule_to_chunk(removed_rule).get("chunk_id")
        deleted = delete_rule_record(target)
        if deleted is None:
            raise ValueError("Custom rule not found in Supabase.")
        qdrant = delete_chunks_from_qdrant([chunk_id])
        try:
            record_audit_event(
                action="analysis_rule_deleted",
                entity_type="analysis_rule",
                entity_id=target,
                before=removed_rule,
                details={
                    "source": "custom",
                    "storage": "supabase",
                    "search_index_status": qdrant.get("status"),
                },
            )
            audit_status = "recorded"
        except Exception as exc:
            audit_status = "audit_failed"
            qdrant["audit_error"] = str(exc)
        return {
            "status": "deleted",
            "source": "custom",
            "rule_id": target,
            "deleted_chunks": [chunk_id],
            "qdrant_delete": qdrant,
            "audit_status": audit_status,
        }

    if source == "original":
        removed_chunk = find_original_chunk(target)
        if removed_chunk is None:
            raise ValueError("Original rule not found.")
        removed_rule = chunk_to_editable_rule(removed_chunk)
        deleted = delete_rule_record(target)
        if deleted is None:
            raise ValueError("Original rule not found in Supabase.")

        qdrant = delete_chunks_from_qdrant([removed_chunk.get("chunk_id")])
        try:
            record_audit_event(
                action="analysis_rule_deleted",
                entity_type="analysis_rule",
                entity_id=target,
                before=removed_rule,
                details={
                    "source": "original",
                    "storage": "supabase",
                    "search_index_status": qdrant.get("status"),
                },
            )
            audit_status = "recorded"
        except Exception as exc:
            audit_status = "audit_failed"
            qdrant["audit_error"] = str(exc)
        return {
            "status": "deleted",
            "source": "original",
            "rule_id": target,
            "deleted_chunks": [removed_chunk.get("chunk_id")],
            "qdrant_delete": qdrant,
            "audit_status": audit_status,
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


def _persist_bulk_index_status(chunks: List[Dict[str, Any]], result: Dict[str, Any]) -> Dict[str, Any]:
    rule_ids = [clean_text(chunk.get("rule_id")) for chunk in chunks if clean_text(chunk.get("rule_id"))]
    reference_chunk_ids = [
        clean_text(chunk.get("chunk_id"))
        for chunk in chunks
        if not clean_text(chunk.get("rule_id")) and clean_text(chunk.get("chunk_id"))
    ]
    status = "indexed" if result.get("status") == "indexed" else "failed"
    try:
        updated = 0
        if rule_ids:
            updated += set_index_status(rule_ids, status=status, error=result.get("error"))
        if reference_chunk_ids:
            updated += set_reference_index_status(
                reference_chunk_ids,
                status=status,
                error=result.get("error"),
            )
        result["index_status_rows_updated"] = updated
        result["index_status"] = status
    except Exception as exc:
        result["index_status_update_error"] = str(exc)
    return result


def reindex_all_custom_rules_to_qdrant() -> Dict[str, Any]:
    chunks = [rule_to_chunk(rule) for rule in load_custom_rules()]
    result = upsert_rule_chunks_to_qdrant(chunks)
    return _persist_bulk_index_status(chunks, result)


def reindex_all_rules_to_qdrant() -> Dict[str, Any]:
    chunks = get_all_chunks_for_indexing()
    if not chunks:
        return {"indexed_chunks": 0, "status": "no_rules"}

    try:
        from app.rag.embedding_service import embed_texts
        from app.rag.vector_db import ensure_collection, upsert_chunks

        texts = [chunk.get("search_text") or chunk.get("text") or "" for chunk in chunks]
        vectors = embed_texts(texts, batch_size=8)

        # Qdrant remains a rebuildable index. A full retry recreates the
        # collection entirely from the authoritative Supabase rule records.
        ensure_collection(recreate=True)
        indexed = upsert_chunks(chunks, vectors)
        result = {
            "status": "indexed",
            "indexed_chunks": indexed,
            "recreated_collection": True,
            "chunk_ids": [chunk.get("chunk_id") for chunk in chunks],
        }
    except Exception as exc:
        result = {
            "status": "index_failed",
            "indexed_chunks": 0,
            "recreated_collection": False,
            "error": str(exc),
            "chunk_ids": [chunk.get("chunk_id") for chunk in chunks],
        }
    return _persist_bulk_index_status(chunks, result)


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
    row = get_rule_row(target) if target else None
    source = clean_text((row or {}).get("source")).lower() or None

    rule_payload = (row or {}).get("rule_payload")
    chunk = (row or {}).get("chunk_payload")
    rule_saved = isinstance(rule_payload, dict)
    chunk_saved = isinstance(chunk, dict)

    result: Dict[str, Any] = {
        "rule_id": target,
        "source": source,
        "storage": "supabase",
        "rule_saved_in_supabase": rule_saved,
        "chunk_saved_in_supabase": chunk_saved,
        "stored_index_status": (row or {}).get("index_status"),
        "stored_indexed_at": (row or {}).get("indexed_at"),
        "stored_index_error": (row or {}).get("index_error"),
        "direct_retrieval_ready": False,
        "qdrant_indexed": False,
        "qdrant_status": "not_checked",
        "qdrant_error": None,
        "chunk_id": chunk.get("chunk_id") if isinstance(chunk, dict) else None,
    }

    if not row or not row.get("enabled", True):
        result.update({"status": "missing_rule", "ready_for_immediate_rca": False})
        return result
    if source not in VALID_SOURCES:
        result.update({"status": "invalid_rule_source", "ready_for_immediate_rca": False})
        return result
    if not rule_saved or not chunk_saved:
        result.update({"status": "incomplete_supabase_record", "ready_for_immediate_rca": False})
        return result

    chunk = normalise_loaded_chunk(dict(chunk)) if source == "original" else dict(chunk)
    chunk_id = chunk.get("chunk_id")
    result["chunk_id"] = chunk_id

    try:
        matching = get_matching_rule_chunks(
            stage=chunk.get("stage"),
            metric=metric_from_rule_like(chunk),
            pattern=chunk.get("pattern"),
        )
        result["direct_retrieval_ready"] = any(
            clean_text(item.get("rule_id")) == target for item in matching
        )
        result["matching_rule_chunk_count"] = len(matching)
    except Exception as exc:
        result["direct_retrieval_error"] = str(exc)

    try:
        from app.rag.rag_config import get_rag_settings
        from app.rag.vector_db import deterministic_point_id, get_qdrant_client

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
        result["rule_saved_in_supabase"]
        and result["chunk_saved_in_supabase"]
        and result["direct_retrieval_ready"]
    )

    if result["ready_for_immediate_rca"] and result["qdrant_indexed"]:
        result["status"] = "fully_ready"
    elif result["ready_for_immediate_rca"]:
        result["status"] = "ready_supabase_direct_qdrant_pending"
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

    requested = canonical_metric_name(metric)
    chunk_metric = metric_from_rule_like(chunk)
    if requested and chunk_metric and requested == chunk_metric:
        return True

    requested_lookup = metric_lookup_key(metric)
    for related in chunk.get("related_scoring_metrics") or []:
        if metric_lookup_key(related) == requested_lookup:
            return True
        if canonical_metric_name(related) == requested:
            return True

    text = chunk.get("search_text") or chunk.get("text") or ""
    return clean_text(metric) in str(text)


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
