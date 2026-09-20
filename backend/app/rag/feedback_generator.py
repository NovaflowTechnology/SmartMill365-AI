import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from app.rag.llm_service import generate_llm_text
except Exception:  # pragma: no cover
    generate_llm_text = None

try:
    from app.rag.plain_language_layer import build_plain_language_feedback
except Exception:  # pragma: no cover
    build_plain_language_feedback = None

logger = logging.getLogger(__name__)

VALID_STAGES = {"S1", "S2", "S3", "CY"}


# =============================================================================
# Environment helpers
# =============================================================================
def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def current_llm_model_name() -> str:
    return os.getenv("LLM_MODEL_NAME", "Qwen/Qwen2.5-0.5B-Instruct").strip()


def should_try_llm() -> bool:
    return env_bool("ENABLE_LLM_GENERATION", False) and generate_llm_text is not None


# =============================================================================
# Basic formatting helpers
# =============================================================================
def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def format_number(value: Any, digits: int = 2) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def format_metric_value(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.4f}"


def normalize_stage(value: Any) -> Optional[str]:
    stage = str(value or "").strip().upper()
    if stage in VALID_STAGES:
        return stage
    return None


def get_selected_stage(metrics: Dict[str, Any]) -> Optional[str]:
    """
    Only user_selected_stage means the user is viewing a stage detail.
    affected_stage can exist even when user opened overall cycle view.
    """
    return normalize_stage(metrics.get("user_selected_stage"))


def normalize_band(score_band: Any, score: Any = None) -> str:
    band = str(score_band or "").strip().lower()
    if band in {"excellent", "good", "fair", "poor", "critical"}:
        return band

    score_number = safe_float(score)
    if score_number is None:
        return "unknown"
    if score_number >= 90:
        return "excellent"
    if score_number >= 75:
        return "good"
    if score_number >= 60:
        return "fair"
    if score_number >= 50:
        return "poor"
    return "critical"


def band_label(score: Any, score_band: Any = None) -> str:
    band = normalize_band(score_band, score)
    return {
        "excellent": "Excellent",
        "good": "Good",
        "fair": "Fair",
        "poor": "Poor",
        "critical": "Critical",
    }.get(band, "Unknown")


def stage_name(stage: Any) -> str:
    value = str(stage or "").upper()
    return {
        "S1": "Stage 1",
        "S2": "Stage 2",
        "S3": "Stage 3",
        "CY": "Overall cycle",
        "EX": "Exhaust stage",
    }.get(value, value or "Unknown stage")


def stage_score_field_label(stage: str) -> str:
    return f"{stage_name(stage)} score"


def get_stage_scores(score_breakdown: Dict[str, Any]) -> Dict[str, Optional[float]]:
    return {
        "S1": safe_float(score_breakdown.get("s1_score")),
        "S2": safe_float(score_breakdown.get("s2_score")),
        "S3": safe_float(score_breakdown.get("s3_score")),
    }


def get_stage_score(score_breakdown: Dict[str, Any], stage: str) -> Optional[float]:
    stage = str(stage or "").lower()
    return safe_float(score_breakdown.get(f"{stage}_score"))


