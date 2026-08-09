"""
Sheet 10 Plain Language Layer for SmartMill / Novaflow RCA V4.

Purpose
-------
This module converts the technical RCA result into an operator-facing RCA
feedback card.

Design goals in this version
----------------------------
1. AI is always attempted first when ENABLE_LLM_GENERATION=true.
2. Long loading time is allowed. There is no short timeout in this file.
3. Fallback is used only when the AI cannot work at all:
   - LLM disabled
   - LLM service unavailable
   - model returns empty text
   - model raises an exception in llm_service.py
4. If the AI output is weak, vague, broken, or contains minor technical wording,
   the system DOES NOT immediately use fallback. Instead, it repairs the final
   operator text using deterministic Sheet 10 / knowledge-base grounded facts.
5. The visible result is kept close to the preferred operator style:
   - clear what happened
   - specific most likely cause
   - practical action list
   - priority consistent with RCA status and severity

Important
---------
The scoring logic, rule matching, retrieval, and recommendation matrix are not
changed here. This file only changes the language shown to the operator.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from app.rag.llm_service import generate_llm_text
except Exception:  # pragma: no cover
    generate_llm_text = None

logger = logging.getLogger(__name__)

VALID_STAGES = {"S1", "S2", "S3", "CY", "EX"}

# -----------------------------------------------------------------------------
# Plain terminology maps
# -----------------------------------------------------------------------------
STAGE_PLAIN = {
    "S1": "first pressurisation phase",
    "S2": "second pressurisation phase",
    "S3": "pressure holding phase",
    "CY": "overall cycle",
    "EX": "pressure release phase",
}

ROOT_CAUSE_PLAIN = {
    "BOILER": "boiler may not be supplying enough steam",
    "BPV": "steam pressure control valve may not be regulating steam properly",
    "COMPETITION": "multiple sterilizers may be demanding steam at the same time",
    "NETWORK": "steam may not be distributed evenly through the main steam line",
    "LOCAL": "this sterilizer may have its own mechanical issue",
}

PATTERN_PLAIN = {
    "A": "the pressure peak did not reach the normal level",
    "B": "the pressure rose too slowly",
    "C": "the pressure fluctuated and was unstable",
    "D": "the pressure release between phases was abnormal",
    "E": "the holding pressure slowly dropped or could not stay stable",
    "F": "more than one sterilizer showed similar pressure behaviour at the same time",
}

# These words should not appear in the visible operator layer.
FORBIDDEN_TECHNICAL_PATTERNS = [
    r"\bMAE\b",
    r"\bRMSE\b",
    r"\bthreshold\b",
    r"\brule\s*ID\b",
    r"\brule\s*number\b",
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
    r"\bcritical limit\b",
    r"\bsafe limit\b",
    r"\bdetected pattern\b",
    r"\bkey metric\b",
    r"\bMAE_[A-Za-z0-9_]+\b",
    r"\bRMSE_[A-Za-z0-9_]+\b",
]

WEAK_CAUSE_PATTERNS = [
    "should be checked using the pressure chart",
    "equipment condition",
    "not available",
    "needs further checking",
    "exact cause still needs",
]

BROKEN_TEXT_PATTERNS = [
    r"\bfrom normal\s+from normal\b",
    r"\bis\s*,\s*which\b",
    r"\bof\s*\.\b",
    r"\bof\s*,\b",
    r"\bnormal is\s*,\b",
    r"\bserious pressure limit of\s*\.\b",
]

# -----------------------------------------------------------------------------
# Environment helpers
# -----------------------------------------------------------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


def _llm_enabled() -> bool:
    return _env_bool("ENABLE_LLM_GENERATION", False) and generate_llm_text is not None

# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------
def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _normalise_stage(value: Any) -> Optional[str]:
    text = _clean_text(value).upper().replace("STAGE", "S").replace(" ", "")
    mapping = {"1": "S1", "2": "S2", "3": "S3", "CYCLE": "CY", "OVERALL": "CY"}
    text = mapping.get(text, text)
    return text if text in VALID_STAGES else None


def _plain_stage(value: Any) -> str:
    stage = _normalise_stage(value)
    return STAGE_PLAIN.get(stage or "", "selected phase")


def _normalise_root_cause(value: Any) -> str:
    text = _clean_text(value).upper()
    if "BOILER" in text:
        return "BOILER"
    if "BPV" in text or "CONTROL VALVE" in text:
        return "BPV"
    if "COMPETITION" in text or "CONCURRENT" in text:
        return "COMPETITION"
    if "NETWORK" in text or "HEADER" in text:
        return "NETWORK"
    if "LOCAL" in text:
        return "LOCAL"
    return text or "UNKNOWN"


def _score_band(score: Any, score_band: Any = None) -> str:
    band = _clean_text(score_band).lower()
    if band in {"excellent", "good", "fair", "poor", "critical"}:
        return band
    value = _safe_float(score)
    if value is None:
        return "unknown"
    if value >= 90:
        return "excellent"
    if value >= 75:
        return "good"
    if value >= 60:
        return "fair"
    if value >= 50:
        return "poor"
    return "critical"


def _split_pattern(pattern: Any) -> List[str]:
    raw = _clean_text(pattern).upper().replace(" ", "")
    return [p for p in re.split(r"[+,/;]", raw) if p in PATTERN_PLAIN]


def _contains_forbidden_terms(text: str) -> bool:
    joined = _clean_text(text)
    for pattern in FORBIDDEN_TECHNICAL_PATTERNS:
        if re.search(pattern, joined, flags=re.IGNORECASE):
            return True
    return False


def _is_broken_text(text: Any) -> bool:
    value = _clean_text(text)
    if not value:
        return True
    for pattern in BROKEN_TEXT_PATTERNS:
        if re.search(pattern, value, flags=re.IGNORECASE):
            return True
    return False


def _sentence_case(text: str) -> str:
    text = _clean_text(text)
    if not text:
        return text
    return text[0].upper() + text[1:]


def _clean_operator_sentence(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""

    replacements = [
        (r"\bbenchmark\b", "normal operating profile"),
        (r"\bdeviation\b", "pressure difference from normal"),
        (r"\bmeasured difference\b", "pressure difference from normal"),
        (r"\bthreshold\b", "limit"),
        (r"\bcritical limit\b", "serious pressure limit"),
        (r"\bsafe limit\b", "normal operating limit"),
        (r"\bmetric value\b", "measured condition"),
        (r"\bcombined_error\b", "pressure difference"),
        (r"\bnormalisation\b|\bnormalization\b", "normal comparison"),
        (r"\bsub-window\b", "phase period"),
        (r"\bStage\s*1\b|\bS1\b", "first pressurisation phase"),
        (r"\bStage\s*2\b|\bS2\b", "second pressurisation phase"),
        (r"\bStage\s*3\b|\bS3\b", "pressure holding phase"),
        (r"\bfirst peak\b", "pressure peak in the first pressurisation phase"),
        (r"\bsecond peak\b", "pressure peak in the second pressurisation phase"),
        (r"\bthird peak\b", "pressure peak in the pressure holding phase"),
        (r"\bMAE_[A-Za-z0-9_]+\b", "pressure difference from normal"),
        (r"\bRMSE_[A-Za-z0-9_]+\b", "pressure instability"),
        (r"\bMAE\b", "pressure difference"),
        (r"\bRMSE\b", "pressure instability"),
        (r"\bPattern\s*[A-F]\b", "pressure behaviour"),
        (r"\b[A-F]\+[A-F](\+[A-F])*\b", "combined pressure behaviour"),
        (r"\bP[1-5]\b", "cause group"),
        (r"\battribution\b", "likely cause"),
        (r"\brule\s*ID\b", "rule reference"),
        (r"\bS[123]-\d{3}\b|\bCY-\d{3}\b|\bEX-\d{3}\b", "rule reference"),
        (r"\bdetected pattern\b", "pressure behaviour"),
        (r"\broot cause\b", "likely cause"),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Remove broken leftovers from rejected technical text.
    text = re.sub(r"\bfrom normal\s+from normal\b", "from normal", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+is\s*,\s*which\s+exceeds\s+[^.]*\.", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"\b0\.\d{3,}\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;:-")

    if text and text[-1] not in ".!?":
        text += "."
    return _sentence_case(text)


def _normalise_priority_text(value: Any, *, severity: Any = None, status: Any = None) -> str:
    text = _clean_text(value)
    lowered = text.lower()
    severity_text = _clean_text(severity).lower()
    status_text = _clean_text(status).lower()

    # RCA state must control priority even if the LLM gives weak wording.
    if status_text == "confirmed" and severity_text == "critical":
        return "Act immediately"
    if status_text == "confirmed" and severity_text in {"warning", "poor"}:
        return "Act as soon as possible"
    if status_text == "candidate":
        return "Verify first"

    if "immediate" in lowered or "urgent" in lowered or severity_text == "critical":
        return "Act immediately"
    if "soon" in lowered or "as soon" in lowered:
        return "Act as soon as possible"
    if "verify" in lowered or "check first" in lowered:
        return "Verify first"
    if "maintenance" in lowered:
        return "Schedule for next maintenance"
    return "Review required"


def _join_names(names: List[str]) -> str:
    clean = []
    for name in names:
        item = _clean_text(name)
        if item and item not in clean:
            clean.append(item)
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    return ", ".join(clean[:-1]) + f", and {clean[-1]}"


def _count_word(count: int) -> str:
    words = {
        0: "no",
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
    }
    return words.get(int(count), str(count))

# -----------------------------------------------------------------------------
# Evidence extraction
# -----------------------------------------------------------------------------
def _peer_names_from_items(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    names: List[str] = []
    for item in items:
        if isinstance(item, dict):
            name = _clean_text(
                item.get("sterilizer_name")
                or item.get("name")
                or item.get("display_name")
                or item.get("field")
            )
        else:
            name = _clean_text(item)
        if name and name not in names:
            names.append(name)
    return names


def _get_evidence_context(scoring_result: Dict[str, Any]) -> Dict[str, Any]:
    metrics = scoring_result.get("metrics", {}) or {}
    evidence = scoring_result.get("evidence_context") or metrics.get("evidence_context") or {}
    return evidence if isinstance(evidence, dict) else {}


def _evidence_summary(evidence: Dict[str, Any]) -> Dict[str, Any]:
    peer = evidence.get("peer_sterilizer_evidence") or {}
    comp = evidence.get("competition_evidence") or {}
    pressure = evidence.get("pressure_time_evidence") or {}

    active_names = _peer_names_from_items(peer.get("active_peers") or [])
    overlapping_names = _peer_names_from_items(peer.get("overlapping_peers") or [])
    ramp_names = _peer_names_from_items(comp.get("concurrent_ramp_peers") or [])
    shared_names = _peer_names_from_items(pressure.get("shared_pressure_event_peers") or [])

    # Prefer active/overlap names because they match the preferred feedback.
    peer_names = active_names or overlapping_names or ramp_names or shared_names

    active_count = int(
        peer.get("active_peer_count")
        or peer.get("stage_active_peer_count")
        or peer.get("selected_stage_peer_count")
        or len(peer_names)
        or 0
    )

    return {
        "available": bool(evidence.get("available")),
        "active_peer_count": active_count,
        "concurrent_ramp_count": int(comp.get("concurrent_ramp_count") or len(ramp_names) or 0),
        "shared_pressure_event_count": int(pressure.get("shared_pressure_event_count") or len(shared_names) or 0),
        "peer_names": peer_names,
        "reason": evidence.get("reason"),
        "summary_lines": evidence.get("summary_lines") or [],
    }

# -----------------------------------------------------------------------------
# Build RCA facts
# -----------------------------------------------------------------------------
def _get_stage_scores(score_breakdown: Dict[str, Any]) -> Dict[str, Optional[float]]:
    return {
        "S1": _safe_float(score_breakdown.get("s1_score")),
        "S2": _safe_float(score_breakdown.get("s2_score")),
        "S3": _safe_float(score_breakdown.get("s3_score")),
    }


def _worst_stage(score_breakdown: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    valid = [(stage, score) for stage, score in _get_stage_scores(score_breakdown).items() if score is not None]
    if not valid:
        return None, None
    return min(valid, key=lambda item: item[1])


def build_plain_language_rca_json(
    *,
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str],
    recommended_actions: Optional[List[str]] = None,
    matched_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    score_breakdown = scoring_result.get("score_breakdown", {}) or {}
    metrics = scoring_result.get("metrics", {}) or {}
    evidence = _get_evidence_context(scoring_result)
    evidence_info = _evidence_summary(evidence)

    worst, worst_score = _worst_stage(score_breakdown)
    selected = _normalise_stage(selected_stage or metrics.get("user_selected_stage"))
    rule_stage = _normalise_stage(primary_rule.get("stage") if primary_rule else None)
    focus_stage = selected or rule_stage or worst

    root_cause = _normalise_root_cause(primary_rule.get("attribution") if primary_rule else metrics.get("attribution"))
    pattern = primary_rule.get("pattern") if primary_rule else metrics.get("pattern")
    severity = primary_rule.get("severity") if primary_rule else scoring_result.get("score_band")
    status = primary_rule.get("status") if primary_rule else "not_available"
    urgency = primary_rule.get("urgency") if primary_rule else None

    action_source = ""
    if primary_rule:
        action_source = _clean_text(primary_rule.get("recommendation_en") or primary_rule.get("recommendation") or "")
    if not action_source and recommended_actions:
        action_source = _clean_text(recommended_actions[0])

    return {
        "language": "EN",
        "cycle_score": scoring_result.get("score"),
        "score_band": scoring_result.get("score_band"),
        "s1_score": score_breakdown.get("s1_score"),
        "s2_score": score_breakdown.get("s2_score"),
        "s3_score": score_breakdown.get("s3_score"),
        "worst_stage": worst,
        "worst_stage_score": worst_score,
        "focus_stage": focus_stage,
        "focus_stage_plain": _plain_stage(focus_stage),
        "root_cause": root_cause,
        "root_cause_plain": ROOT_CAUSE_PLAIN.get(root_cause, "the equipment condition needs checking"),
        "pattern": pattern,
        "pattern_plain": [PATTERN_PLAIN[p] for p in _split_pattern(pattern)],
        "severity": severity,
        "rca_status": status,
        "urgency": urgency,
        "urgency_plain": _normalise_priority_text(urgency, severity=severity, status=status),
        "peer_active_count": evidence_info["active_peer_count"],
        "peer_names": evidence_info["peer_names"],
        "concurrent_ramp_count": evidence_info["concurrent_ramp_count"],
        "shared_pressure_event_count": evidence_info["shared_pressure_event_count"],
        "evidence_available": evidence_info["available"],
        "evidence_reason": evidence_info["reason"],
        "evidence_summary_lines": evidence_info["summary_lines"],
        "recommended_action_source": action_source,
        "matched_rule_count": len(matched_rules or []),
    }

# -----------------------------------------------------------------------------
# Preferred KB-grounded content builder
# -----------------------------------------------------------------------------
def _peer_phrase(rca_json: Dict[str, Any]) -> str:
    count = int(rca_json.get("peer_active_count") or 0)
    names = rca_json.get("peer_names") or []
    if count <= 0:
        return ""
    if names:
        return f"{_count_word(count).capitalize()} other sterilizer(s) ({_join_names(names)})"
    return f"{_count_word(count).capitalize()} other sterilizer(s)"


def _build_what_happened(rca_json: Dict[str, Any]) -> str:
    stage = rca_json.get("focus_stage_plain") or "selected phase"
    root = rca_json.get("root_cause")
    peer = _peer_phrase(rca_json)
    patterns = rca_json.get("pattern_plain") or []

    if root == "BOILER":
        if peer:
            return (
                f"During the {stage}, the pressure behaviour was affected while {peer.lower()} "
                "were also active, increasing total steam demand. This indicates a shared steam supply issue among the sterilizers."
            )
        return (
            f"During the {stage}, the pressure did not build up as strongly as expected. "
            "This indicates that the sterilizer may not have received enough steam during this part of the cycle."
        )

    if root == "COMPETITION":
        if peer:
            return (
                f"During the {stage}, this sterilizer needed steam while {peer.lower()} were also active. "
                "This indicates that steam demand may have overlapped between sterilizers."
            )
        return f"During the {stage}, the pressure behaviour suggests possible overlap with another sterilizer demanding steam."

    if root == "BPV":
        return (
            f"During the {stage}, the pressure did not follow the expected operating profile. "
            "This suggests the steam delivery or pressure control response may not be stable."
        )

    if root == "NETWORK":
        if peer:
            return (
                f"During the {stage}, more than one sterilizer showed pressure behaviour that needs attention. "
                f"{peer} were active, so the shared steam line may be affecting steam distribution."
            )
        return f"During the {stage}, the pressure behaviour suggests uneven steam distribution in the shared steam line."

    if root == "LOCAL":
        return (
            f"During the {stage}, this sterilizer showed abnormal pressure behaviour while the issue appears local to this unit. "
            "The affected part should be checked before the same problem repeats."
        )

    if patterns:
        return f"During the {stage}, {patterns[0]}. The cycle should be checked before the next operation."

    return f"During the {stage}, the pressure behaviour needs attention and should be checked before the next cycle."


def _build_most_likely_cause(rca_json: Dict[str, Any]) -> str:
    root = rca_json.get("root_cause")
    peer = _peer_phrase(rca_json)

    if root == "BOILER":
        text = "The most likely cause is that the boiler may not be supplying enough steam during this period."
        if peer:
            text += f" {peer} were also active, which can increase total steam demand."
            text += " This suggests a shared steam supply issue among the sterilizers."
        else:
            text += " This can happen when boiler output, feed water supply, combustion, or steam trap condition is not stable enough for the cycle demand."
        return text

    if root == "COMPETITION":
        text = "The most likely cause is that multiple sterilizers may be demanding steam at the same time."
        if peer:
            text += f" {peer} were active in the same period, so the available steam may have been shared between units."
        return text

    if root == "BPV":
        return (
            "The most likely cause is that the steam pressure control valve may not be regulating or delivering steam properly. "
            "A slow valve response, incorrect setting, actuator issue, or unstable air supply can reduce steam delivery during the affected phase."
        )

    if root == "NETWORK":
        text = "The most likely cause is uneven steam distribution through the main steam header pipe."
        if peer:
            text += f" {peer} were active, so the shared steam line, branch valves, and steam traps should be checked."
        return text

    if root == "LOCAL":
        return (
            "The most likely cause is a local issue on this sterilizer. "
            "Possible areas include the inlet steam valve, steam trap, door seal, or local control valve response."
        )

    return "The most likely cause is not fully confirmed yet, so the related pressure chart and equipment condition should be checked."



def _split_kb_recommendation_sentences(text: Any) -> List[str]:
    """
    Split the rule-focused Recommendation_EN from the knowledge base into
    candidate action sentences.

    Important: this function does not invent maintenance actions. It only keeps
    clauses that are already present in the KB recommendation text.
    """
    source = _clean_text(text)
    if not source:
        return []

    # Remove target metric sentence because it is not an operator action.
    source = re.sub(r"(?i)\bTarget\s*:\s*[^.]*\.?", " ", source)
    source = re.sub(r"(?i)\bSasaran\s*:\s*[^.]*\.?", " ", source)

    # The part before an em dash is usually symptom/context, not action.
    # Keep both sides only if the left side already begins with an action verb.
    chunks: List[str] = []
    for part in re.split(r"[\n\r]+", source):
        part = part.strip()
        if not part:
            continue
        if "—" in part or "-" in part:
            dash_parts = [x.strip() for x in re.split(r"\s+[—–-]\s+", part) if x.strip()]
            chunks.extend(dash_parts)
        else:
            chunks.append(part)

    # Split into shorter sentence-level clauses.
    candidates: List[str] = []
    for chunk in chunks:
        parts = re.split(r"(?<=[.!?])\s+", chunk)
        for item in parts:
            item = item.strip(" .;:-")
            if item:
                candidates.append(item)

    return candidates


def _is_kb_action_clause(sentence: str) -> bool:
    """Return True only for clauses that look like actions from Recommendation_EN."""
    text = _clean_text(sentence).lower()
    if not text:
        return False

    action_verbs = (
        "check", "inspect", "monitor", "confirm", "verify", "stagger",
        "reduce", "increase", "recalibrate", "calibrate", "retune", "re-tune",
        "tune", "adjust", "implement", "enforce", "avoid", "limit", "compare",
        "clean", "drain", "replace", "repair", "maintain", "schedule"
    )

    # Direct action sentence.
    if text.startswith(action_verbs):
        return True

    # Sentence containing an action verb after context.
    return bool(re.search(r"(?i)\b(check|inspect|monitor|confirm|verify|stagger|reduce|increase|recalibrate|calibrate|re-tune|retune|tune|adjust|implement|enforce|avoid|limit|compare|clean|drain|replace|repair|maintain|schedule)\b", sentence))


def _split_compound_kb_action(sentence: str, rca_json: Dict[str, Any]) -> List[str]:
    """
    Split one KB action sentence into operator-friendly points, but only using
    action details that exist in that same sentence.
    """
    clean = _clean_operator_sentence(sentence)
    if not clean:
        return []

    # Remove non-action lead-ins before the first action verb.
    match = re.search(
        r"(?i)\b(check|inspect|monitor|confirm|verify|stagger|reduce|increase|recalibrate|calibrate|re-tune|retune|tune|adjust|implement|enforce|avoid|limit|compare|clean|drain|replace|repair|maintain|schedule)\b",
        clean,
    )
    if match and match.start() > 0:
        clean = clean[match.start():].strip()

    stage = rca_json.get("focus_stage_plain") or "selected phase"
    clean = re.sub(r"(?i)\bduring the second ramp\b", f"during the {stage}", clean)
    clean = re.sub(r"(?i)\bduring second ramp\b", f"during the {stage}", clean)
    clean = re.sub(r"(?i)\bduring S1 window\b", "during the first pressurisation phase", clean)
    clean = re.sub(r"(?i)\bduring S2 window\b", "during the second pressurisation phase", clean)
    clean = re.sub(r"(?i)\bduring S3 window\b", "during the pressure holding phase", clean)
    clean = re.sub(r"(?i)\bduring first pressurisation phase window\b", "during the first pressurisation phase", clean)
    clean = re.sub(r"(?i)\bduring second pressurisation phase window\b", "during the second pressurisation phase", clean)
    clean = re.sub(r"(?i)\bduring pressure holding phase window\b", "during the pressure holding phase", clean)

    # Split only simple "Check A and B" structures into separate action points.
    # Do not create new content; each item must come from the original clause.
    if clean.lower().startswith("check ") and " and " in clean:
        body = re.sub(r"(?i)^check\s+", "", clean).strip(" .")
        parts = [part.strip(" .") for part in re.split(r"\s*,\s*|\s+and\s+", body) if part.strip(" .")]
        if 2 <= len(parts) <= 4:
            return [_sentence_case(f"Check {part}.") for part in parts]

    return [_sentence_case(clean)]


def _extract_kb_action_items(rca_json: Dict[str, Any]) -> List[str]:
    """
    Build WHAT_TO_DO only from the rule-focused knowledge-base recommendation.

    Source priority:
    1. primary_rule.recommendation_en captured as recommended_action_source
    2. feedback_generator next_actions passed into recommended_actions

    No root-cause hardcoded action list is used here.
    """
    source = _clean_text(rca_json.get("recommended_action_source"))
    if not source:
        return []

    actions: List[str] = []
    for sentence in _split_kb_recommendation_sentences(source):
        if not _is_kb_action_clause(sentence):
            continue
        for action in _split_compound_kb_action(sentence, rca_json):
            action = _clean_operator_sentence(action)
            if action and action not in actions:
                actions.append(action)

    return actions[:4]

def _build_action_items(rca_json: Dict[str, Any]) -> List[str]:
    """
    WHAT_TO_DO must be grounded in the rule-focused recommendation from the
    knowledge base. Do not hardcode boiler/BPV/network/local action lists here.
    """
    actions = _extract_kb_action_items(rca_json)
    if actions:
        return actions

    # Transparent fallback when the matched RCA rule has no recommendation text.
    return ["No knowledge-base recommendation was returned for this RCA rule."]

def _build_priority(rca_json: Dict[str, Any]) -> str:
    return _normalise_priority_text(
        rca_json.get("urgency_plain"),
        severity=rca_json.get("severity"),
        status=rca_json.get("rca_status"),
    )


def _make_payload(
    *,
    what: str,
    cause: str,
    actions: List[str],
    priority: str,
    source: str,
    generation: Optional[Dict[str, Any]] = None,
    rca_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    safe_actions = [_clean_operator_sentence(item) for item in actions if _clean_operator_sentence(item)]
    safe_actions = safe_actions[:4]

    what = _clean_operator_sentence(what)
    cause = _clean_operator_sentence(cause)
    priority = _normalise_priority_text(
        priority,
        severity=(rca_json or {}).get("severity"),
        status=(rca_json or {}).get("rca_status"),
    )

    source_label = "AI plain-language output" if source.startswith("sheet10_llm") else "Template fallback output"

    return {
        "language": "EN",
        "source": source,
        "source_label": source_label,
        "what_happened": what,
        "most_likely_cause": cause,
        "what_to_do": safe_actions,
        "priority": priority,
        "sections": [
            {"key": "what_happened", "title": "What happened", "body": what},
            {"key": "most_likely_cause", "title": "Most likely cause", "body": cause},
            {"key": "what_to_do", "title": "What to do", "items": safe_actions},
            {"key": "priority", "title": "Priority", "body": priority},
        ],
        "paragraphs": [what, cause, " ".join([f"• {item}" for item in safe_actions]), priority],
        "generation": generation or {},
        "evidence_used": _build_evidence_used(rca_json or {}),
        "knowledge_base_basis": _build_knowledge_basis(rca_json or {}),
    }


def build_template_plain_language_feedback(rca_json: Dict[str, Any]) -> Dict[str, Any]:
    return _make_payload(
        what=_build_what_happened(rca_json),
        cause=_build_most_likely_cause(rca_json),
        actions=_build_action_items(rca_json),
        priority=_build_priority(rca_json),
        source="sheet10_template_fallback",
        generation={
            "enabled": _llm_enabled(),
            "attempted": False,
            "success": False,
            "fallback_used": True,
            "reason": "Template generated from RCA facts.",
        },
        rca_json=rca_json,
    )

# -----------------------------------------------------------------------------
# AI generation and repair
# -----------------------------------------------------------------------------
def _build_llm_system_prompt() -> str:
    return """
