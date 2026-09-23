from typing import Any, Dict, List, Optional
import re


# -----------------------------------------------------------------------------
# RCA rule evaluator with peer + competition evidence support
# -----------------------------------------------------------------------------
# Main change:
# - Some attributions should not become Confirmed from the selected cycle alone.
# - Competition requires concurrent ramp evidence.
# - Boiler / Network / BPV shared causes require peer/system evidence.
# - Local can be confirmed when selected sterilizer is abnormal and peers are not
#   active/competing.
# -----------------------------------------------------------------------------


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def get_metric_value(metrics: Dict[str, Any], metric_name: str) -> Optional[float]:
    if not metric_name:
        return None

    if metric_name in metrics:
        return safe_float(metrics.get(metric_name))

    for key, value in metrics.items():
        if str(key).lower() == str(metric_name).lower():
            return safe_float(value)

    return None


def extract_primary_metric(chunk: Dict[str, Any]) -> Optional[str]:
    related = chunk.get("related_scoring_metrics") or []
    if related:
        return str(related[0])

    metric = chunk.get("threshold_metric_name")
    if metric:
        found = re.findall(
            r"\b(?:MAE|RMSE|combined|osc_ratio|mean_error|local_dev|N_conc|low_count)[A-Za-z0-9_]*\b",
            str(metric),
        )
        if found:
            return found[0]

    return None


def parse_thresholds(chunk: Dict[str, Any]) -> Dict[str, Optional[float]]:
    return {
        "warn_low": safe_float(chunk.get("warn_low")),
        "critical_value": safe_float(chunk.get("critical_value")),
        "warn_high": safe_float(chunk.get("warn_high_critical_starts_here")),
    }


def _text_for_rule(chunk: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(chunk.get("pattern", "")),
            str(chunk.get("text", "")),
            str(chunk.get("search_text", "")),
            str(chunk.get("recommendation_en", "")),
            str(chunk.get("target_metric", "")),
            str(chunk.get("attribution", "")),
        ]
    ).lower()


def rule_requires_extra_confirmation(chunk: Dict[str, Any]) -> bool:
    """
    Rules involving shared/system causes should require evidence beyond the
    selected cycle.
    """
    text = _text_for_rule(chunk)
    attribution = str(chunk.get("attribution") or "").lower().strip()
    priority = str(chunk.get("priority") or "").upper().strip()

    peer_phrases = [
        "≥2 sterilizers",
        ">=2 sterilizers",
        "multiple sterilizers",
        "same pattern",
        "pattern f",
        "peer",
        "all sts",
        "across all st",
        "concurrent",
        "competition",
        "new sterilizer ramp",
        "demand collision",
    ]

    shared_attributions = {"competition", "boiler", "bpv", "network"}
    shared_priorities = {"P1", "P2", "P3", "P4"}

    return (
        any(phrase.lower() in text for phrase in peer_phrases)
        or attribution in shared_attributions
        or priority in shared_priorities
    )