def weakest_stage(score_breakdown: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    stage_scores = get_stage_scores(score_breakdown)
    valid = [(stage, value) for stage, value in stage_scores.items() if value is not None]
    if not valid:
        return None, None
    return min(valid, key=lambda item: item[1])


def other_stage_context(score_breakdown: Dict[str, Any], selected_stage: str) -> str:
    parts: List[str] = []
    for stage, value in get_stage_scores(score_breakdown).items():
        if stage == selected_stage:
            continue
        if value is not None:
            parts.append(f"{stage} {format_number(value, 2)} ({band_label(value)})")
    return ", ".join(parts) if parts else "Other stage scores are unavailable."


# =============================================================================
# Rule and RCA explanation helpers
# =============================================================================
def choose_primary_rule(
    evaluated_rules: List[Dict[str, Any]],
    preferred_stage: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not evaluated_rules:
        return None

    rules = evaluated_rules
    if preferred_stage:
        stage_rules = [
            r for r in evaluated_rules
            if str(r.get("stage") or "").upper() == preferred_stage
        ]
        if stage_rules:
            rules = stage_rules

    confirmed = [r for r in rules if r.get("status") == "confirmed"]
    if confirmed:
        return confirmed[0]

    candidates = [r for r in rules if r.get("status") == "candidate"]
    if candidates:
        return candidates[0]

    triggered = [r for r in rules if r.get("triggered")]
    if triggered:
        return triggered[0]

    return rules[0]


def attribution_display(primary_rule: Optional[Dict[str, Any]]) -> str:
    if not primary_rule:
        return "No analysis rule triggered"

    attribution = primary_rule.get("attribution") or primary_rule.get("ml_label") or "Unknown"
    priority = primary_rule.get("priority")
    return f"{attribution} ({priority})" if priority else str(attribution)


def attribution_simple_name(primary_rule: Optional[Dict[str, Any]]) -> str:
    if not primary_rule:
        return "No specific root cause"
    attribution = str(primary_rule.get("attribution") or "").strip()
    if not attribution:
        return "Unknown root cause"
    return attribution


def attribution_explanation(primary_rule: Optional[Dict[str, Any]]) -> str:
    if not primary_rule:
        return "No specific root-cause rule exceeded the threshold."

    attribution = str(primary_rule.get("attribution") or "").lower()
    if attribution == "competition":
        return "Possible steam demand conflict: another sterilizer may be using steam at the same time."
    if attribution == "boiler":
        return "Possible shared boiler output issue: the boiler may not be supplying enough steam for the demand."
    if attribution == "bpv":
        return "Possible BPV delivery or regulation issue: steam pressure may not be controlled or delivered properly."
    if attribution == "network":
        return "Possible shared steam network issue affecting more than one sterilizer."
    if attribution == "local":
        return "Possible local sterilizer issue, such as valve, trap, or sensor-related behavior."

    recommendation = str(primary_rule.get("recommendation_en") or "").strip()
    if recommendation:
        return recommendation.split(".")[0].strip() + "."
    return "The explanation is based on the triggered analysis rule."


def pattern_explanation(pattern: Any) -> str:
    raw = str(pattern or "").upper().replace(" ", "")
    if not raw or raw == "-":
        return "No specific pressure pattern was identified."

    meanings = {
        "A": "peak pressure problem: the highest pressure point is lower than expected or different from the benchmark",
        "B": "slow ramp problem: pressure rises more slowly than expected",
        "C": "unstable pressure problem: pressure fluctuates more than expected",
        "D": "release problem: pressure drops or releases differently from the benchmark",
        "E": "holding problem: pressure during the holding period is not stable or not close to the benchmark",
        "F": "shared/system pressure movement: other sterilizers also show pressure activity around the same period",
    }

    parts = [part for part in re.split(r"[+,/]", raw) if part]
    explained = [meanings[p] for p in parts if p in meanings]

    if not explained:
        return "The detected pattern indicates the pressure curve differs from the benchmark."

    if len(explained) == 1:
        return f"The detected pattern means {explained[0]}."

    return "The detected patterns mean " + "; and ".join(explained) + "."


def metric_explanation(metric_name: Any, pattern: Any = None) -> str:
    metric = str(metric_name or "").lower()

    if "s1" in metric:
        stage_text = "Stage 1"
    elif "s2" in metric:
        stage_text = "Stage 2"
    elif "s3" in metric:
        stage_text = "Stage 3"
    else:
        stage_text = "the selected stage"

    if "ramp" in metric:
        return (
            f"This metric measures how different the pressure rise is in {stage_text}. "
            "A higher value means pressure may be rising too slowly or not following the normal rising pattern."
        )
    if "peak" in metric:
        return (
            f"This metric measures how different the pressure peak is in {stage_text}. "
            "A higher value means the highest pressure point is too different from the normal benchmark peak."
        )
    if "release" in metric:
        return (
            f"This metric measures how different the pressure release is in {stage_text}. "
            "A higher value means the pressure drop does not follow the expected release pattern."
        )
    if "hold" in metric or "holding" in metric:
        return (
            f"This metric measures pressure level and stability during the holding part of {stage_text}. "
            "A higher value means the sterilization holding pressure may be unstable or far from normal."
        )
    if "osc" in metric or "stability" in metric:
        return "This metric measures pressure instability. A higher value means the pressure fluctuates more than expected."
    if "duration" in metric:
        return "This metric measures timing difference. A higher value means the stage duration is different from the benchmark."
    if metric:
        return "This metric measures how much the selected pressure curve differs from the normal benchmark."
    return "No key metric was selected."


def threshold_explanation(primary_rule: Optional[Dict[str, Any]]) -> str:
    if not primary_rule:
        return "No threshold comparison available."

    metric_name = primary_rule.get("metric_name") or "Metric"
    metric_value = safe_float(primary_rule.get("metric_value"))
    warn = safe_float(primary_rule.get("warn_low"))
    critical = safe_float(primary_rule.get("critical_value"))

    if metric_value is None:
        return "Metric value is unavailable."

    base_metric = metric_explanation(metric_name)

    if critical is not None and metric_value >= critical:
        return (
            f"{base_metric} The value is {format_metric_value(metric_value)}, "
            f"which is higher than the critical limit of {format_metric_value(critical)}. "
            "This means the deviation is serious and should be checked."
        )

    if warn is not None and metric_value >= warn:
        return (
            f"{base_metric} The value is {format_metric_value(metric_value)}, "
            f"which is higher than the warning limit of {format_metric_value(warn)}. "
            "This means the pressure difference needs attention."
        )

    return (
        f"{base_metric} The value is {format_metric_value(metric_value)}, "
        "which is below the analysis trigger limit."
    )


def rule_status_explanation(primary_rule: Optional[Dict[str, Any]]) -> str:
    if not primary_rule:
        return "No analysis rule was selected as the primary explanation."

    status = primary_rule.get("status") or "not_available"
    severity = primary_rule.get("severity") or "not_available"
    rule_id = primary_rule.get("rule_id") or "-"
    attribution = str(primary_rule.get("attribution") or "").lower()

    if status == "confirmed":
        return f"Rule {rule_id} is confirmed with {severity} severity because the metric crossed the threshold and supporting evidence was found."

    if status == "candidate":
        if attribution == "competition":
            return (
                f"Rule {rule_id} is only a candidate. The metric suggests possible steam demand competition, "
                "but direct competition is not fully proven unless another sterilizer clearly starts ramping during the selected stage period."
            )
        return f"Rule {rule_id} is a candidate because additional peer/system confirmation is required."

    if primary_rule.get("triggered"):
        return f"Rule {rule_id} was triggered with {severity} severity."

    return f"Rule {rule_id} was retrieved but not triggered by the current metric value."


def score_explanation(
    score: Any,
    score_band: Any = None,
    *,
    is_weakest: bool = False,
    is_selected_stage: bool = False,
) -> str:
    band = normalize_band(score_band, score)
    base = {
        "excellent": "Excellent — very close to the benchmark.",
        "good": "Good — acceptable and close to the benchmark.",
        "fair": "Fair — noticeable deviation from the benchmark.",
        "poor": "Poor — significant deviation from the benchmark.",
        "critical": "Critical — serious deviation from the benchmark.",
        "unknown": "Unable to classify because the score is unavailable.",
    }.get(band, "Unable to classify because the score is unavailable.")

    if is_selected_stage and band in {"fair", "poor", "critical"}:
        return f"{base} This selected stage needs further analysis."
    if is_selected_stage:
        return f"{base} This selected stage is not the main abnormal area."
    if is_weakest and band in {"fair", "poor", "critical"}:
        return f"{base} This is the weakest part of the cycle."
    if is_weakest:
        return f"{base} This is the lowest stage score in the cycle."
    return base


# =============================================================================
# Evidence helpers
# =============================================================================
def get_evidence_context(scoring_result: Dict[str, Any]) -> Dict[str, Any]:
    metrics = scoring_result.get("metrics", {}) or {}
    evidence = scoring_result.get("evidence_context") or metrics.get("evidence_context") or {}
    return evidence if isinstance(evidence, dict) else {}


def get_evidence_counts(evidence: Dict[str, Any]) -> Dict[str, int]:
    peer = evidence.get("peer_sterilizer_evidence") or {}
    comp = evidence.get("competition_evidence") or {}
    pressure = evidence.get("pressure_time_evidence") or {}

    return {
        "active_peer_count": int(peer.get("active_peer_count") or 0),
        "stage_peer_count": int(peer.get("stage_active_peer_count") or peer.get("selected_stage_peer_count") or 0),
        "concurrent_ramp_count": int(comp.get("concurrent_ramp_count") or 0),
        "shared_pressure_event_count": int(pressure.get("shared_pressure_event_count") or 0),
    }


def evidence_display_value(evidence: Dict[str, Any]) -> str:
    if not evidence:
        return "Not checked"
    if not evidence.get("available"):
        return "Not available"

    counts = get_evidence_counts(evidence)
    return (
        f"Pressure-time checked: {counts['active_peer_count']} peer active, "
        f"{counts['concurrent_ramp_count']} peer ramp, "
        f"{counts['shared_pressure_event_count']} shared pressure event"
    )


def peer_pressure_condition_explanation(
    evidence: Dict[str, Any],
    max_peers: int = 4,
) -> str:
    """Format actual affected-stage pressure values for relevant peer sterilizers."""
    peer = evidence.get("peer_sterilizer_evidence") or {}
    conditions = peer.get("affected_stage_pressure_conditions") or []
    if not conditions:
        # Backward-compatible fallback for evidence payloads created before the
        # compact display records were introduced.
        conditions = (evidence.get("pressure_time_evidence") or {}).get("peer_window_stats") or []

    sentences: List[str] = []
    for item in conditions:
        if not item.get("available"):
            continue
        unit = item.get("source_unit") or item.get("benchmark_unit") or "pressure units"
        minimum = item.get("min_pressure", item.get("raw_min_pressure"))
        mean = item.get("mean_pressure", item.get("raw_mean_pressure"))
        maximum = item.get("max_pressure", item.get("raw_max_pressure"))
        start = item.get("start_pressure", item.get("raw_start_pressure"))
        end = item.get("end_pressure", item.get("raw_end_pressure"))
        condition = item.get("pressure_condition") or "active during the affected stage"
        sentences.append(
            f"{item.get('sterilizer_name') or item.get('field')} was {condition} from "
            f"{item.get('window_start')} to {item.get('window_end')}: minimum {minimum} {unit}, "
            f"mean {mean} {unit}, maximum {maximum} {unit}, start {start} {unit}, and end {end} {unit}."
        )
        if len(sentences) >= max_peers:
            break

    if not sentences:
        return "Individual peer pressure values for the affected-stage period were unavailable."
    return "Affected-stage peer pressure details: " + " ".join(sentences)


def _shorten_lines(lines: List[Any], max_lines: int = 4) -> str:
    clean = [str(x).strip() for x in lines if str(x).strip()]
    return " ".join(clean[:max_lines]) if clean else ""


def humanize_evidence_text(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return ""

    replacements = {
        "selected stage window": "selected stage period",
        "same stage window": "same stage period",
        "stage window": "stage period",
        "Stage-window peer evidence": "Selected-stage peer evidence",
        "pressure-time ramp start": "pressure ramp start",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
        value = value.replace(old.capitalize(), new.capitalize())
    return value


def evidence_explanation(evidence: Dict[str, Any], primary_rule: Optional[Dict[str, Any]] = None) -> str:
    if not evidence:
        return "Peer and pressure-time evidence was not provided to the feedback generator."

    if not evidence.get("available"):
        return str(evidence.get("reason") or "Evidence could not be checked because required context was unavailable.")

    counts = get_evidence_counts(evidence)
    attribution = str(primary_rule.get("attribution") or "").lower() if primary_rule else ""
    status = str(primary_rule.get("status") or "").lower() if primary_rule else ""

    if attribution == "competition" and counts["concurrent_ramp_count"] == 0:
        return (
            f"{counts['active_peer_count']} other sterilizer(s) were active during this cycle, "
            f"but no clear new pressure ramp was detected from another sterilizer during the selected stage period. "
            f"{counts['shared_pressure_event_count']} other sterilizer(s) showed notable pressure movement. "
            "Therefore, direct steam competition is not fully proven by ramp-start evidence. "
            + peer_pressure_condition_explanation(evidence)
        )

    if attribution == "competition" and counts["concurrent_ramp_count"] > 0:
        return (
            f"{counts['concurrent_ramp_count']} other sterilizer(s) started a pressure ramp during the selected stage period. "
            "This supports steam demand competition because another sterilizer likely demanded steam at the same time. "
            + peer_pressure_condition_explanation(evidence)
        )

    if attribution in {"boiler", "bpv"} and primary_rule and primary_rule.get("evidence_reason"):
        # The evaluator includes only the matching auxiliary channel and its
        # actual readings, so Boiler and BPV details never leak into other causes.
        return (
            humanize_evidence_text(primary_rule.get("evidence_reason"))
            + " "
            + peer_pressure_condition_explanation(evidence)
        ).strip()

    if attribution == "network":
        return peer_pressure_condition_explanation(evidence)

    if primary_rule and primary_rule.get("evidence_reason"):
        return humanize_evidence_text(primary_rule.get("evidence_reason"))

    lines = evidence.get("summary_lines") or []
    if lines:
        return humanize_evidence_text(_shorten_lines(lines))

    return "Peer and pressure-time evidence was checked for this cycle."


# =============================================================================
# Diagnosis table generation
# =============================================================================
def make_row(field: str, value: str, explanation: str, severity: str = "neutral") -> Dict[str, str]:
    return {
        "field": field,
        "value": value,
        "explanation": explanation,
        "severity": severity,
    }


def build_cycle_diagnosis_table(
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    score_breakdown = scoring_result.get("score_breakdown", {})
    metrics = scoring_result.get("metrics", {})
    evidence = get_evidence_context(scoring_result)

    cycle_score = scoring_result.get("score")
    cycle_band = scoring_result.get("score_band")
    weakest, weakest_value = weakest_stage(score_breakdown)

    s1 = score_breakdown.get("s1_score")
    s2 = score_breakdown.get("s2_score")
    s3 = score_breakdown.get("s3_score")

    metric_name = primary_rule.get("metric_name") if primary_rule else None
    metric_value = primary_rule.get("metric_value") if primary_rule else None
    pattern = primary_rule.get("pattern") if primary_rule else metrics.get("pattern")
    severity = str(primary_rule.get("severity") or "normal") if primary_rule else "normal"

    return [
        make_row("Cycle score", format_number(cycle_score, 2), score_explanation(cycle_score, cycle_band), normalize_band(cycle_band, cycle_score)),
        make_row("S1 score", format_number(s1, 2), score_explanation(s1, None, is_weakest=weakest == "S1"), normalize_band(None, s1)),
        make_row("S2 score", format_number(s2, 2), score_explanation(s2, None, is_weakest=weakest == "S2"), normalize_band(None, s2)),
        make_row("S3 score", format_number(s3, 2), score_explanation(s3, None, is_weakest=weakest == "S3"), normalize_band(None, s3)),
        make_row("Most affected stage", f"{stage_name(weakest)} ({format_number(weakest_value, 2)})" if weakest else "-", "This is the stage with the lowest score and is the main contributor to the cycle deviation.", normalize_band(None, weakest_value)),
        make_row("Root cause", attribution_display(primary_rule), attribution_explanation(primary_rule), severity),
        make_row("Pattern", str(pattern or "-"), pattern_explanation(pattern), severity),
        make_row("Key metric", f"{metric_name} = {format_metric_value(metric_value)}" if metric_name else "-", threshold_explanation(primary_rule), severity),
        make_row("Rule triggered", str(primary_rule.get("rule_id") or "-") if primary_rule else "-", rule_status_explanation(primary_rule), severity),
        make_row("Extra evidence", evidence_display_value(evidence), evidence_explanation(evidence, primary_rule), severity),
    ]


def build_stage_diagnosis_table(
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: str,
) -> List[Dict[str, str]]:
    score_breakdown = scoring_result.get("score_breakdown", {})
    metrics = scoring_result.get("metrics", {})
    evidence = get_evidence_context(scoring_result)

    cycle_score = scoring_result.get("score")
    cycle_band = scoring_result.get("score_band")
    selected_score = get_stage_score(score_breakdown, selected_stage)
    selected_band = normalize_band(None, selected_score)

    metric_name = primary_rule.get("metric_name") if primary_rule else None
    metric_value = primary_rule.get("metric_value") if primary_rule else None
    pattern = primary_rule.get("pattern") if primary_rule else metrics.get("pattern")
    severity = str(primary_rule.get("severity") or selected_band) if primary_rule else selected_band

    return [
        make_row("Selected analysis", stage_name(selected_stage), f"The user is reviewing {stage_name(selected_stage)}, so this feedback focuses on this stage first.", "neutral"),
        make_row(stage_score_field_label(selected_stage), format_number(selected_score, 2), score_explanation(selected_score, None, is_selected_stage=True), selected_band),
        make_row("Root cause", attribution_display(primary_rule), attribution_explanation(primary_rule), severity),
        make_row("Pattern", str(pattern or "-"), pattern_explanation(pattern), severity),
        make_row("Key metric", f"{metric_name} = {format_metric_value(metric_value)}" if metric_name else "-", threshold_explanation(primary_rule), severity),
        make_row("Rule triggered", str(primary_rule.get("rule_id") or "-") if primary_rule else "-", rule_status_explanation(primary_rule), severity),
        make_row("Extra evidence", evidence_display_value(evidence), evidence_explanation(evidence, primary_rule), severity),
        make_row("Cycle context", f"{format_number(cycle_score, 2)} ({band_label(cycle_score, cycle_band)})", f"The overall cycle score is shown only as context. The current feedback is focused on {stage_name(selected_stage)}.", normalize_band(cycle_band, cycle_score)),
        make_row("Other stage context", other_stage_context(score_breakdown, selected_stage), "These scores help show whether the issue is localised to the selected stage or affects the whole cycle.", "neutral"),
    ]


def build_diagnosis_table(
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str] = None,
) -> List[Dict[str, str]]:
    if selected_stage:
        return build_stage_diagnosis_table(scoring_result, primary_rule, selected_stage)
    return build_cycle_diagnosis_table(scoring_result, primary_rule)


# =============================================================================
# Deterministic explanation, action, and LLM-friendly facts
# =============================================================================
def build_plain_explanation(
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str] = None,
) -> List[str]:
    facts = build_operator_facts(scoring_result, primary_rule, selected_stage, clean_next_actions(primary_rule, []))
    paragraphs = [
        f"{facts['problem_sentence']} {facts['score_sentence']} {facts['metric_sentence']}",
        f"{facts['cause_sentence']} {facts['status_sentence']} {facts['evidence_sentence']}",
    ]
    if facts.get("action_sentence"):
        paragraphs.append(facts["action_sentence"])
    return paragraphs


def clean_next_actions(
    primary_rule: Optional[Dict[str, Any]],
    existing_next_actions: Optional[List[str]] = None,
    selected_stage: Optional[str] = None,
) -> List[str]:
    """
    Important fix:
    - If the analysis finding is confirmed, show the knowledge-base recommendation.
    - If it is only a candidate, do not present corrective text as a proven action.
      Show a verification-first action instead.
    """
    if not primary_rule:
        return ["Continue monitoring. No analysis action is required unless the deviation repeats."]

    status = str(primary_rule.get("status") or "").lower()
    attribution = str(primary_rule.get("attribution") or "").lower()
    recommendation = str(primary_rule.get("recommendation_en") or "").strip()

    if status == "candidate":
        if attribution == "competition":
            return [
                "Verify the suspected steam competition before applying corrective action. Check whether another sterilizer actually starts ramping during the selected stage period. If this repeats, adjust the sterilizer start schedule so a new ramp does not begin while another sterilizer is in Stage 2."
            ]
        if attribution == "boiler":
            return [
                "Verify the suspected boiler/shared steam supply issue. Check whether other sterilizers show the same pressure problem and review boiler pressure data if available."
            ]
        if attribution == "bpv":
            return [
                "Verify the suspected BPV issue using the BPV pressure reading and peer/system behavior from the same affected-stage period before changing valve settings."
            ]
        if attribution == "network":
            return [
                "Verify the suspected steam-network issue by comparing peer sterilizer pressure behavior in the same affected-stage period."
            ]
        if attribution == "local":
            return [
                "Verify the suspected local equipment issue by checking the selected sterilizer while confirming that peers do not show the same behavior."
            ]
        return [
            "Verify this candidate analysis finding with evidence that directly matches the suggested cause before applying corrective action."
        ]

    if recommendation:
        return [recommendation]

    return ["Continue monitoring. No analysis action is required unless the deviation repeats."]


def build_operator_facts(
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str],
    recommended_actions: List[str],
) -> Dict[str, str]:
    score_breakdown = scoring_result.get("score_breakdown", {})
    evidence = get_evidence_context(scoring_result)

    cycle_score = scoring_result.get("score")
    cycle_band = band_label(cycle_score, scoring_result.get("score_band"))
    weakest, weakest_value = weakest_stage(score_breakdown)

    focus_stage = selected_stage or normalize_stage(primary_rule.get("stage") if primary_rule else None) or weakest
    focus_stage_name = stage_name(focus_stage)
    focus_score = get_stage_score(score_breakdown, focus_stage) if focus_stage in VALID_STAGES else cycle_score
    focus_band = band_label(focus_score)

    metric_name = primary_rule.get("metric_name") if primary_rule else None
    metric_value = primary_rule.get("metric_value") if primary_rule else None
    attribution = attribution_simple_name(primary_rule)
    status = str(primary_rule.get("status") or "not_available") if primary_rule else "not_available"
    severity = str(primary_rule.get("severity") or "not_available") if primary_rule else "not_available"
    pattern = primary_rule.get("pattern") if primary_rule else None

    problem_sentence = f"{focus_stage_name} is the main area that needs attention in this cycle."
    score_sentence = (
        f"Its score is {format_number(focus_score, 2)} ({focus_band}), while the overall cycle score is "
        f"{format_number(cycle_score, 2)} ({cycle_band})."
    )

    metric_sentence = ""
    if metric_name:
        metric_sentence = (
            f"The main pressure difference is related to {metric_human_label(metric_name)}. "
            f"The measured deviation is {format_metric_value(metric_value)}. "
            f"{threshold_explanation(primary_rule)}"
        )

    cause_sentence = f"The most likely cause suggested by the analysis rule is {attribution}. {attribution_explanation(primary_rule)}"

    status_sentence = ""
    if status == "confirmed":
        status_sentence = "This analysis finding is confirmed because the rule threshold was exceeded and supporting evidence was found."
    elif status == "candidate":
        status_sentence = "This analysis finding is only a candidate because the metric suggests this cause, but the supporting evidence is not strong enough to fully prove it."
    else:
        status_sentence = "This analysis result should be reviewed together with the pressure chart and supporting evidence."

    evidence_sentence = evidence_explanation(evidence, primary_rule)

    action_sentence = ""
    if recommended_actions:
        action_sentence = "Recommended next step: " + str(recommended_actions[0]).strip()

    return {
        "focus_stage_name": focus_stage_name,
        "focus_score": format_number(focus_score, 2),
        "focus_band": focus_band,
        "cycle_score": format_number(cycle_score, 2),
        "cycle_band": cycle_band,
        "problem_sentence": problem_sentence,
        "score_sentence": score_sentence,
        "metric_sentence": metric_sentence,
        "pattern_sentence": pattern_explanation(pattern),
        "cause_sentence": cause_sentence,
        "status_sentence": status_sentence,
        "evidence_sentence": evidence_sentence,
        "action_sentence": action_sentence,
        "status": status,
        "severity": severity,
        "attribution": attribution,
    }


def metric_human_label(metric_name: Any) -> str:
    metric = str(metric_name or "").lower()
    if "s1" in metric:
        stage = "Stage 1"
    elif "s2" in metric:
        stage = "Stage 2"
    elif "s3" in metric:
        stage = "Stage 3"
    else:
        stage = "the selected stage"

    if "peak" in metric:
        return f"the pressure peak in {stage}"
    if "ramp" in metric:
        return f"the pressure rise/ramp in {stage}"
    if "release" in metric:
        return f"the pressure release in {stage}"
    if "hold" in metric or "holding" in metric:
        return f"the holding pressure in {stage}"
    return f"the pressure behavior in {stage}"


BAD_LLM_PATTERNS = [
    "selected analysis:",
    "diagnostic details:",
    "diagnosis details:",
    "rule id:",
    "root cause:",
    "key metric:",
    "feedback focus:",
    "stage 2 score:",
    "pattern:",
    "industrial sterilizer rca result analysis",
    "implement stage",
    "by starting a new ramp",
    "start a new ramp",
]


def clean_llm_output(text: Any) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []

    raw = raw.replace("**", "")
    raw = re.sub(r"#+\s*", "", raw)
    raw = re.sub(r"(?i)^\s*(summary|explanation paragraphs|diagnostic details)\s*:?\s*", "", raw)

    # Split into paragraphs. If model produces one block, keep it as one paragraph.
    paragraphs = [p.strip(" -\t") for p in re.split(r"\n\s*\n|\r\n\s*\r\n", raw) if p.strip()]

    # Remove label-only / heading-like lines.
    clean: List[str] = []
    for paragraph in paragraphs:
        p = paragraph.strip()
        lower = p.lower()
        if lower in {"summary", "explanation", "diagnosis", "diagnostic details"}:
            continue
        p = re.sub(r"(?i)^\s*(summary|explanation|what this means)\s*:\s*", "", p).strip()
        if p:
            clean.append(make_operator_safe_text(p))

    # Avoid cut-off last paragraph with no punctuation.
    if clean and clean[-1][-1] not in ".!?":
        if len(clean) > 1:
            clean = clean[:-1]

    return clean[:3]


def output_is_operator_friendly(paragraphs: List[str]) -> bool:
    if not paragraphs:
        return False

    joined = " ".join(paragraphs).lower()
    if len(joined) < 120:
        return False

    if any(bad in joined for bad in BAD_LLM_PATTERNS):
        return False

    # Too many raw field separators usually means it copied a table.
    if joined.count(":") >= 4:
        return False

    return True


def build_llm_prompt(operator_facts: Dict[str, str], strict: bool = False) -> str:
    max_words = "120" if strict else "160"
    return f"""
You are writing an explanation for a palm oil mill operator.

Write only the final explanation paragraphs.
Do not include headings.
Do not include bullet points.
Do not include markdown.
Do not write labels such as Root Cause, Rule ID, Pattern, Key Metric, or Diagnostic Details.
Do not mention rule IDs unless absolutely necessary.
Do not tell the operator that another sterilizer started ramping unless the facts say it was detected.
Do not invent actions.
Use simple English and explain what happened, why it matters, and what should be checked next.
Keep the answer under {max_words} words.

Facts to explain:
- {operator_facts['problem_sentence']}
- {operator_facts['score_sentence']}
- {operator_facts['metric_sentence']}
- {operator_facts['pattern_sentence']}
- {operator_facts['cause_sentence']}
- {operator_facts['status_sentence']}
- Evidence: {operator_facts['evidence_sentence']}
- {operator_facts['action_sentence']}

Final explanation:
""".strip()


def generate_llm_operator_explanation(operator_facts: Dict[str, str]) -> Dict[str, Any]:
    model_name = current_llm_model_name()
    if not should_try_llm():
        return {
            "enabled": False,
            "success": False,
            "model_name": model_name,
            "error": "LLM generation is disabled or unavailable.",
            "payload": None,
            "timed_out": False,
        }

    try:
        logger.warning("Starting operator-friendly LLM explanation with %s", model_name)
        prompt = build_llm_prompt(operator_facts, strict=False)
        text = generate_llm_text(prompt)
        paragraphs = clean_llm_output(text)

        if not output_is_operator_friendly(paragraphs):
            logger.warning("First LLM output was not operator-friendly. Retrying with stricter prompt. output=%r", text)
            prompt = build_llm_prompt(operator_facts, strict=True)
            text = generate_llm_text(prompt)
            paragraphs = clean_llm_output(text)

        if not output_is_operator_friendly(paragraphs):
            return {
                "enabled": True,
                "success": False,
                "model_name": model_name,
                "error": "LLM output was generated but rejected because it was still too technical or table-like.",
                "payload": None,
                "timed_out": False,
            }

        return {
            "enabled": True,
            "success": True,
            "model_name": model_name,
            "error": None,
            "payload": {"plain_explanation": paragraphs},
            "timed_out": False,
        }

    except Exception as exc:
        logger.exception("Operator-friendly LLM explanation failed: %s", exc)
        return {
            "enabled": True,
            "success": False,
            "model_name": model_name,
            "error": str(exc),
            "payload": None,
            "timed_out": False,
        }


def operator_stage_name(stage: Any) -> str:
    value = str(stage or "").upper().strip()
    return {
        "S1": "first pressurization phase",
        "S2": "second pressurization phase",
        "S3": "pressure holding phase",
        "CY": "overall cycle",
        "EX": "exhaust phase",
    }.get(value, "selected phase")


def make_operator_safe_text(value: Any) -> str:
    """
    Convert technical wording into Sheet 10 operator wording.

    This is used before sending text to the LLM so the model does not copy
    raw terms such as benchmark, threshold, deviation, Stage 2, S2, or metric
    values into the operator-facing recommendation explanation.
    """
    text = str(value or "").strip()
    if not text:
        return ""

    replacements = [
        (r"(?i)\bbenchmark\b", "normal reference level"),
        (r"(?i)\bthreshold\b", "safe limit"),
        (r"(?i)\bdeviation\b", "difference from normal"),
        (r"(?i)\bmetric value\b", "measured condition"),
        (r"(?i)\bcombined_error\b", "pressure difference"),
        (r"(?i)\bnormalisation\b|\bnormalization\b", "normal comparison"),
        (r"(?i)\bsub-window\b", "phase period"),
        (r"(?i)\bStage\s*1\b|\bS1\b", "first pressurization phase"),
        (r"(?i)\bStage\s*2\b|\bS2\b", "second pressurization phase"),
        (r"(?i)\bStage\s*3\b|\bS3\b", "pressure holding phase"),
        (r"(?i)\bfirst peak\b", "pressure peak in the first pressurization phase"),
        (r"(?i)\bsecond peak\b", "pressure peak in the second pressurization phase"),
        (r"(?i)\bthird peak\b", "pressure peak in the pressure holding phase"),
        (r"(?i)\bMAE_[A-Za-z0-9_]+\b", "pressure difference from normal"),
        (r"(?i)\bRMSE_[A-Za-z0-9_]+\b", "pressure instability"),
        (r"(?i)\bMAE\b", "pressure difference"),
        (r"(?i)\bRMSE\b", "pressure instability"),
        (r"(?i)\bPattern\s*[A-F]\b", "pressure behavior"),
        (r"\b[A-F]\+[A-F](\+[A-F])*\b", "combined pressure behavior"),
        (r"\bP[1-5]\b", "cause group"),
        (r"(?i)\battribution\b", "likely cause"),
        (r"(?i)\bRCA\b", "analysis result"),
        (r"(?i)\brule\s*ID\b", "rule reference"),
        (r"\bS[123]-\d{3}\b|\bCY-\d{3}\b|\bEX-\d{3}\b", "rule reference"),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)

    # Remove bare decimal diagnostic numbers that the LLM may otherwise explain.
    text = re.sub(r"\b0\.\d{2,}\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -:;.")
    return text


def action_text_has_forbidden_terms(text: Any) -> bool:
    value = str(text or "")
    forbidden_patterns = [
        r"\bMAE\b",
        r"\bRMSE\b",
        r"\bthreshold\b",
        r"\brule\s*ID\b",
        r"\bS[123]-\d{3}\b",
        r"\bCY-\d{3}\b",
        r"\bEX-\d{3}\b",
        r"\bPattern\s*[A-F]\b",
        r"\b[A-F]\+[A-F](\+[A-F])*\b",
        r"\bcombined_error\b",
        r"\bnormalisation\b",
        r"\bnormalization\b",
        r"\bsub-window\b",
        r"\bbenchmark\b",
        r"\bP[1-5]\b",
        r"\battribution\b",
        r"\bmetric value\b",
        r"\bdeviation\b",
        r"\bStage\s*[123]\b",
        r"\bS[123]\b",
        r"\bfirst peak\b",
        r"\bsecond peak\b",
        r"\bthird peak\b",
        r"\b\d+\.\d{3,}\b",
    ]
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in forbidden_patterns)


def build_action_expansion_facts(
    *,
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str],
    recommended_actions: List[str],
    operator_facts: Dict[str, str],
) -> Dict[str, str]:
    """
    Build clean Sheet 10 operator-friendly facts for explaining the original
    recommended action.

    The original KB recommendation can contain technical wording such as
    benchmark, S2 window, second peak, or numeric diagnostic values. We keep
    the recommendation meaning, but convert the wording before sending it to
    the LLM.
    """
    score_breakdown = scoring_result.get("score_breakdown", {}) or {}
    evidence = get_evidence_context(scoring_result)

    raw_original_action = str(recommended_actions[0]).strip() if recommended_actions else "Continue monitoring and verify the cycle if the issue repeats."
    original_action = make_operator_safe_text(raw_original_action)
    if not original_action:
        original_action = "Continue monitoring and verify the cycle if the issue repeats"

    focus_stage = selected_stage or normalize_stage(primary_rule.get("stage") if primary_rule else None)
    if not focus_stage:
        focus_stage, _ = weakest_stage(score_breakdown)

    focus_stage_name = operator_stage_name(focus_stage)
    focus_score = get_stage_score(score_breakdown, focus_stage) if focus_stage in VALID_STAGES else scoring_result.get("score")
    focus_band = band_label(focus_score)

    attribution = attribution_simple_name(primary_rule)
    attribution_plain = make_operator_safe_text(attribution)
    status = str(primary_rule.get("status") or "not_available") if primary_rule else "not_available"
    severity = str(primary_rule.get("severity") or "not_available") if primary_rule else "not_available"
    metric_name = primary_rule.get("metric_name") if primary_rule else None

    pressure_area = "pressure behavior"
    if metric_name:
        pressure_area = make_operator_safe_text(metric_human_label(metric_name))

    metric_fact = f"The main pressure concern is related to {pressure_area}."

    status_fact = "The analysis result should be reviewed with the available pressure evidence."
    if status == "confirmed":
        status_fact = "The analysis result is supported by the pressure behavior and available evidence."
    elif status == "candidate":
        status_fact = "This is still a possible cause and should be verified before major corrective action."

    pattern_fact = make_operator_safe_text(
        operator_facts.get("pattern_sentence") or "The pressure behavior is not following the normal reference level."
    )

    return {
        "original_action": original_action,
        "raw_original_action": raw_original_action,
        "focus_stage_name": focus_stage_name,
        "focus_score": format_number(focus_score, 2),
        "focus_band": focus_band,
        "root_cause": attribution_plain,
        "status": status,
        "severity": severity,
        "metric_fact": metric_fact,
        "pattern_fact": pattern_fact,
        "evidence_fact": make_operator_safe_text(evidence_explanation(evidence, primary_rule)),
        "status_fact": status_fact,
    }