You are a senior palm oil mill engineer explaining sterilizer RCA feedback to an operator.
Use simple operator language. Do not expose calculations, rule codes, or model details.
Return the answer using exactly these headings:

WHAT_HAPPENED:
MOST_LIKELY_CAUSE:
WHAT_TO_DO:
PRIORITY:

Rules:
- Most likely cause must be specific, not vague.
- What to do must rewrite ONLY the provided knowledge-base recommendation text into practical action items.
- Do not add actions that are not stated or directly implied by the knowledge-base recommendation.
- If other sterilizers are active, mention them in Most likely cause.
- Do not use: MAE, RMSE, threshold, rule ID, Pattern A, benchmark, deviation, metric value, P1, P2, P3, S1, S2, S3.
- Use phase names such as first pressurisation phase, second pressurisation phase, pressure holding phase.
- Priority must be one phrase only: Act immediately, Act as soon as possible, Verify first, or Schedule for next maintenance.
""".strip()


def _build_llm_user_prompt(rca_json: Dict[str, Any], preferred: Dict[str, Any], retry_note: str = "") -> str:
    facts = {
        "affected_phase": rca_json.get("focus_stage_plain"),
        "likely_cause": rca_json.get("root_cause_plain"),
        "status": rca_json.get("rca_status"),
        "severity": rca_json.get("severity"),
        "peer_active_count": rca_json.get("peer_active_count"),
        "peer_names": rca_json.get("peer_names"),
        "pressure_behaviour": rca_json.get("pattern_plain"),
        "knowledge_base_recommendation_text": rca_json.get("recommended_action_source"),
        "preferred_meaning": {
            "what_happened": preferred.get("what_happened"),
            "most_likely_cause": preferred.get("most_likely_cause"),
            "what_to_do": preferred.get("what_to_do"),
            "priority": preferred.get("priority"),
        },
    }

    return f"""