def _evidence_flags(evidence: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    if not evidence:
        return {}
    flags = evidence.get("confirmation_flags") or {}
    return {str(k): bool(v) for k, v in flags.items()}


def _evidence_summary(evidence: Optional[Dict[str, Any]]) -> str:
    if not evidence:
        return "No peer or concurrent-demand evidence was provided to the analysis evaluator."

    lines = evidence.get("summary_lines") or []
    if lines:
        return " ".join(str(x) for x in lines if x)

    reason = evidence.get("reason")
    if reason:
        return str(reason)

    return "Pressure-time peer/competition evidence was checked, but no summary was generated."


def _format_auxiliary_pressure_evidence(
    evidence: Optional[Dict[str, Any]],
    cause_key: str,
) -> str:
    """Format only the equipment channel relevant to the evaluated cause."""
    if not evidence:
        return ""

    item = (evidence.get("auxiliary_pressure_evidence") or {}).get(cause_key) or {}
    stats = item.get("stage_window_stats") or {}
    if not item.get("available") or not stats.get("available"):
        return ""

    display_name = str(item.get("display_name") or cause_key.upper())
    unit = stats.get("source_unit") or stats.get("benchmark_unit") or "pressure units"
    minimum = stats.get("raw_min_pressure", stats.get("min_pressure"))
    mean = stats.get("raw_mean_pressure", stats.get("mean_pressure"))
    maximum = stats.get("raw_max_pressure", stats.get("max_pressure"))
    start = stats.get("raw_start_pressure", stats.get("start_pressure"))
    end = stats.get("raw_end_pressure", stats.get("end_pressure"))
    condition = item.get("pressure_condition") or "recorded during the affected stage"

    return (
        f"{display_name} pressure ({item.get('field')}) was {condition} during the affected-stage window "
        f"from {stats.get('window_start')} to {stats.get('window_end')}: "
        f"minimum {minimum} {unit}, mean {mean} {unit}, maximum {maximum} {unit}, "
        f"start {start} {unit}, and end {end} {unit}. "
        "No normal equipment reference threshold was supplied, so these readings are supporting observations only."
    )


def evidence_confirms_rule(chunk: Dict[str, Any], evidence: Optional[Dict[str, Any]]) -> bool:
    if not evidence or not evidence.get("available"):
        return False

    attribution = str(chunk.get("attribution") or "").lower().strip()
    priority = str(chunk.get("priority") or "").upper().strip()
    flags = _evidence_flags(evidence)

    if attribution == "competition" or priority == "P3":
        return flags.get("competition_confirmation_available", False)

    if attribution == "boiler" or priority == "P1":
        return bool(
            flags.get("boiler_or_system_confirmation_available", False)
            and flags.get("boiler_pressure_evidence_available", False)
        )

    if attribution == "network" or priority == "P4":
        return flags.get("boiler_or_system_confirmation_available", False) or flags.get(
            "peer_confirmation_available", False
        )

    if attribution == "bpv" or priority == "P2":
        return bool(
            flags.get("peer_confirmation_available", False)
            and flags.get("bpv_pressure_evidence_available", False)
        )

    if attribution == "local" or priority == "P5":
        return flags.get("local_confirmation_available", False)

    return flags.get("peer_confirmation_available", False)


def evidence_reason_for_rule(chunk: Dict[str, Any], evidence: Optional[Dict[str, Any]]) -> str:
    if not evidence:
        return "No extra evidence was available."

    attribution = str(chunk.get("attribution") or "").lower().strip()
    priority = str(chunk.get("priority") or "").upper().strip()
    flags = _evidence_flags(evidence)
    summary = _evidence_summary(evidence)

    if attribution == "competition" or priority == "P3":
        if flags.get("competition_confirmation_available"):
            return "Competition is supported by pressure-time concurrent ramp evidence. " + summary
        return "Competition could not be fully confirmed because no peer pressure-time ramp start was detected in the selected stage window. " + summary

    if attribution == "boiler" or priority == "P1":
        equipment = _format_auxiliary_pressure_evidence(evidence, "boiler")
        if evidence_confirms_rule(chunk, evidence):
            return "Shared steam supply evidence is available because Boiler pressure and multiple peer sterilizers were observed in the same stage window. " + equipment + " " + summary
        missing = []
        if not flags.get("boiler_pressure_evidence_available"):
            missing.append("Boiler pressure")
        if not flags.get("boiler_or_system_confirmation_available"):
            missing.append("shared peer/system behavior")
        return "Boiler/shared supply could not be fully confirmed because " + " and ".join(missing or ["supporting evidence"]) + " was not available. " + equipment + " " + summary

    if attribution == "bpv" or priority == "P2":
        equipment = _format_auxiliary_pressure_evidence(evidence, "bpv")
        if evidence_confirms_rule(chunk, evidence):
            return "BPV evidence is available because BPV pressure and peer/system activity were observed in the same stage window. " + equipment + " " + summary
        missing = []
        if not flags.get("bpv_pressure_evidence_available"):
            missing.append("BPV pressure")
        if not flags.get("peer_confirmation_available"):
            missing.append("peer/system behavior")
        return "BPV could not be fully confirmed because " + " and ".join(missing or ["supporting evidence"]) + " was not available. " + equipment + " " + summary

    if attribution == "network" or priority == "P4":
        if flags.get("boiler_or_system_confirmation_available") or flags.get("peer_confirmation_available"):
            return "System/network evidence is available because peer sterilizers show pressure-time activity or pressure movement in the same stage window. " + summary
        return "System/network issue could not be fully confirmed because peer evidence was insufficient. " + summary

    if attribution == "local" or priority == "P5":
        if flags.get("local_confirmation_available"):
            return "Local evidence is supported because no peer sterilizer or concurrent ramp evidence was detected in the same window. " + summary
        return "Local issue is not fully confirmed because other peer sterilizers were active or competition evidence exists. " + summary

    return summary


def evaluate_single_rule(
    chunk: Dict[str, Any],
    metrics: Dict[str, Any],
    peer_confirmation_available: bool = False,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metric_name = extract_primary_metric(chunk)
    metric_value = get_metric_value(metrics, metric_name) if metric_name else None

    thresholds = parse_thresholds(chunk)
    warn_low = thresholds["warn_low"]
    critical_value = thresholds["critical_value"]

    if metric_value is None:
        severity = "not_evaluable"
        triggered = False
        reason = f"Required metric {metric_name} is not available in scoring output."
    elif critical_value is not None and metric_value > critical_value:
        severity = "critical"
        triggered = True
        reason = f"{metric_name}={metric_value:.4f} exceeds critical threshold {critical_value}."
    elif warn_low is not None and metric_value > warn_low:
        severity = "warning"
        triggered = True
        reason = f"{metric_name}={metric_value:.4f} exceeds warning threshold {warn_low}."
    else:
        severity = "normal"
        triggered = False
        if warn_low is not None:
            reason = f"{metric_name}={metric_value:.4f} is within normal range ≤ {warn_low}."
        else:
            reason = f"{metric_name}={metric_value:.4f} did not trigger the rule."

    requires_extra = rule_requires_extra_confirmation(chunk)
    evidence_confirmed = evidence_confirms_rule(chunk, evidence)
    evidence_reason = evidence_reason_for_rule(chunk, evidence)

    if not triggered:
        status = "not_triggered"
    elif evidence_confirmed:
        status = "confirmed"
        reason += " Extra evidence confirms this analysis finding. " + evidence_reason
    elif requires_extra and not evidence_confirmed:
        status = "candidate"
        reason += " Extra evidence is required before confirming this analysis finding. " + evidence_reason
    else:
        status = "confirmed"
        if evidence and evidence.get("available"):
            reason += " " + evidence_reason

    return {
        "rule_id": chunk.get("rule_id"),
        "rec_key": chunk.get("rec_key"),
        "stage": chunk.get("stage"),
        "priority": chunk.get("priority"),
        "attribution": chunk.get("attribution"),
        "pattern": chunk.get("pattern"),
        "ml_label": chunk.get("ml_label"),
        "urgency": chunk.get("urgency"),
        "target_metric": chunk.get("target_metric"),
        "metric_name": metric_name,
        "metric_value": metric_value,
        "warn_low": warn_low,
        "critical_value": critical_value,
        "severity": severity,
        "status": status,
        "triggered": triggered,
        "requires_peer_confirmation": requires_extra,
        "peer_confirmation_available": bool(peer_confirmation_available or evidence_confirmed),
        "evidence_confirmed": evidence_confirmed,
        "evidence_reason": evidence_reason,
        "evidence_summary": _evidence_summary(evidence),
        "reason": reason,
        "score_warn": chunk.get("score_warn"),
        "score_crit": chunk.get("score_crit"),
        "cap": chunk.get("cap"),
        "recommendation_en": chunk.get("recommendation_en"),
        "recommendation_bm": chunk.get("recommendation_bm"),
        "_vector_score": chunk.get("_vector_score"),
        "_rerank_score": chunk.get("_rerank_score"),
    }


def severity_rank(item: Dict[str, Any]) -> int:
    severity_order = {
        "critical": 4,
        "warning": 3,
        "normal": 2,
        "not_evaluable": 1,
    }
    status_bonus = {
        "confirmed": 3,
        "candidate": 2,
        "not_triggered": 0,
    }

    return severity_order.get(item.get("severity"), 0) * 10 + status_bonus.get(item.get("status"), 0)


def evaluate_rules(
    chunks: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    peer_confirmation_available: bool = False,
    evidence: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    evaluated = [
        evaluate_single_rule(
            chunk=chunk,
            metrics=metrics,
            peer_confirmation_available=peer_confirmation_available,
            evidence=evidence,
        )
        for chunk in chunks
    ]

    evaluated.sort(
        key=lambda item: (
            severity_rank(item),
            float(item.get("_rerank_score") or 0.0),
            float(item.get("_vector_score") or 0.0),
        ),
        reverse=True,
    )

    return evaluated