BAD_ACTION_LLM_PATTERNS = [
    "new recommended action",
    "alternative recommendation",
    "instead of the recommended action",
    "replace the recommendation",
    "ignore the recommendation",
    "diagnostic details:",
    "rule id:",
    "key metric:",
    "root cause:",
    "recommended action:",
    "action plan:",
    "steps:",
    "benchmark",
    "threshold",
    "deviation",
    "metric value",
    "pattern ",
    "stage 1",
    "stage 2",
    "stage 3",
    "first peak",
    "second peak",
    "third peak",
    "mae",
    "rmse",
    "normalisation",
    "normalization",
    "sub-window",
]

def clean_action_expansion_output(text: Any) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []

    raw = raw.replace("**", "")
    raw = re.sub(r"#+\s*", "", raw)
    raw = re.sub(
        r"(?i)^\s*(why this action is recommended|action explanation|recommendation explanation|summary)\s*:?\s*",
        "",
        raw,
    )

    paragraphs = [p.strip(" -\t") for p in re.split(r"\n\s*\n|\r\n\s*\r\n", raw) if p.strip()]

    clean: List[str] = []
    for paragraph in paragraphs:
        p = re.sub(
            r"(?i)^\s*(why this action is recommended|action explanation|recommendation explanation|summary)\s*:?\s*",
            "",
            paragraph.strip(),
        ).strip()
        if p:
            clean.append(make_operator_safe_text(p))

    if clean and clean[-1][-1] not in ".!?":
        if len(clean) > 1:
            clean = clean[:-1]

    return clean[:2]


