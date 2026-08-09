from typing import Any, Dict, Optional

from app.rag.retriever import retrieve_rca_chunks
from app.rag.rule_evaluator import evaluate_rules
from app.rag.context_augmenter import augment_context
from app.rag.feedback_generator import generate_rule_based_feedback
from app.services.evidence_service import collect_peer_competition_evidence


# -----------------------------------------------------------------------------
# RCA pipeline with peer + competition evidence
# -----------------------------------------------------------------------------
# Flow:
# scoring_result -> evidence collection -> retrieval -> rule evaluation -> feedback
# -----------------------------------------------------------------------------


def select_primary_metric(metrics: Dict[str, Any]) -> Optional[str]:
    """
    Choose the metric most likely responsible for the affected stage.
    """

    affected_stage = metrics.get("affected_stage") or metrics.get("user_selected_stage")

    stage_metric_candidates = {
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
            "cycle_score",
        ],
    }

    candidates = stage_metric_candidates.get(str(affected_stage).upper(), [])

    best_metric = None
    best_value = None

    for metric in candidates:
        value = metrics.get(metric)
        try:
            value = float(value)
        except Exception:
            continue

        if metric == "cycle_score":
            continue

        if best_value is None or value > best_value:
            best_metric = metric
            best_value = value

    return best_metric


def run_rca_feedback_pipeline(
    scoring_result: Dict[str, Any],
    peer_confirmation_available: bool = False,
) -> Dict[str, Any]:
    metrics = dict(scoring_result.get("metrics", {}) or {})

    affected_stage = metrics.get("affected_stage") or metrics.get("user_selected_stage")
    pattern = metrics.get("pattern")
    primary_metric = select_primary_metric(metrics)

    # 1) Collect peer + competition evidence before rule evaluation.
    evidence_context = collect_peer_competition_evidence(scoring_result)

    # Add evidence flags into metrics so retrieval/evaluation/feedback can use them.
    evidence_flags = evidence_context.get("confirmation_flags") or {}
    metrics.update(evidence_flags)
    metrics["evidence_context"] = evidence_context

    scoring_result_with_evidence = {
        **scoring_result,
        "metrics": metrics,
        "evidence_context": evidence_context,
    }

    # 2) Retrieve the most relevant RCA chunks.
    retrieved_chunks = retrieve_rca_chunks(
        affected_stage=affected_stage,
        metric=primary_metric,
        pattern=pattern,
        scoring_context={
            **metrics,
            **scoring_result.get("score_breakdown", {}),
            "cycle_score": scoring_result.get("score"),
            "score_band": scoring_result.get("score_band"),
        },
    )

    # 3) Evaluate rules using metric thresholds + extra evidence.
    evaluated_rules = evaluate_rules(
        chunks=retrieved_chunks,
        metrics=metrics,
        peer_confirmation_available=peer_confirmation_available,
        evidence=evidence_context,
    )

    # 4) Build augmented context and human feedback.
    augmented_context = augment_context(
        scoring_result=scoring_result_with_evidence,
        evaluated_rules=evaluated_rules,
    )

    feedback = generate_rule_based_feedback(
        scoring_result=scoring_result_with_evidence,
        evaluated_rules=evaluated_rules,
        augmented_context=augmented_context,
    )

    return {
        "affected_stage": affected_stage,
        "primary_metric": primary_metric,
        "pattern": pattern,
        "retrieved_chunk_count": len(retrieved_chunks),
        "evidence_context": evidence_context,
        "feedback": feedback,
    }
