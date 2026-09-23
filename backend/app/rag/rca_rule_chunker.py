from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import unicodedata


BACKEND_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PARSED_INPUT = BACKEND_ROOT / "knowledge_base" / "parsed" / "parsed_rca_v4_excel.json"
DEFAULT_CHUNK_OUTPUT = BACKEND_ROOT / "knowledge_base" / "chunks" / "rca_v4_chunks.json"

MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def now_malaysia_iso() -> str:
    return datetime.now(MALAYSIA_TZ).replace(microsecond=0).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def normalise_lookup_key(value: Any) -> str:
    """Return a stable key for joins between different Excel sheets.

    Excel keys can look identical in the workbook while containing different
    dash characters, spacing, case, or non-breaking spaces.  Comparing the raw
    strings made valid Recommendation Matrix rows fail to join silently.
    """
    text = unicodedata.normalize("NFKC", clean_text(value)).upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def slugify(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "_", value)
    return value.strip("_") or "unknown"


def stable_hash(text: str, length: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def load_parsed_excel(path: str | Path = DEFAULT_PARSED_INPUT) -> Dict[str, Any]:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Parsed RCA Excel JSON not found: {path}. "
            "Run: python -m app.rag.excel_parser first."
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_chunks(chunks: List[Dict[str, Any]], output_path: str | Path = DEFAULT_CHUNK_OUTPUT) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    return output


def build_index(records: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    index = {}

    for record in records:
        value = normalise_lookup_key(record.get(key))
        if value:
            index[value] = record

    return index


def record_get(record: Dict[str, Any], key: str, default: Any = "") -> Any:
    return record.get(key, default)


def build_rule_text(rule: Dict[str, Any], threshold: Optional[Dict[str, Any]], rec: Optional[Dict[str, Any]]) -> str:
    lines = []

    lines.append(f"Rule ID: {record_get(rule, 'rule_id')}")
    lines.append(f"Priority: {record_get(rule, 'pri')}")
    lines.append(f"Stage: {record_get(rule, 'stage')}")
    lines.append(f"Attribution group: {record_get(rule, 'attribution')}")
    lines.append(f"Pattern: {record_get(rule, 'pattern')}")
    lines.append(f"ML label: {record_get(rule, 'ml_label')}")
    lines.append("")
    lines.append("Input signal(s):")
    lines.append(clean_text(record_get(rule, 'input_signal_s_sub_window_mae_rmse')))
    lines.append("")
    lines.append("Executable formula:")
    lines.append(clean_text(record_get(rule, 'executable_formula_normalised_scale_0_1')))
    lines.append("")
    lines.append("Rule thresholds from Rules Master:")
    lines.append(f"Warning threshold: {clean_text(record_get(rule, 'warn_threshold'))}")
    lines.append(f"Critical threshold: {clean_text(record_get(rule, 'critical_threshold'))}")
    lines.append(f"Score deduction when warning: {record_get(rule, 'score_warn')}")
    lines.append(f"Score deduction when critical: {record_get(rule, 'score_crit')}")
    lines.append(f"Attribution cap: {record_get(rule, 'cap')}")
    lines.append("")

    if threshold:
        lines.append("Threshold Library reference:")
        lines.append(f"Metric name: {clean_text(record_get(threshold, 'metric_name'))}")
        lines.append(f"Unit: {clean_text(record_get(threshold, 'unit'))}")
        lines.append(f"Warn low: {record_get(threshold, 'warn_low')}")
        lines.append(f"Warn high / critical starts here: {record_get(threshold, 'warn_high_critical_starts_here')}")
        lines.append(f"Critical value: {record_get(threshold, 'critical_value')}")
        lines.append(f"Derivation / notes: {clean_text(record_get(threshold, 'derivation_notes'))}")
        lines.append("")

    if rec:
        lines.append("Recommendation Matrix reference:")
        lines.append(f"Recommendation key: {clean_text(record_get(rec, 'rec_key'))}")
        lines.append(f"Root cause: {clean_text(record_get(rec, 'root_cause'))}")
        lines.append(f"Symptom: {clean_text(record_get(rec, 'symptom_sub_window_elevated'))}")
        lines.append(f"Urgency: {clean_text(record_get(rec, 'urgency'))}")
        lines.append(f"Target metric: {clean_text(record_get(rec, 'target_metric'))}")
        lines.append("")
        lines.append("Recommendation EN:")
        lines.append(clean_text(record_get(rec, 'recommendation_en')))
        lines.append("")
        lines.append("Recommendation BM:")
        lines.append(clean_text(record_get(rec, 'recommendation_bm')))

    return "\n".join(lines).strip()


def related_scoring_metrics_for_rule(rule: Dict[str, Any], threshold: Optional[Dict[str, Any]]) -> List[str]:
    metrics = []

    for source in [
        record_get(rule, "input_signal_s_sub_window_mae_rmse"),
        record_get(rule, "executable_formula_normalised_scale_0_1"),
        record_get(threshold or {}, "metric_name"),
    ]:
        text = clean_text(source)
        found = re.findall(r"\b(?:MAE|RMSE|combined|osc_ratio|mean_error|N_conc|local_dev|low_count)[A-Za-z0-9_]*\b", text)
        metrics.extend(found)

    # Deduplicate while preserving order.
    seen = set()
    unique = []
    for item in metrics:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    return unique


def build_rule_chunk(rule: Dict[str, Any], threshold: Optional[Dict[str, Any]], rec: Optional[Dict[str, Any]], source_file_name: str) -> Dict[str, Any]:
    rule_id = clean_text(record_get(rule, "rule_id"))
    rec_key = clean_text(record_get(rule, "rec_key_to_sheet_7"))
    stage = clean_text(record_get(rule, "stage"))
    attribution = clean_text(record_get(rule, "attribution"))
    pattern = clean_text(record_get(rule, "pattern"))
    recommendation_en = clean_text(record_get(rec or {}, "recommendation_en"))
    recommendation_bm = clean_text(record_get(rec or {}, "recommendation_bm"))
    urgency = clean_text(record_get(rec or {}, "urgency"))
    target_metric = clean_text(record_get(rec or {}, "target_metric"))

    if not rec:
        recommendation_join_error = (
            f"No Recommendation Matrix row matched Rec_Key {rec_key!r}."
            if rec_key
            else "Rules Master row has no Rec_Key_to_Sheet_7 value."
        )
    elif not recommendation_en:
        recommendation_join_error = (
            f"Recommendation Matrix row {rec_key!r} matched, but Recommendation_EN is empty."
        )
    else:
        recommendation_join_error = ""

    text = build_rule_text(rule, threshold, rec)

    search_text = "\n\n".join([
        f"RCA V4 Rule {rule_id}",
        f"Stage {stage}",
        f"Attribution {attribution}",
        f"Pattern {pattern}",
        f"Rec key {rec_key}",
        "Related scoring metrics: " + ", ".join(related_scoring_metrics_for_rule(rule, threshold)),
        text,
    ]).strip()

    chunk_id = f"rca_v4_rule_{slugify(rule_id)}_{stable_hash(search_text)}"

    return {
        "chunk_id": chunk_id,
        "chunk_type": "rca_rule_joined",
        "retrieval_scope": "primary_rca_rule",
        "include_for_anomaly_retrieval": True,
        "source_file_name": source_file_name,

        "rule_id": rule_id,
        "priority": clean_text(record_get(rule, "pri")),
        "stage": stage,
        "attribution": attribution,
        "pattern": pattern,
        "ml_label": clean_text(record_get(rule, "ml_label")),
        "rec_key": rec_key,

        "related_scoring_metrics": related_scoring_metrics_for_rule(rule, threshold),

        "warn_threshold": clean_text(record_get(rule, "warn_threshold")),
        "critical_threshold": clean_text(record_get(rule, "critical_threshold")),
        "threshold_metric_name": clean_text(record_get(threshold or {}, "metric_name")),
        "threshold_unit": clean_text(record_get(threshold or {}, "unit")),
        "warn_low": clean_text(record_get(threshold or {}, "warn_low")),
        "warn_high_critical_starts_here": clean_text(record_get(threshold or {}, "warn_high_critical_starts_here")),
        "critical_value": clean_text(record_get(threshold or {}, "critical_value")),

        "score_warn": record_get(rule, "score_warn"),
        "score_crit": record_get(rule, "score_crit"),
        "cap": record_get(rule, "cap"),

        "recommendation_en": recommendation_en,
        "recommendation_bm": recommendation_bm,
        "urgency": urgency,
        "target_metric": target_metric,
        "recommendation_joined": bool(rec),
        "recommendation_join_error": recommendation_join_error,

        "text": text,
        "search_text": search_text,
        "created_at": now_malaysia_iso(),
    }


def build_stage_definition_chunk(record: Dict[str, Any], source_file_name: str) -> Dict[str, Any]:
    stage_id = clean_text(record_get(record, "stage_id"))

    text = "\n".join([
        f"Stage ID: {stage_id}",
        f"Stage name: {clean_text(record_get(record, 'stage_name'))}",
        f"Start condition: {clean_text(record_get(record, 'start_condition'))}",
        f"End condition: {clean_text(record_get(record, 'end_condition'))}",
        f"Sub-windows for RCA: {clean_text(record_get(record, 'sub_windows_for_rca_mae_decomposition'))}",
        f"Benchmark field: {clean_text(record_get(record, 'benchmark_field_influxdb'))}",
        f"Score weight: {clean_text(record_get(record, 'score_weight'))}",
        f"Engineering purpose: {clean_text(record_get(record, 'engineering_purpose'))}",
    ]).strip()

    return {
        "chunk_id": f"rca_v4_stage_definition_{slugify(stage_id)}_{stable_hash(text)}",
        "chunk_type": "stage_definition",
        "retrieval_scope": "scoring_reference",
        "include_for_anomaly_retrieval": False,
        "source_file_name": source_file_name,
        "stage": stage_id,
        "text": text,
        "search_text": text,
        "created_at": now_malaysia_iso(),
    }


def build_attribution_chunk(record: Dict[str, Any], source_file_name: str) -> Dict[str, Any]:
    priority = clean_text(record_get(record, "priority"))

    text = "\n".join([
        f"Attribution step: {clean_text(record_get(record, 'step'))}",
        f"Priority: {priority}",
        f"Applies to stage: {clean_text(record_get(record, 'applies_to_stage'))}",
        f"Condition IF: {clean_text(record_get(record, 'condition_if_mae_sub_window_based'))}",
        f"Condition AND: {clean_text(record_get(record, 'condition_and_confirmation_check'))}",
        f"Root cause output: {clean_text(record_get(record, 'root_cause_output'))}",
        f"Deduction cap: {clean_text(record_get(record, 'deduction_cap'))}",
    ]).strip()

    return {
        "chunk_id": f"rca_v4_attribution_{slugify(priority)}_{stable_hash(text)}",
        "chunk_type": "attribution_engine",
        "retrieval_scope": "attribution_reference",
        "include_for_anomaly_retrieval": True,
        "source_file_name": source_file_name,
        "priority": priority,
        "text": text,
        "search_text": text,
        "created_at": now_malaysia_iso(),
    }


def build_exhaust_safety_chunk(record: Dict[str, Any], source_file_name: str) -> Dict[str, Any]:
    check_id = clean_text(record_get(record, "check_id"))

    text = "\n".join([
        f"Exhaust safety check ID: {check_id}",
        f"Check name: {clean_text(record_get(record, 'check_name'))}",
        f"Condition: {clean_text(record_get(record, 'condition'))}",
        f"Pass: {clean_text(record_get(record, 'pass'))}",
        f"Fail: {clean_text(record_get(record, 'fail'))}",
        f"Alarm: {clean_text(record_get(record, 'alarm'))}",
        f"Action if fail: {clean_text(record_get(record, 'action_if_fail'))}",
        "Note: Exhaust safety check is pass/fail only and has zero effect on cycle score.",
    ]).strip()

    return {
        "chunk_id": f"rca_v4_exhaust_safety_{slugify(check_id)}_{stable_hash(text)}",
        "chunk_type": "exhaust_safety_check",
        "retrieval_scope": "safety_check",
        "include_for_anomaly_retrieval": False,
        "always_include_for_exhaust_failure": True,
        "source_file_name": source_file_name,
        "check_id": check_id,
        "alarm": clean_text(record_get(record, "alarm")),
        "text": text,
        "search_text": text,
        "created_at": now_malaysia_iso(),
    }


def build_formula_reference_chunk(parsed: Dict[str, Any]) -> Dict[str, Any]:
    source_file_name = parsed.get("source_file_name", "")
    score_model_rows = parsed["sheets"].get("6_SCORE_MODEL", [])
    scoring_blocks = parsed["sheets"].get("1_SCORING_PIPELINE", [])

    text_parts = [
        "RCA V4 Scoring Formula Reference",
        "Cycle score = S1_score*0.20 + S2_score*0.30 + S3_score*0.50.",
        "Full-cycle min-max normalization is used before stage slicing.",
        "MAE and RMSE are combined with 50/50 weight.",
        "Exponential score uses k=2.2.",
        "Exhaust is safety pass/fail only and does not affect cycle score.",
    ]

    for row in score_model_rows:
        if row.get("key") or row.get("value"):
            text_parts.append(f"{clean_text(row.get('key'))}: {clean_text(row.get('value'))}")

    for block in scoring_blocks[:3]:
        text_parts.append(f"{clean_text(block.get('title'))}\n{clean_text(block.get('text'))}")

    text = "\n\n".join(text_parts).strip()

    return {
        "chunk_id": f"rca_v4_formula_reference_{stable_hash(text)}",
        "chunk_type": "formula_reference",
        "retrieval_scope": "scoring_reference",
        "include_for_anomaly_retrieval": False,
        "source_file_name": source_file_name,
        "text": text,
        "search_text": text,
        "created_at": now_malaysia_iso(),
    }


def chunk_rca_v4(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    sheets = parsed["sheets"]
    source_file_name = parsed.get("source_file_name", "")

    rules = sheets.get("3_RULES_MASTER", [])
    thresholds = sheets.get("4_THRESHOLD_LIBRARY", [])
    recs = sheets.get("7_RECOMMENDATION_MATRIX", [])

    threshold_by_rule = build_index(thresholds, "rule_id")
    rec_by_key = build_index(recs, "rec_key")

    chunks = []

    # Important: one primary chunk per rule, joined with its threshold and recommendation.
    # This prevents overlap between RULES_MASTER, THRESHOLD_LIBRARY, and RECOMMENDATION_MATRIX.
    for rule in rules:
        rule_id = clean_text(record_get(rule, "rule_id"))
        rec_key = clean_text(record_get(rule, "rec_key_to_sheet_7"))
        threshold = threshold_by_rule.get(normalise_lookup_key(rule_id))
        rec = rec_by_key.get(normalise_lookup_key(rec_key))

        chunks.append(build_rule_chunk(rule, threshold, rec, source_file_name))

    for stage in sheets.get("2_STAGE_DEFINITION", []):
        chunks.append(build_stage_definition_chunk(stage, source_file_name))

    for attr in sheets.get("5_ATTRIBUTION_ENGINE", []):
        chunks.append(build_attribution_chunk(attr, source_file_name))

    for exhaust in sheets.get("8_EXHAUST_SAFETY_CHECK", []):
        chunks.append(build_exhaust_safety_chunk(exhaust, source_file_name))

    chunks.append(build_formula_reference_chunk(parsed))

    return chunks


def chunk_and_save_rca_v4(
    input_path: str | Path = DEFAULT_PARSED_INPUT,
    output_path: str | Path = DEFAULT_CHUNK_OUTPUT,
) -> Dict[str, Any]:
    parsed = load_parsed_excel(input_path)
    chunks = chunk_rca_v4(parsed)
    saved_path = save_chunks(chunks, output_path)

    type_counts: Dict[str, int] = {}
    for chunk in chunks:
        chunk_type = chunk.get("chunk_type", "unknown")
        type_counts[chunk_type] = type_counts.get(chunk_type, 0) + 1

    rule_chunks = [chunk for chunk in chunks if chunk.get("chunk_type") == "rca_rule_joined"]
    missing_recommendation_rules = [
        {
            "rule_id": chunk.get("rule_id"),
            "rec_key": chunk.get("rec_key"),
            "reason": chunk.get("recommendation_join_error"),
        }
        for chunk in rule_chunks
        if not clean_text(chunk.get("recommendation_en"))
    ]

    return {
        "input_path": str(input_path),
        "output_path": str(saved_path),
        "total_chunks": len(chunks),
        "chunk_type_counts": type_counts,
        "rules_with_recommendations": len(rule_chunks) - len(missing_recommendation_rules),
        "rules_missing_recommendations": missing_recommendation_rules,
        "created_at": now_malaysia_iso(),
    }


if __name__ == "__main__":
    result = chunk_and_save_rca_v4()
    print(json.dumps(result, indent=2))