def action_expansion_is_valid(paragraphs: List[str]) -> bool:
    if not paragraphs:
        return False

    joined = " ".join(paragraphs).strip()
    lowered = joined.lower()

    if len(joined) < 80:
        return False

    if any(bad in lowered for bad in BAD_ACTION_LLM_PATTERNS):
        return False

    if action_text_has_forbidden_terms(joined):
        return False

    # Too many labels usually means the model copied a report/table style.
    if joined.count(":") >= 3:
        return False

    return True


def build_action_expansion_prompt(action_facts: Dict[str, str], strict: bool = False) -> str:
    max_words = "160" if strict else "220"

    return f"""
You are explaining an EXISTING sterilizer recommendation to a palm oil mill operator.

Your job:
Explain why the given action is suitable and reliable, using only the supplied context.
Do not create a new recommendation. Do not add equipment checks that are not implied by the source action.

Source of truth action:
{action_facts['original_action']}

Plain context to use:
- Affected phase: {action_facts['focus_stage_name']}.
- Performance level: {action_facts['focus_band']}.
- Likely equipment cause: {action_facts['root_cause']}.
- {action_facts['metric_fact']}
- {action_facts['pattern_fact']}
- {action_facts['status_fact']}
- Evidence: {action_facts['evidence_fact']}

STRICT RULES:
- Explain the reason for the source action, not a new action plan.
- Ground the explanation on the affected phase, likely equipment cause, and evidence above.
- Do not mention raw scores, raw numbers, rule names, or diagnostic values.
- Do not use headings, markdown, bullet points, labels, or tables.
- Do not use these words: MAE, RMSE, threshold, rule ID, Pattern, benchmark, deviation, metric value, normalisation, normalization, sub-window, P1, P2, P3, P4, P5, attribution, Stage 1, Stage 2, Stage 3, S1, S2, S3, first peak, second peak, third peak.
- Use "normal reference level" instead of benchmark.
- Use "difference from normal" instead of deviation.
- Use "first pressurization phase", "second pressurization phase", or "pressure holding phase" instead of Stage/S1/S2/S3.
- Keep it under {max_words} words.

Write one or two short paragraphs. The explanation must be complete enough to tell the operator why this action is relevant and what problem it helps verify or correct.
""".strip()


