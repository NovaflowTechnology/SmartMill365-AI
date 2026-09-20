from typing import Any, Dict, List


def format_score_summary(scoring_result: Dict[str, Any]) -> str:
    score_breakdown = scoring_result.get("score_breakdown", {})
    metrics = scoring_result.get("metrics", {})

    lines = [
        "Scoring Summary",
        f"Cycle score: {scoring_result.get('score')}",
        f"Classification: {scoring_result.get('classification')}",
        f"Score band: {scoring_result.get('score_band')}",
        f"S1 score: {score_breakdown.get('s1_score')}",
        f"S2 score: {score_breakdown.get('s2_score')}",
        f"S3 score: {score_breakdown.get('s3_score')}",
        f"Affected stage: {metrics.get('affected_stage')}",
        f"Pattern: {metrics.get('pattern')}",
    ]

    for key in [
        "MAE_s1_peak",
        "MAE_s1_ramp",
        "MAE_s1_release",
        "MAE_s2_peak",
        "MAE_s2_ramp",
        "MAE_s2_release",
        "MAE_s3_ramp",
        "MAE_s3_hold",
        "RMSE_s3_hold",
        "combined_s3_hold",
        "osc_ratio_s3_hold",
    ]:
        if key in metrics:
            lines.append(f"{key}: {metrics.get(key)}")

    return "\n".join(lines)


def format_rule_context(evaluated_rules: List[Dict[str, Any]], max_rules: int = 3) -> str:
    lines = ["Retrieved Analysis Rule Context"]

    for idx, rule in enumerate(evaluated_rules[:max_rules], start=1):
        lines.extend([
            "",
            f"[Rule {idx}]",
            f"Rule ID: {rule.get('rule_id')}",
            f"Status: {rule.get('status')}",
            f"Severity: {rule.get('severity')}",
            f"Stage: {rule.get('stage')}",
            f"Attribution: {rule.get('attribution')}",
            f"Pattern: {rule.get('pattern')}",
            f"Metric: {rule.get('metric_name')} = {rule.get('metric_value')}",
            f"Warning threshold: {rule.get('warn_low')}",
            f"Critical threshold: {rule.get('critical_value')}",
            f"Urgency: {rule.get('urgency')}",
            f"Target metric: {rule.get('target_metric')}",
            f"Reason: {rule.get('reason')}",
            f"Recommendation EN: {rule.get('recommendation_en')}",
        ])

    return "\n".join(lines)


def augment_context(
    scoring_result: Dict[str, Any],
    evaluated_rules: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Augmentation step.

    This builds the grounded context package used by the feedback generator:
    scoring summary + retrieved/evaluated RCA rules.
    """

    score_summary = format_score_summary(scoring_result)
    rule_context = format_rule_context(evaluated_rules)

    augmented_text = "\n\n".join([
        score_summary,
        rule_context,
    ])

    return {
        "score_summary": score_summary,
        "rule_context": rule_context,
        "augmented_text": augmented_text,
        "used_rule_ids": [r.get("rule_id") for r in evaluated_rules[:3]],
        "used_rec_keys": [r.get("rec_key") for r in evaluated_rules[:3]],
    }