Use only the facts below. Keep the meaning close to the preferred wording.

{json.dumps(facts, ensure_ascii=False, indent=2)}

{retry_note}

Return only the four required headings and their content.
""".strip()


def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            candidate = json.loads(raw[start:end + 1])
            return candidate if isinstance(candidate, dict) else None
        except Exception:
            return None
    return None


def _normalise_action_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        text = _clean_text(value)
        # Split numbered/bulleted lines first.
        items = [line.strip() for line in re.split(r"\n+", text) if line.strip()]
        if len(items) <= 1 and ";" in text:
            items = [part.strip() for part in text.split(";") if part.strip()]
    clean = []
    for item in items:
        line = _clean_text(item)
        line = re.sub(r"^[-•*]\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        line = _clean_operator_sentence(line)
        if line and line not in clean:
            clean.append(line)
    return clean


def _parse_llm_output(text: Any) -> Optional[Dict[str, Any]]:
    raw = _clean_text(text)
    if not raw:
        return None

    json_payload = _extract_json_object(raw)
    if json_payload is not None:
        what = json_payload.get("what_happened") or json_payload.get("whatHappened") or ""
        cause = json_payload.get("most_likely_cause") or json_payload.get("mostLikelyCause") or json_payload.get("cause") or ""
        actions = json_payload.get("what_to_do") or json_payload.get("whatToDo") or json_payload.get("actions") or []
        priority = json_payload.get("priority") or ""
        return {
            "what_happened": _clean_operator_sentence(what),
            "most_likely_cause": _clean_operator_sentence(cause),
            "what_to_do": _normalise_action_list(actions),
            "priority": _normalise_priority_text(priority),
        }

    cleaned = raw.replace("**", "")
    replacements = {
        "【What happened】": "WHAT_HAPPENED:",
        "【Most likely cause】": "MOST_LIKELY_CAUSE:",
        "【What to do】": "WHAT_TO_DO:",
        "Priority:": "PRIORITY:",
        "What happened:": "WHAT_HAPPENED:",
        "Most likely cause:": "MOST_LIKELY_CAUSE:",
        "What to do:": "WHAT_TO_DO:",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"(?i)^#+\s*", "", cleaned, flags=re.MULTILINE)

    def section_between(start_patterns: List[str], end_patterns: List[str]) -> str:
        starts = "|".join(re.escape(p) for p in start_patterns)
        ends = "|".join(re.escape(p) for p in end_patterns)
        pattern = rf"(?:{starts})\s*(.*?)"
        pattern += rf"(?=(?:{ends})|$)" if ends else r"$"
        match = re.search(pattern, cleaned, flags=re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    what = section_between(["WHAT_HAPPENED:"], ["MOST_LIKELY_CAUSE:", "WHAT_TO_DO:", "PRIORITY:"])
    cause = section_between(["MOST_LIKELY_CAUSE:"], ["WHAT_TO_DO:", "PRIORITY:"])
    todo = section_between(["WHAT_TO_DO:"], ["PRIORITY:"])
    priority = section_between(["PRIORITY:"], [])

    if not any([what, cause, todo, priority]):
        paras = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
        if len(paras) >= 2:
            what = paras[0]
            cause = paras[1]
            todo = "\n".join(paras[2:])
            priority = ""
        else:
            return None

    return {
        "what_happened": _clean_operator_sentence(what),
        "most_likely_cause": _clean_operator_sentence(cause),
        "what_to_do": _normalise_action_list(todo),
        "priority": _normalise_priority_text(priority),
    }


def _section_is_weak(section_key: str, value: Any) -> bool:
    text = _clean_text(value)
    lowered = text.lower()

    if _is_broken_text(text):
        return True
    if _contains_forbidden_terms(text):
        return True

    if section_key == "what_happened":
        if len(text) < 45:
            return True
        if "recommended" in lowered or "next step" in lowered or "rule" in lowered:
            return True
        # Long technical mixed paragraphs should be replaced with preferred content.
        if len(text) > 520:
            return True

    if section_key == "most_likely_cause":
        if len(text) < 65:
            return True
        if any(p in lowered for p in WEAK_CAUSE_PATTERNS):
            return True
        if "likely cause" not in lowered and "most likely cause" not in lowered and "boiler" not in lowered and "valve" not in lowered and "steam" not in lowered:
            return True

    if section_key == "what_to_do":
        actions = value if isinstance(value, list) else _normalise_action_list(value)
        if len(actions) < 2:
            return True
        joined = " ".join(actions).lower()
        if _contains_forbidden_terms(joined) or _is_broken_text(joined):
            return True
        if "pressure peak" in joined or "below normal reference" in joined or "second ramp" in joined:
            return True

    if section_key == "priority":
        if text.lower() in {"review required", "schedule for next maintenance"}:
            return True

    return False


def _merge_ai_with_preferred(
    ai_payload: Optional[Dict[str, Any]],
    preferred_payload: Dict[str, Any],
    rca_json: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Keep AI output only where it is clear and operator-safe.
    Replace weak/broken sections with the preferred KB-grounded sections.
    This prevents the bad screenshot state while still using the LLM when it works.
    """
    preferred = {
        "what_happened": preferred_payload["what_happened"],
        "most_likely_cause": preferred_payload["most_likely_cause"],
        "what_to_do": preferred_payload["what_to_do"],
        "priority": preferred_payload["priority"],
    }

    if not ai_payload:
        return preferred, {
            "post_processed": False,
            "sections_replaced": ["all"],
            "reason": "AI output was empty or could not be parsed.",
        }

    final = {
        "what_happened": ai_payload.get("what_happened") or preferred["what_happened"],
        "most_likely_cause": ai_payload.get("most_likely_cause") or preferred["most_likely_cause"],
        "what_to_do": ai_payload.get("what_to_do") or preferred["what_to_do"],
        "priority": ai_payload.get("priority") or preferred["priority"],
    }

    replaced: List[str] = []
    for key in ["what_happened", "most_likely_cause", "what_to_do", "priority"]:
        if _section_is_weak(key, final[key]):
            final[key] = preferred[key]
            replaced.append(key)

    # WHAT_TO_DO must stay grounded in the rule-focused KB recommendation.
    # The LLM may improve wording, but it must not invent maintenance actions.
    # Therefore we prefer the parsed KB action list whenever the KB returned one.
    if preferred.get("what_to_do"):
        if final.get("what_to_do") != preferred.get("what_to_do"):
            final["what_to_do"] = preferred["what_to_do"]
            if "what_to_do" not in replaced:
                replaced.append("what_to_do")

    # Strong RCA state overrides LLM priority.
    final["priority"] = _normalise_priority_text(
        final["priority"],
        severity=rca_json.get("severity"),
        status=rca_json.get("rca_status"),
    )

    return final, {
        "post_processed": bool(replaced),
        "sections_replaced": replaced,
        "reason": "Weak, vague, technical, or broken AI sections were replaced using KB-grounded Sheet 10 wording." if replaced else "AI sections accepted.",
    }