def deterministic_action_explanation(action_facts: Dict[str, str]) -> List[str]:
    """
    Safe Sheet 10 fallback for action explanation.
    It avoids technical terms and explains only why the existing action is useful.
    """
    stage = action_facts.get("focus_stage_name", "the selected phase")
    cause = action_facts.get("root_cause", "the suspected equipment condition")
    status = action_facts.get("status", "not_available")
    action = action_facts.get("original_action", "the recommended check")
    evidence = action_facts.get("evidence_fact", "The pressure chart and equipment condition should be checked together.")

    if status == "candidate":
        return [
            f"This action is recommended as a verification step because the likely cause is still not fully proven. The {stage} shows pressure behavior that needs checking, and the available evidence should be compared with the sterilizer schedule and peer pressure charts before a major correction is made.",
            f"The action is useful because it helps confirm whether {cause} is really affecting the cycle. {evidence}"
        ]

    return [
        f"This action is recommended because the {stage} shows pressure behavior that is not following the normal reference level, and the available evidence points toward {cause}.",
        f"The action helps the operator focus on the equipment condition most likely related to this pressure problem, instead of checking unrelated parts first. {evidence}"
    ]


def generate_ai_action_explanation(
    *,
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str],
    recommended_actions: List[str],
    operator_facts: Dict[str, str],
) -> Dict[str, Any]:
    """
    Use the LLM to elaborate the existing recommended action.

    This function NEVER replaces recommended_actions. It only returns an
    explanation of why the original action is recommended.
    """
    model_name = current_llm_model_name()

    action_facts = build_action_expansion_facts(
        scoring_result=scoring_result,
        primary_rule=primary_rule,
        selected_stage=selected_stage,
        recommended_actions=recommended_actions,
        operator_facts=operator_facts,
    )

    fallback_paragraphs = deterministic_action_explanation(action_facts)

    if not should_try_llm():
        return {
            "enabled": False,
            "success": False,
            "model_name": model_name,
            "error": "LLM generation is disabled or unavailable.",
            "paragraphs": fallback_paragraphs,
            "fallback_used": True,
            "original_action": action_facts.get("original_action"),
        }

    try:
        logger.warning("Starting AI-expanded recommendation explanation with %s", model_name)

        prompt = build_action_expansion_prompt(action_facts, strict=False)
        text = generate_llm_text(prompt)
        paragraphs = clean_action_expansion_output(text)

        if not action_expansion_is_valid(paragraphs):
            logger.warning(
                "First AI action explanation was rejected. Retrying with stricter prompt. output=%r",
                text,
            )
            prompt = build_action_expansion_prompt(action_facts, strict=True)
            text = generate_llm_text(prompt)
            paragraphs = clean_action_expansion_output(text)

        if not action_expansion_is_valid(paragraphs):
            return {
                "enabled": True,
                "success": False,
                "model_name": model_name,
                "error": "LLM action explanation was generated but rejected because it was too technical, table-like, or unsafe.",
                "paragraphs": fallback_paragraphs,
                "fallback_used": True,
                "original_action": action_facts.get("original_action"),
            }

        return {
            "enabled": True,
            "success": True,
            "model_name": model_name,
            "error": None,
            "paragraphs": paragraphs,
            "fallback_used": False,
            "original_action": action_facts.get("original_action"),
        }

    except Exception as exc:
        logger.exception("AI-expanded recommendation explanation failed: %s", exc)
        return {
            "enabled": True,
            "success": False,
            "model_name": model_name,
            "error": str(exc),
            "paragraphs": fallback_paragraphs,
            "fallback_used": True,
            "original_action": action_facts.get("original_action"),
        }