def _generate_ai_feedback(rca_json: Dict[str, Any], preferred_payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not _llm_enabled():
        return None, {
            "enabled": False,
            "attempted": False,
            "success": False,
            "fallback_used": True,
            "error": "ENABLE_LLM_GENERATION is false or LLM service is unavailable.",
        }

    attempts = max(1, _env_int("PLAIN_LANGUAGE_LLM_RETRIES", 5))
    max_new_tokens = _env_int("PLAIN_LANGUAGE_MAX_NEW_TOKENS", 650)
    last_error = None
    raw_outputs: List[str] = []

    for attempt in range(1, attempts + 1):
        retry_note = ""
        if attempt > 1:
            retry_note = (
                "Previous output was not clear enough. Rewrite it with specific cause, practical actions, "
                "and no technical terms."
            )
        try:
            raw = generate_llm_text(
                _build_llm_user_prompt(rca_json, preferred_payload, retry_note=retry_note),
                max_new_tokens=max_new_tokens,
                system_prompt=_build_llm_system_prompt(),
            )
            if raw:
                raw_outputs.append(str(raw))
                parsed = _parse_llm_output(raw)
                if parsed:
                    return parsed, {
                        "enabled": True,
                        "attempted": True,
                        "success": True,
                        "fallback_used": False,
                        "attempts": attempt,
                        "raw_output_preview": _clean_text(raw)[:800],
                    }
                last_error = "AI output returned but could not be parsed into required sections."
            else:
                last_error = "AI returned empty output."
        except Exception as exc:  # generate_llm_text normally catches exceptions, but keep safe.
            logger.exception("Plain-language LLM generation failed: %s", exc)
            last_error = str(exc)

    # If the LLM returned any text, we still treat AI as used and let the
    # post-processor convert it to the preferred safe result.
    if raw_outputs:
        return None, {
            "enabled": True,
            "attempted": True,
            "success": True,
            "fallback_used": False,
            "attempts": attempts,
            "error": last_error,
            "raw_output_preview": _clean_text(raw_outputs[-1])[:800],
            "post_processor_used_because": "AI returned text but it was not safely parseable.",
        }

    return None, {
        "enabled": True,
        "attempted": True,
        "success": False,
        "fallback_used": True,
        "attempts": attempts,
        "error": last_error or "AI did not return usable output.",
    }

# -----------------------------------------------------------------------------
# Optional details for frontend display
# -----------------------------------------------------------------------------
def _build_evidence_used(rca_json: Dict[str, Any]) -> List[str]:
    items: List[str] = []
    count = int(rca_json.get("peer_active_count") or 0)
    names = rca_json.get("peer_names") or []
    if count > 0:
        if names:
            items.append(f"{_count_word(count).capitalize()} other sterilizer(s) were active: {_join_names(names)}.")
        else:
            items.append(f"{_count_word(count).capitalize()} other sterilizer(s) were active during the affected period.")
    shared = int(rca_json.get("shared_pressure_event_count") or 0)
    if shared > 0:
        items.append(f"{shared} peer sterilizer(s) also showed notable pressure movement.")
    if not items:
        items.append("RCA rule result and selected cycle pressure behaviour were used.")
    return items


def _build_knowledge_basis(rca_json: Dict[str, Any]) -> List[str]:
    source = _clean_text(rca_json.get("recommended_action_source"))
    rule_text = []
    if rca_json.get("root_cause"):
        rule_text.append(f"Matched RCA rule root cause: {rca_json.get('root_cause')}")
    if rca_json.get("focus_stage_plain"):
        rule_text.append(f"Affected phase: {rca_json.get('focus_stage_plain')}")
    if source:
        rule_text.append(f"Knowledge-base recommendation used: {_clean_operator_sentence(source)}")
    else:
        rule_text.append("No rule-focused knowledge-base recommendation text was returned.")
    return rule_text

# -----------------------------------------------------------------------------
# Public entry point used by feedback_generator.py
# -----------------------------------------------------------------------------
def build_plain_language_feedback(
    *,
    scoring_result: Dict[str, Any],
    primary_rule: Optional[Dict[str, Any]],
    selected_stage: Optional[str],
    recommended_actions: Optional[List[str]] = None,
    matched_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build operator-facing Sheet 10 RCA feedback.

    This function is called by feedback_generator.build_human_feedback().
    It returns a payload that the frontend reads from:
        human_feedback.plain_language_feedback
        human_feedback.plain_language
        human_feedback.operator_explanation
    """
    rca_json = build_plain_language_rca_json(
        scoring_result=scoring_result,
        primary_rule=primary_rule,
        selected_stage=selected_stage,
        recommended_actions=recommended_actions or [],
        matched_rules=matched_rules or [],
    )

    preferred_payload = build_template_plain_language_feedback(rca_json)

    ai_payload, generation = _generate_ai_feedback(rca_json, preferred_payload)

    # If AI cannot work at all, use template fallback.
    if not generation.get("success") and generation.get("fallback_used"):
        fallback = build_template_plain_language_feedback(rca_json)
        fallback["generation"] = generation
        return fallback

    final_sections, repair = _merge_ai_with_preferred(ai_payload, preferred_payload, rca_json)
    generation = {
        **generation,
        **repair,
        "final_output_strategy": "AI-first with KB-grounded section repair",
    }

    return _make_payload(
        what=final_sections["what_happened"],
        cause=final_sections["most_likely_cause"],
        actions=final_sections["what_to_do"],
        priority=final_sections["priority"],
        source="sheet10_llm_post_processed",
        generation=generation,
        rca_json=rca_json,
    )