# =============================================================================
# Public generation functions used by rca_pipeline
# =============================================================================
def feedback_title(
    score_band: str,
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str] = None,
) -> str:
    if selected_stage:
        if primary_rule and primary_rule.get("status") in {"confirmed", "candidate"}:
            status = str(primary_rule.get("status") or "").title()
            attribution = primary_rule.get("attribution") or "Analysis"
            return f"{stage_name(selected_stage)} {attribution} Analysis {status}"
        return f"{stage_name(selected_stage)} Analysis Feedback"

    if primary_rule and primary_rule.get("status") in {"confirmed", "candidate"}:
        status = str(primary_rule.get("status") or "").title()
        stage = primary_rule.get("stage") or "Cycle"
        attribution = primary_rule.get("attribution") or "Analysis"
        return f"{stage} {attribution} Analysis {status}"

    if score_band in {"poor", "critical"}:
        return "Significant cycle deviation detected"
    if score_band == "fair":
        return "Moderate cycle deviation detected"
    return "Cycle close to benchmark"


def build_human_feedback(
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    next_actions: List[str],
    selected_stage: Optional[str] = None,
    augmented_context: Optional[Dict[str, Any]] = None,
    matched_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    diagnosis_table = build_diagnosis_table(scoring_result, primary_rule, selected_stage)
    recommended_actions = clean_next_actions(primary_rule, next_actions, selected_stage)
    operator_facts = build_operator_facts(scoring_result, primary_rule, selected_stage, recommended_actions)
    plain_explanation = build_plain_explanation(scoring_result, primary_rule, selected_stage)

    # Sheet 10 plain-language layer. This is the new operator-facing output.
    # It does not replace scoring/rule logic; it only translates the technical
    # RCA result into simple language and keeps technical details collapsed.
    plain_language_feedback = None
    if build_plain_language_feedback is not None:
        try:
            plain_language_feedback = build_plain_language_feedback(
                scoring_result=scoring_result,
                primary_rule=primary_rule,
                selected_stage=selected_stage,
                recommended_actions=recommended_actions,
                matched_rules=matched_rules or [],
                augmented_context=augmented_context or {},
            )
        except Exception as exc:
            logger.exception("Sheet 10 plain-language layer failed: %s", exc)
            plain_language_feedback = None

    # AI-expanded recommendation explanation.
    # Important: this explains the original recommendation only. It does NOT
    # replace or create recommended_actions.
    ai_action_result = generate_ai_action_explanation(
        scoring_result=scoring_result,
        primary_rule=primary_rule,
        selected_stage=selected_stage,
        recommended_actions=recommended_actions,
        operator_facts=operator_facts,
    )

    technical_details = {}
    if primary_rule:
        technical_details = {
            "rule_id": primary_rule.get("rule_id"),
            "rec_key": primary_rule.get("rec_key"),
            "stage": primary_rule.get("stage"),
            "priority": primary_rule.get("priority"),
            "attribution": primary_rule.get("attribution"),
            "pattern": primary_rule.get("pattern"),
            "metric_name": primary_rule.get("metric_name"),
            "metric_value": primary_rule.get("metric_value"),
            "warning_threshold": primary_rule.get("warn_low"),
            "critical_threshold": primary_rule.get("critical_value"),
            "status": primary_rule.get("status"),
            "severity": primary_rule.get("severity"),
            "target_metric": primary_rule.get("target_metric"),
            "selected_stage": selected_stage,
            "evidence_confirmed": primary_rule.get("evidence_confirmed"),
            "evidence_reason": primary_rule.get("evidence_reason"),
            "evidence_summary": primary_rule.get("evidence_summary"),
            "evidence_context": get_evidence_context(scoring_result),
        }

    human_feedback: Dict[str, Any] = {
        "focus_type": "stage" if selected_stage else "cycle",
        "selected_stage": selected_stage,
        # Preferred Sheet 10 payload for the updated frontend.
        # Keep both names so old and new UI versions can read the same stable data.
        "plain_language_feedback": plain_language_feedback,
        "plain_language": plain_language_feedback,
        "operator_explanation": plain_language_feedback,
        "plain_language_generation": (plain_language_feedback or {}).get("generation") if isinstance(plain_language_feedback, dict) else None,
        "diagnosis_table": diagnosis_table,
        "plain_explanation": plain_explanation,
        "recommended_actions": recommended_actions,
        "ai_action_explanation": ai_action_result.get("paragraphs") or [],
        "ai_action_generation": {
            "enabled": bool(ai_action_result.get("enabled")),
            "success": bool(ai_action_result.get("success")),
            "model_name": ai_action_result.get("model_name"),
            "error": ai_action_result.get("error"),
            "fallback_used": bool(ai_action_result.get("fallback_used")),
            "original_action": ai_action_result.get("original_action"),
        },
        "technical_details": technical_details,
        "operator_facts": operator_facts,
    }

    # Backward compatibility: old UI reads plain_explanation. Keep it populated,
    # but the updated UI will prefer plain_language_feedback as Layer 1.
    if isinstance(plain_language_feedback, dict) and plain_language_feedback.get("paragraphs"):
        human_feedback["plain_language_paragraphs"] = plain_language_feedback.get("paragraphs")

    llm_result = generate_llm_operator_explanation(operator_facts)

    human_feedback["llm_generation"] = {
        "enabled": bool(llm_result.get("enabled")),
        "success": bool(llm_result.get("success")),
        "model_name": llm_result.get("model_name"),
        "error": llm_result.get("error"),
        "timed_out": bool(llm_result.get("timed_out")),
        "fallback_used": not bool(llm_result.get("success")) if llm_result.get("enabled") else False,
    }

    logger.warning("RCA LLM explanation generation: %s", human_feedback["llm_generation"])
    logger.warning("RCA AI action explanation generation: %s", human_feedback["ai_action_generation"])

    # Keep the older paragraph explanation only for backward compatibility.
    # Do NOT let it overwrite the Sheet 10 structured explanation, because the
    # older paragraph output is less reliable and can collapse all information
    # into "What happened" on the frontend.
    if llm_result.get("success"):
        llm_payload = llm_result.get("payload") or {}
        llm_explanation = llm_payload.get("plain_explanation")
        if isinstance(llm_explanation, list) and llm_explanation:
            clean_explanation = [str(x).strip() for x in llm_explanation if str(x).strip()]
            if clean_explanation:
                human_feedback["legacy_llm_plain_explanation"] = clean_explanation[:3]

    return human_feedback


def generate_rule_based_feedback(
    scoring_result: Dict[str, Any],
    evaluated_rules: List[Dict[str, Any]],
    augmented_context: Dict[str, Any],
) -> Dict[str, Any]:
    metrics = scoring_result.get("metrics", {})
    score_breakdown = scoring_result.get("score_breakdown", {})
    score_band = normalize_band(scoring_result.get("score_band"), scoring_result.get("score"))
    cycle_score = scoring_result.get("score")

    selected_stage = get_selected_stage(metrics)
    preferred_stage = selected_stage or normalize_stage(metrics.get("affected_stage"))
    primary = choose_primary_rule(evaluated_rules, preferred_stage=preferred_stage)

    summary_lines: List[str] = []
    if selected_stage:
        selected_score = get_stage_score(score_breakdown, selected_stage)
        summary_lines.append(
            f"{stage_name(selected_stage)} is the selected analysis target. "
            f"Its score is {format_number(selected_score, 2)} ({band_label(selected_score)})."
        )
        summary_lines.append(
            f"Overall cycle score is {format_number(cycle_score, 2)} ({band_label(cycle_score, score_band)}) and is shown as context."
        )
    else:
        summary_lines.extend([
            f"Cycle score is {format_number(cycle_score, 2)} ({band_label(cycle_score, score_band)}).",
            (
                f"S1={format_number(score_breakdown.get('s1_score'), 2)}, "
                f"S2={format_number(score_breakdown.get('s2_score'), 2)}, "
                f"S3={format_number(score_breakdown.get('s3_score'), 2)}."
            ),
        ])

    affected_stage = metrics.get("affected_stage")
    # User-facing summary should use the selected primary RCA rule pattern.
    # metrics["pattern"] can be a raw/cycle-level signal pattern and may differ
    # from the RCA rule pattern, which causes confusing UI mismatch.
    primary_pattern = primary.get("pattern") if primary else None
    raw_detected_pattern = metrics.get("pattern")
    display_pattern = primary_pattern or raw_detected_pattern

    if affected_stage and not selected_stage:
        summary_lines.append(f"The most affected stage is {affected_stage}.")
    if display_pattern:
        summary_lines.append(f"Detected analysis pattern: {display_pattern}.")

    if primary:
        metric_name = primary.get("metric_name")
        metric_value = primary.get("metric_value")
        if metric_name and metric_value is not None:
            summary_lines.append(f"The key analysis metric is {metric_name}={format_metric_value(metric_value)}.")

        status = primary.get("status")
        severity = primary.get("severity")
        if status == "confirmed":
            summary_lines.append(f"Rule {primary.get('rule_id')} is confirmed with {severity} severity.")
        elif status == "candidate":
            summary_lines.append(f"Rule {primary.get('rule_id')} is a candidate analysis finding with {severity} severity.")
        elif primary.get("triggered"):
            summary_lines.append(f"Rule {primary.get('rule_id')} is triggered with {severity} severity.")

    recommendation = primary.get("recommendation_en") if primary else None
    recommendation_bm = primary.get("recommendation_bm") if primary else None

    notes: List[str] = []
    if selected_stage:
        notes.append(
            f"This feedback is stage-focused because the user selected {stage_name(selected_stage)}. "
            "Overall cycle and other stage scores are included only as supporting context."
        )

    evidence_context = get_evidence_context(scoring_result)
    if evidence_context:
        notes.append(evidence_explanation(evidence_context, primary))

    if primary and primary.get("status") == "candidate":
        attribution = str(primary.get("attribution") or "").strip().lower()
        required = {
            "boiler": "matching Boiler pressure and shared peer/system behavior",
            "bpv": "matching BPV pressure and peer/system behavior",
            "competition": "a concurrent peer pressure-ramp start",
            "network": "matching peer/system pressure behavior",
            "local": "evidence that the issue is isolated to the selected sterilizer",
        }.get(attribution, "evidence that directly matches the suggested cause")
        notes.append(
            "This analysis finding is marked as a candidate because it still needs " + required + "."
        )

    if metrics.get("data_quality_score") is not None and metrics.get("data_quality_score") < 85:
        notes.append("Data quality may affect this analysis. Verify sensor and database readings.")

    next_actions: List[str] = []
    if recommendation:
        next_actions.append(str(recommendation).strip())
    if not next_actions:
        next_actions.append("Continue monitoring. No analysis rule exceeded the warning threshold.")

    human_feedback = build_human_feedback(
        scoring_result=scoring_result,
        primary_rule=primary,
        next_actions=next_actions,
        selected_stage=selected_stage,
        augmented_context=augmented_context,
        matched_rules=evaluated_rules,
    )

    return {
        "title": feedback_title(score_band, primary, selected_stage=selected_stage),
        "summary": " ".join(summary_lines),
        "primary_rule": primary,
        "matched_rules": evaluated_rules,
        "recommendation": recommendation,
        "recommendation_bm": recommendation_bm,
        "next_actions": next_actions,
        "notes": notes,
        "human_feedback": human_feedback,
        "augmented_context": augmented_context,
    }
