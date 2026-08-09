import numpy as np
import pandas as pd
from scipy.signal import find_peaks


# V4 scoring bands from SmartMill365 RCA V4:
# Excellent >= 90, Good 75-89, Fair 60-74, Poor < 60, Critical < 50
WARNING_SCORE_THRESHOLD = 75.0
POOR_SCORE_THRESHOLD = 60.0
CRITICAL_SCORE_THRESHOLD = 50.0

K_EXPONENTIAL = 2.2

STAGE_WEIGHTS = {
    "s1_score": 0.20,
    "s2_score": 0.30,
    "s3_score": 0.50,
}

WARN_COMBINED_ERROR = 0.13
CRITICAL_COMBINED_ERROR = 0.23
OSC_WARN_RATIO = 1.30
OSC_CRIT_RATIO = 1.50


# ---------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------
def safe_float(value, default=0.0):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def clip_score(value):
    return float(np.clip(float(value), 0.0, 100.0))


def exponential_score(error, sensitivity=K_EXPONENTIAL):
    """
    V4 exponential conversion:
        score = 100 * exp(-2.2 * combined_error)

    The input error should already be on the 0-1 normalised scale.
    """
    error = max(float(error), 0.0)
    return clip_score(100.0 * np.exp(-sensitivity * error))


def minmax_normalise_full_cycle(y):
    """
    V4 full-cycle normalisation:
        P_norm = (P - min(P_full)) / (max(P_full) - min(P_full))

    This is full-cycle normalisation, not per-stage normalisation.
    """
    y = np.asarray(y, dtype=float)

    if len(y) < 2:
        raise ValueError("Signal too short for full-cycle normalisation.")

    y_min = float(np.nanmin(y))
    y_max = float(np.nanmax(y))
    y_range = max(y_max - y_min, 1e-9)

    return (y - y_min) / y_range, {
        "min": y_min,
        "max": y_max,
        "range": y_range,
    }


def resample_to_n_points(y, n_points):
    y = np.asarray(y, dtype=float)

    if len(y) < 2:
        raise ValueError("Signal too short to resample.")

    x_old = np.linspace(0, 1, len(y))
    x_new = np.linspace(0, 1, int(n_points))

    return np.interp(x_new, x_old, y)


def normalise_cycle_to_benchmark_length(cycle_df, benchmark_curve, value_col="smooth"):
    """
    Returns:
    - actual curve resampled to benchmark length
    - benchmark curve as array
    - full-cycle normalised actual curve
    - full-cycle normalised benchmark curve
    """

    benchmark_curve = np.asarray(benchmark_curve, dtype=float)
    n_points = len(benchmark_curve)

    actual_raw = cycle_df[value_col].to_numpy(dtype=float)
    actual_resampled = resample_to_n_points(actual_raw, n_points)

    actual_norm, actual_norm_info = minmax_normalise_full_cycle(actual_resampled)
    bench_norm, bench_norm_info = minmax_normalise_full_cycle(benchmark_curve)

    return actual_resampled, benchmark_curve, actual_norm, bench_norm, {
        "actual_normalisation": actual_norm_info,
        "benchmark_normalisation": bench_norm_info,
        "normalised_points": int(n_points),
    }


def window_slice(y, start_idx, end_idx):
    y = np.asarray(y, dtype=float)
    n = len(y)

    s = int(np.clip(start_idx, 0, max(n - 1, 0)))
    e = int(np.clip(end_idx, s, max(n - 1, 0)))

    return y[s:e + 1]


def mae(values_a, values_b):
    a = np.asarray(values_a, dtype=float)
    b = np.asarray(values_b, dtype=float)

    if len(a) == 0 or len(b) == 0:
        return 0.0

    return float(np.mean(np.abs(a - b)))


def rmse(values_a, values_b):
    a = np.asarray(values_a, dtype=float)
    b = np.asarray(values_b, dtype=float)

    if len(a) == 0 or len(b) == 0:
        return 0.0

    return float(np.sqrt(np.mean((a - b) ** 2)))


def mean_error(values_a, values_b):
    a = np.asarray(values_a, dtype=float)
    b = np.asarray(values_b, dtype=float)

    if len(a) == 0 or len(b) == 0:
        return 0.0

    return float(np.mean(a - b))


def combined_mae_rmse(values_a, values_b):
    """
    V4 combined error:
        combined_error = 0.5 * MAE + 0.5 * RMSE
    """

    mae_value = mae(values_a, values_b)
    rmse_value = rmse(values_a, values_b)

    return {
        "mae": mae_value,
        "rmse": rmse_value,
        "combined_error": 0.5 * mae_value + 0.5 * rmse_value,
        "mean_error": mean_error(values_a, values_b),
        "osc_ratio": float(rmse_value / max(mae_value, 1e-9)),
    }


def get_benchmark_duration_seconds(benchmark):
    source_cycles = benchmark.get("source_cycles", [])

    durations = [
        safe_float(c.get("duration_seconds"), None)
        for c in source_cycles
        if isinstance(c, dict) and c.get("duration_seconds") is not None
    ]

    durations = [d for d in durations if d is not None and d > 0]

    if not durations:
        return None

    return float(np.mean(durations))


# ---------------------------------------------------------------------
# V4 triple-peak stage and sub-window detection
# ---------------------------------------------------------------------
def detect_major_peaks(y, min_distance_ratio=0.08, prominence_ratio=0.04):
    y = np.asarray(y, dtype=float)

    if len(y) < 5:
        return np.array([], dtype=int), {}

    y_range = max(float(np.max(y) - np.min(y)), 1e-9)
    min_distance = max(2, int(len(y) * min_distance_ratio))
    prominence = max(y_range * prominence_ratio, 1e-6)

    peaks, props = find_peaks(
        y,
        distance=min_distance,
        prominence=prominence,
    )

    return peaks, props


def fallback_three_peak_indices(y):
    """
    Fallback when peak detection does not return 3 clear peaks.
    It splits the benchmark into three broad zones and takes the max in each zone.
    """

    y = np.asarray(y, dtype=float)
    n = len(y)

    zones = [
        (0, max(1, int(n * 0.33))),
        (max(1, int(n * 0.25)), max(2, int(n * 0.66))),
        (max(2, int(n * 0.55)), n - 1),
    ]

    peak_indices = []
    for s, e in zones:
        s = int(np.clip(s, 0, n - 2))
        e = int(np.clip(e, s + 1, n - 1))
        local_idx = int(np.argmax(y[s:e + 1]))
        peak_indices.append(s + local_idx)

    peak_indices = sorted(list(set(peak_indices)))

    while len(peak_indices) < 3:
        candidate = int(np.argmax(y))
        if candidate not in peak_indices:
            peak_indices.append(candidate)
        else:
            peak_indices.append(min(n - 1, candidate + len(peak_indices)))

    return sorted(peak_indices[:3])


def choose_three_process_peaks(y):
    """
    Detect S1/S2/S3 peaks from benchmark normalised curve.
    Prefer three process peaks in chronological order.
    """

    peaks, props = detect_major_peaks(y)

    if len(peaks) < 3:
        return fallback_three_peak_indices(y)

    # If more than 3 peaks are detected, keep important peaks while preserving order.
    prominences = props.get("prominences", np.ones(len(peaks)))
    ranked = sorted(
        [(int(p), float(prom)) for p, prom in zip(peaks, prominences)],
        key=lambda item: item[1],
        reverse=True,
    )

    selected = sorted([p for p, _ in ranked[:3]])

    # If the selected peaks are too close to each other, fall back to chronological first 3.
    if len(selected) == 3 and min(np.diff(selected)) >= max(2, int(len(y) * 0.05)):
        return selected

    return [int(p) for p in sorted(peaks[:3])]


def valley_between(y, start_idx, end_idx):
    y = np.asarray(y, dtype=float)
    n = len(y)

    s = int(np.clip(start_idx, 0, n - 2))
    e = int(np.clip(end_idx, s + 1, n - 1))

    local_idx = int(np.argmin(y[s:e + 1]))
    return s + local_idx


def detect_exhaust_start_after_s3(y, s3_peak_idx):
    """
    Exhaust starts after the S3 high-pressure/holding region ends.

    Without valve signal, this approximates exhaust start as the first sustained
    drop below the high-pressure threshold after the S3 peak.
    """

    y = np.asarray(y, dtype=float)
    n = len(y)

    p3 = int(np.clip(s3_peak_idx, 0, n - 2))

    high_threshold = 0.75
    min_hold_len = max(3, int(n * 0.03))

    # Find first point after a minimum holding length where pressure drops below high threshold.
    for i in range(p3 + min_hold_len, n - 1):
        if y[i] < high_threshold and np.mean(np.diff(y[max(p3, i - 3):i + 1])) < 0:
            return int(i)

    # Fallback: first stronger negative gradient after the third peak.
    gradients = np.diff(y)
    if p3 + 2 < len(gradients):
        tail = gradients[p3 + 1:]
        drop_candidates = np.where(tail < -0.01)[0]
        if len(drop_candidates) > 0:
            return int(p3 + 1 + drop_candidates[0])

    return n - 1


def peak_window_around(y, peak_idx, stage_start, stage_end, within_ratio=0.05):
    """
    V4 peak sub-window: values within 5% of the local peak.
    """

    y = np.asarray(y, dtype=float)
    n = len(y)

    s = int(np.clip(stage_start, 0, n - 2))
    e = int(np.clip(stage_end, s + 1, n - 1))
    p = int(np.clip(peak_idx, s, e))

    local = y[s:e + 1]
    local_min = float(np.min(local))
    peak_value = float(y[p])
    local_range = max(peak_value - local_min, 1e-9)
    threshold = peak_value - within_ratio * local_range

    left = p
    while left > s and y[left - 1] >= threshold:
        left -= 1

    right = p
    while right < e and y[right + 1] >= threshold:
        right += 1

    return int(left), int(max(left, right))


def build_v4_stage_windows_from_benchmark(benchmark_norm):
    """
    Build S1/S2/S3/EX windows from the normalised benchmark.

    V4 expects pressure-event based triple peak boundaries:
    - S1 ends at the first local minimum after first peak.
    - S2 ends at the second local minimum after second peak.
    - S3 ends when exhaust starts / sustained pressure drop begins.
    - Exhaust is separate safety-only region.
    """

    y = np.asarray(benchmark_norm, dtype=float)
    n = len(y)

    if n < 30:
        raise ValueError("Benchmark curve too short for V4 stage detection.")

    p1, p2, p3 = choose_three_process_peaks(y)

    if not (p1 < p2 < p3):
        p1, p2, p3 = fallback_three_peak_indices(y)

    s1_min = valley_between(y, p1, p2)
    s2_min = valley_between(y, p2, p3)
    exhaust_start = detect_exhaust_start_after_s3(y, p3)

    s1_start = 0
    s1_end = max(s1_min, p1 + 1)

    s2_start = s1_end
    s2_end = max(s2_min, p2 + 1)

    s3_start = s2_end
    s3_end = max(exhaust_start, p3 + 1)

    exhaust_start = min(max(s3_end, p3 + 1), n - 1)
    exhaust_end = n - 1

    # Guard boundaries
    s1_end = int(np.clip(s1_end, 1, n - 3))
    s2_start = s1_end
    s2_end = int(np.clip(max(s2_end, s2_start + 1), s2_start + 1, n - 2))
    s3_start = s2_end
    s3_end = int(np.clip(max(s3_end, s3_start + 1), s3_start + 1, n - 1))

    if s3_end >= n - 1:
        exhaust_start = n - 1
    else:
        exhaust_start = s3_end

    s1_peak_left, s1_peak_right = peak_window_around(y, p1, s1_start, s1_end)
    s2_peak_left, s2_peak_right = peak_window_around(y, p2, s2_start, s2_end)
    s3_peak_left, s3_peak_right = peak_window_around(y, p3, s3_start, s3_end)

    windows = {
        "s1_full": (s1_start, s1_end),
        "s2_full": (s2_start, s2_end),
        "s3_full": (s3_start, s3_end),
        "exhaust_full": (exhaust_start, exhaust_end),

        "s1_ramp": (s1_start, p1),
        "s1_peak": (s1_peak_left, s1_peak_right),
        "s1_release": (p1, s1_end),

        "s2_ramp": (s2_start, p2),
        "s2_peak": (s2_peak_left, s2_peak_right),
        "s2_release": (p2, s2_end),

        "s3_ramp": (s3_start, p3),
        "s3_peak": (s3_peak_left, s3_peak_right),
        "s3_hold": (p3, s3_end),
    }

    progress = {}
    for name, (s, e) in windows.items():
        progress[f"{name}_start_progress"] = float(s / max(n - 1, 1))
        progress[f"{name}_end_progress"] = float(e / max(n - 1, 1))

    return {
        "peaks": {
            "s1_peak_idx": int(p1),
            "s2_peak_idx": int(p2),
            "s3_peak_idx": int(p3),
        },
        "boundaries": {
            "s1_start_idx": int(s1_start),
            "s1_end_idx": int(s1_end),
            "s2_start_idx": int(s2_start),
            "s2_end_idx": int(s2_end),
            "s3_start_idx": int(s3_start),
            "s3_end_idx": int(s3_end),
            "exhaust_start_idx": int(exhaust_start),
            "exhaust_end_idx": int(exhaust_end),
            **progress,
        },
        "windows": windows,
        "stage_detection_method": "v4_triple_peak_benchmark_shape",
    }


# ---------------------------------------------------------------------
# V4 scoring metrics
# ---------------------------------------------------------------------
def compute_window_metric(actual_norm, bench_norm, window):
    s, e = window
    actual = window_slice(actual_norm, s, e)
    bench = window_slice(bench_norm, s, e)
    return combined_mae_rmse(actual, bench)


def compute_all_v4_window_metrics(actual_norm, bench_norm, stage_info):
    metrics = {}

    for window_name, window in stage_info["windows"].items():
        m = compute_window_metric(actual_norm, bench_norm, window)

        metrics[f"MAE_{window_name}"] = m["mae"]
        metrics[f"RMSE_{window_name}"] = m["rmse"]
        metrics[f"combined_{window_name}"] = m["combined_error"]
        metrics[f"mean_error_{window_name}"] = m["mean_error"]
        metrics[f"osc_ratio_{window_name}"] = m["osc_ratio"]

    return metrics


def compute_v4_stage_scores(window_metrics):
    s1_combined = window_metrics["combined_s1_full"]
    s2_combined = window_metrics["combined_s2_full"]
    s3_combined = window_metrics["combined_s3_full"]

    s1_score = exponential_score(s1_combined, K_EXPONENTIAL)
    s2_score = exponential_score(s2_combined, K_EXPONENTIAL)
    s3_score = exponential_score(s3_combined, K_EXPONENTIAL)

    cycle_score = (
        STAGE_WEIGHTS["s1_score"] * s1_score
        + STAGE_WEIGHTS["s2_score"] * s2_score
        + STAGE_WEIGHTS["s3_score"] * s3_score
    )

    return {
        "s1_score": clip_score(s1_score),
        "s2_score": clip_score(s2_score),
        "s3_score": clip_score(s3_score),
        "cycle_score": clip_score(cycle_score),
        "s1_combined_error": float(s1_combined),
        "s2_combined_error": float(s2_combined),
        "s3_combined_error": float(s3_combined),
    }


def classify_v4_score(score):
    score = float(score)

    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "fair"
    if score >= 50:
        return "poor"
    return "critical"


def classify_for_existing_ui(score):
    """
    Keep compatibility with existing UI badges:
    normal / warning / abnormal.
    """
    score = float(score)

    if score >= WARNING_SCORE_THRESHOLD:
        return "normal"
    if score >= POOR_SCORE_THRESHOLD:
        return "warning"
    return "abnormal"


def threshold_status_from_error(error_value):
    error_value = float(error_value)

    if error_value > CRITICAL_COMBINED_ERROR:
        return "critical"
    if error_value > WARN_COMBINED_ERROR:
        return "warning"
    return "normal"


def status_rank(status):
    return {
        "normal": 0,
        "warning": 1,
        "critical": 2,
    }.get(status, 0)


def derive_divergence_patterns(window_metrics):
    """
    Implements V4 pattern hints:
    A = Peak too low
    B = Slow ramp / right shifted
    C = Oscillation
    D = Release abnormal
    E = Holding drift
    F = Systemic (not confirmable with single-cycle data here)
    """

    patterns = []

    stage_prefixes = ["s1", "s2"]
    for sx in stage_prefixes:
        peak_mae = window_metrics.get(f"MAE_{sx}_peak", 0.0)
        ramp_mae = window_metrics.get(f"MAE_{sx}_ramp", 0.0)
        release_mae = window_metrics.get(f"MAE_{sx}_release", 0.0)
        ramp_mean_error = window_metrics.get(f"mean_error_{sx}_ramp", 0.0)
        full_osc_ratio = window_metrics.get(f"osc_ratio_{sx}_full", 0.0)
        full_combined = window_metrics.get(f"combined_{sx}_full", 0.0)

        if peak_mae > WARN_COMBINED_ERROR and ramp_mae <= WARN_COMBINED_ERROR:
            patterns.append({
                "stage": sx.upper(),
                "pattern": "A",
                "label": "Peak too low / peak deviation",
                "evidence_metric": f"MAE_{sx}_peak",
                "evidence_value": float(peak_mae),
            })

        if ramp_mae > WARN_COMBINED_ERROR and ramp_mean_error < -0.05:
            patterns.append({
                "stage": sx.upper(),
                "pattern": "B",
                "label": "Slow ramp / right-shifted curve",
                "evidence_metric": f"MAE_{sx}_ramp + mean_error_{sx}_ramp",
                "evidence_value": float(ramp_mae),
            })

        if full_osc_ratio > OSC_WARN_RATIO and full_combined > WARN_COMBINED_ERROR:
            patterns.append({
                "stage": sx.upper(),
                "pattern": "C",
                "label": "Oscillation / RMSE much larger than MAE",
                "evidence_metric": f"osc_ratio_{sx}_full",
                "evidence_value": float(full_osc_ratio),
            })

        if release_mae > WARN_COMBINED_ERROR and peak_mae <= WARN_COMBINED_ERROR and ramp_mae <= WARN_COMBINED_ERROR:
            patterns.append({
                "stage": sx.upper(),
                "pattern": "D",
                "label": "Partial release abnormal",
                "evidence_metric": f"MAE_{sx}_release",
                "evidence_value": float(release_mae),
            })

    # S3
    s3_hold_mae = window_metrics.get("MAE_s3_hold", 0.0)
    s3_hold_combined = window_metrics.get("combined_s3_hold", 0.0)
    s3_hold_osc = window_metrics.get("osc_ratio_s3_hold", 0.0)
    s3_ramp_mae = window_metrics.get("MAE_s3_ramp", 0.0)
    s3_ramp_mean_error = window_metrics.get("mean_error_s3_ramp", 0.0)

    if s3_ramp_mae > WARN_COMBINED_ERROR and s3_ramp_mean_error < -0.05:
        patterns.append({
            "stage": "S3",
            "pattern": "B",
            "label": "Slow S3 ramp",
            "evidence_metric": "MAE_s3_ramp + mean_error_s3_ramp",
            "evidence_value": float(s3_ramp_mae),
        })

    if s3_hold_mae > WARN_COMBINED_ERROR or s3_hold_combined > WARN_COMBINED_ERROR:
        patterns.append({
            "stage": "S3",
            "pattern": "E",
            "label": "Holding drift / holding deviation",
            "evidence_metric": "MAE_s3_hold / combined_s3_hold",
            "evidence_value": float(max(s3_hold_mae, s3_hold_combined)),
        })

    if s3_hold_osc > OSC_WARN_RATIO and s3_hold_combined > WARN_COMBINED_ERROR:
        patterns.append({
            "stage": "S3",
            "pattern": "C+E",
            "label": "Oscillating holding stage",
            "evidence_metric": "osc_ratio_s3_hold",
            "evidence_value": float(s3_hold_osc),
        })

    return patterns


def determine_affected_stage(stage_scores):
    stage_errors = {
        "S1": stage_scores["s1_combined_error"],
        "S2": stage_scores["s2_combined_error"],
        "S3": stage_scores["s3_combined_error"],
    }

    affected_stage = max(stage_errors, key=stage_errors.get)
    return affected_stage, stage_errors


def build_v4_rca_hints(stage_scores, window_metrics, patterns):
    affected_stage, stage_errors = determine_affected_stage(stage_scores)

    # Any stage below 75 should trigger RCA candidate retrieval.
    rca_triggered = (
        stage_scores["s1_score"] < WARNING_SCORE_THRESHOLD
        or stage_scores["s2_score"] < WARNING_SCORE_THRESHOLD
        or stage_scores["s3_score"] < WARNING_SCORE_THRESHOLD
        or stage_scores["cycle_score"] < WARNING_SCORE_THRESHOLD
    )

    worst_stage_score = {
        "S1": stage_scores["s1_score"],
        "S2": stage_scores["s2_score"],
        "S3": stage_scores["s3_score"],
    }[affected_stage]

    if patterns:
        pattern_code = "+".join(sorted(set(p["pattern"] for p in patterns if p.get("stage") == affected_stage)))
        if not pattern_code:
            pattern_code = "+".join(sorted(set(p["pattern"] for p in patterns)))
    else:
        pattern_code = None

    if affected_stage == "S1":
        query = "S1 Stage 1 first peak ramp air purging MAE_s1_peak MAE_s1_ramp Boiler BPV Competition Network Local"
    elif affected_stage == "S2":
        query = "S2 Stage 2 second peak fruitlet loosening MAE_s2_peak MAE_s2_ramp release Competition Boiler BPV Local"
    else:
        query = "S3 Stage 3 third peak holding MAE_s3_hold RMSE_s3_hold holding drift Boiler BPV Local"

    if pattern_code:
        query += f" Pattern {pattern_code}"

    # Single-cycle scoring cannot confirm Pattern F/systemic causes by itself.
    return {
        "rca_triggered": bool(rca_triggered),
        "affected_stage": affected_stage,
        "affected_stage_combined_error": float(stage_errors[affected_stage]),
        "affected_stage_score": float(worst_stage_score),
        "pattern": pattern_code,
        "patterns_detected": patterns,
        "rag_query_hint": query,
        "systemic_confirmation_available": False,
        "rca_confirmation_note": (
            "Single-cycle benchmark comparison can detect the deviation symptom. "
            "Boiler/BPV/Competition/Network attribution needs peer sterilizer, boiler, BPV, "
            "or concurrent demand data for confirmation."
        ),
    }


def compute_data_quality_score(cycle_df, value_col="smooth"):
    quality_issues = []
    penalties = []

    n_points = len(cycle_df)

    if n_points < 20:
        quality_issues.append("too_few_points")
        penalties.append(0.50)

    values = cycle_df[value_col].to_numpy(dtype=float)

    if np.isnan(values).any():
        quality_issues.append("missing_or_nan_values")
        penalties.append(0.40)

    if np.nanstd(values) < 1e-6:
        quality_issues.append("flatline_signal")
        penalties.append(0.35)

    gap_ratio = 0.0
    try:
        times = pd.to_datetime(cycle_df["time"])
        deltas = times.diff().dt.total_seconds().dropna().to_numpy(dtype=float)

        if len(deltas) > 0:
            median_delta = float(np.median(deltas))
            if median_delta > 0:
                large_gaps = deltas > (5.0 * median_delta)
                gap_ratio = float(np.mean(large_gaps))
                if gap_ratio > 0.05:
                    quality_issues.append("irregular_or_missing_time_gaps")
                    penalties.append(min(gap_ratio, 0.50))
    except Exception:
        quality_issues.append("timestamp_quality_check_failed")
        penalties.append(0.20)

    spike_ratio = 0.0
    if len(values) >= 5:
        diffs = np.diff(values)
        med = float(np.median(diffs))
        mad = float(np.median(np.abs(diffs - med)))
        threshold = max(6.0 * mad, 1e-6)

        spike_ratio = float(np.mean(np.abs(diffs - med) > threshold))
        if spike_ratio > 0.05:
            quality_issues.append("possible_spikes_or_signal_noise")
            penalties.append(min(spike_ratio, 0.50))

    combined_penalty = min(float(np.sum(penalties)), 1.0) if penalties else 0.0

    return {
        "data_quality_score": clip_score(100.0 * (1.0 - combined_penalty)),
        "data_quality_penalty": combined_penalty,
        "data_quality_issues": quality_issues,
        "data_quality_gap_ratio": gap_ratio,
        "data_quality_spike_ratio": spike_ratio,
        "data_point_count": int(n_points),
    }


def generate_v4_feedback(stage_scores, rca_hints, data_quality):
    cycle_score = stage_scores["cycle_score"]
    band = classify_v4_score(cycle_score)

    messages = []

    if band in {"excellent", "good"}:
        messages.append("Cycle follows the benchmark closely based on V4 S1/S2/S3 MAE+RMSE scoring.")
    elif band == "fair":
        messages.append("Cycle shows noticeable deviation from benchmark. RCA review is recommended.")
    elif band == "poor":
        messages.append("Cycle shows significant deviation from benchmark. RCA is required.")
    else:
        messages.append("Cycle shows severe deviation from benchmark. Immediate inspection is recommended.")

    affected_stage = rca_hints.get("affected_stage")
    if rca_hints.get("rca_triggered"):
        messages.append(f"The most affected stage is {affected_stage}.")

    if affected_stage == "S1":
        messages.append("S1 relates to air purging / first peak behaviour.")
    elif affected_stage == "S2":
        messages.append("S2 relates to fruitlet loosening / second peak behaviour.")
    elif affected_stage == "S3":
        messages.append("S3 relates to final holding / sterilization behaviour and carries the highest weight.")

    patterns = rca_hints.get("patterns_detected", [])
    if patterns:
        labels = []
        for p in patterns[:3]:
            labels.append(f"{p.get('stage')} Pattern {p.get('pattern')}: {p.get('label')}")
        messages.append("Detected divergence pattern(s): " + "; ".join(labels))

    if data_quality["data_quality_score"] < 85:
        messages.append("Data quality issue may affect interpretation. Verify sensor/database readings.")

    if not rca_hints.get("systemic_confirmation_available"):
        messages.append(
            "Current result is based on single-cycle curve comparison. "
            "Systemic root causes such as Boiler, BPV, Competition, or Network require peer/system data to confirm."
        )

    return messages


# ---------------------------------------------------------------------
# Main comparison
# ---------------------------------------------------------------------
def compare_single_cycle_to_benchmark(cycle_df, benchmark, value_col="smooth"):
    benchmark_curve = np.asarray(benchmark["benchmark_curve"], dtype=float)
    benchmark_std = np.asarray(
        benchmark.get("benchmark_std", np.zeros_like(benchmark_curve)),
        dtype=float,
    )

    (
        actual_resampled,
        benchmark_resampled,
        actual_norm,
        bench_norm,
        norm_info,
    ) = normalise_cycle_to_benchmark_length(
        cycle_df=cycle_df,
        benchmark_curve=benchmark_curve,
        value_col=value_col,
    )

    stage_info = build_v4_stage_windows_from_benchmark(bench_norm)
    window_metrics = compute_all_v4_window_metrics(actual_norm, bench_norm, stage_info)
    stage_scores = compute_v4_stage_scores(window_metrics)
    patterns = derive_divergence_patterns(window_metrics)
    rca_hints = build_v4_rca_hints(stage_scores, window_metrics, patterns)
    data_quality = compute_data_quality_score(cycle_df, value_col=value_col)

    real_duration = float(
        (cycle_df["time"].iloc[-1] - cycle_df["time"].iloc[0]).total_seconds()
    )
    bench_duration = get_benchmark_duration_seconds(benchmark)

    feedback_messages = generate_v4_feedback(stage_scores, rca_hints, data_quality)

    n_points = len(benchmark_resampled)
    progress = np.linspace(0, 100, n_points)

    # Overlay chart in original pressure scale for analysis page.
    overlay_chart = [
        {
            "progress": float(progress[i]),
            "realtime": float(actual_resampled[i]),
            "benchmark": float(benchmark_resampled[i]),
            "benchmark_upper": float(benchmark_resampled[i] + benchmark_std[i]),
            "benchmark_lower": float(benchmark_resampled[i] - benchmark_std[i]),
        }
        for i in range(n_points)
    ]

    original_chart = [
        {
            "time": t.isoformat(),
            "value": float(v),
            "smooth": float(s),
        }
        for t, v, s in zip(cycle_df["time"], cycle_df["value"], cycle_df["smooth"])
    ]

    visual_overlay_chart = build_visual_overlay_on_realtime_time(cycle_df, benchmark)

    cycle_score = stage_scores["cycle_score"]

    result = {
        "cycle_start": cycle_df["time"].iloc[0].isoformat(),
        "cycle_end": cycle_df["time"].iloc[-1].isoformat(),
        "duration_seconds": real_duration,

        # Keep old field name "score" for frontend compatibility.
        "score": round(cycle_score, 2),
        "classification": classify_for_existing_ui(cycle_score),
        "score_band": classify_v4_score(cycle_score),
        "feedback_messages": feedback_messages,

        # V4 score breakdown.
        "score_breakdown": {
            "s1_score": round(stage_scores["s1_score"], 2),
            "s2_score": round(stage_scores["s2_score"], 2),
            "s3_score": round(stage_scores["s3_score"], 2),
            "cycle_score": round(stage_scores["cycle_score"], 2),
        },
        "score_weights": STAGE_WEIGHTS,

        "metrics": {
            "scoring_version": "V4_MAE_RMSE_TRIPLE_PEAK",
            "normalisation_method": "full_cycle_minmax_actual_and_benchmark",
            "alignment_method": "full_cycle_progress_interpolation_point_to_point",
            "error_method": "MAE_RMSE_50_50",
            "exponential_k": K_EXPONENTIAL,

            "warning_score_threshold": WARNING_SCORE_THRESHOLD,
            "poor_score_threshold": POOR_SCORE_THRESHOLD,
            "critical_score_threshold": CRITICAL_SCORE_THRESHOLD,
            "warn_combined_error_threshold": WARN_COMBINED_ERROR,
            "critical_combined_error_threshold": CRITICAL_COMBINED_ERROR,

            "s1_combined_error": stage_scores["s1_combined_error"],
            "s2_combined_error": stage_scores["s2_combined_error"],
            "s3_combined_error": stage_scores["s3_combined_error"],
            "s1_status": threshold_status_from_error(stage_scores["s1_combined_error"]),
            "s2_status": threshold_status_from_error(stage_scores["s2_combined_error"]),
            "s3_status": threshold_status_from_error(stage_scores["s3_combined_error"]),

            "benchmark_duration_seconds": bench_duration,
            "duration_seconds": real_duration,
            "duration_ratio": (
                float(real_duration / bench_duration)
                if bench_duration and bench_duration > 0
                else None
            ),

            "normalisation_info": norm_info,
            "stage_detection_method": stage_info["stage_detection_method"],
            "stage_boundaries": stage_info["boundaries"],
            "stage_peaks": stage_info["peaks"],

            # V4 sub-window metrics used by Rules Master / Threshold Library.
            **window_metrics,

            # RCA/RAG hints.
            "rca_triggered": rca_hints["rca_triggered"],
            "affected_stage": rca_hints["affected_stage"],
            "affected_stage_score": rca_hints["affected_stage_score"],
            "affected_stage_combined_error": rca_hints["affected_stage_combined_error"],
            "pattern": rca_hints["pattern"],
            "patterns_detected": rca_hints["patterns_detected"],
            "rag_query_hint": rca_hints["rag_query_hint"],
            "systemic_confirmation_available": rca_hints["systemic_confirmation_available"],
            "rca_confirmation_note": rca_hints["rca_confirmation_note"],

            # Data quality is reported but not included in V4 cycle score.
            **data_quality,
        },

        "visual_overlay_chart": visual_overlay_chart,
        "overlay_chart": overlay_chart,
        "original_cycle_chart": original_chart,
    }

    # RCA feedback is generated lazily from the frontend when the user clicks
    # a specific abnormal cycle/detail item. This avoids running RAG for every
    # detected cycle and improves comparison-page performance.
    result["rca_feedback"] = None
    result["rca_feedback_status"] = "lazy_not_generated"

    return result


def compare_detected_cycles_to_benchmark(df, cycles_idx, benchmark, value_col="smooth"):
    comparisons = []

    for idx, (s, e) in enumerate(cycles_idx, start=1):
        cycle_df = df.iloc[s:e + 1].copy().reset_index(drop=True)
        comp = compare_single_cycle_to_benchmark(
            cycle_df,
            benchmark,
            value_col=value_col,
        )
        comp["cycle_no"] = idx
        comparisons.append(comp)

    return comparisons


def build_visual_overlay_on_realtime_time(cycle_df, benchmark):
    benchmark_curve = np.asarray(benchmark["benchmark_curve"], dtype=float)
    benchmark_std = np.asarray(
        benchmark.get("benchmark_std", np.zeros_like(benchmark_curve)),
        dtype=float,
    )

    n_real = len(cycle_df)

    if n_real < 2:
        raise ValueError("Cycle too short for visualization overlay.")

    x_bench = np.linspace(0, 1, len(benchmark_curve))
    x_real = np.linspace(0, 1, n_real)

    benchmark_on_real = np.interp(x_real, x_bench, benchmark_curve)
    benchmark_upper_on_real = np.interp(x_real, x_bench, benchmark_curve + benchmark_std)
    benchmark_lower_on_real = np.interp(x_real, x_bench, benchmark_curve - benchmark_std)

    chart = []

    for i in range(n_real):
        chart.append(
            {
                "time": cycle_df["time"].iloc[i].isoformat(),
                "raw_value": float(cycle_df["value"].iloc[i]),
                "realtime_smooth": float(cycle_df["smooth"].iloc[i]),
                "benchmark": float(benchmark_on_real[i]),
                "benchmark_upper": float(benchmark_upper_on_real[i]),
                "benchmark_lower": float(benchmark_lower_on_real[i]),
            }
        )

    return chart


def build_continuous_window_overlay(df, cycles_idx, cycle_results, benchmark):
    full_chart = []

    for i in range(len(df)):
        full_chart.append(
            {
                "time": df["time"].iloc[i].isoformat(),
                "raw_value": float(df["value"].iloc[i]),
                "realtime_smooth": float(df["smooth"].iloc[i]),
                "benchmark_overlay": None,
                "cycle_no": None,
                "cycle_score": None,
            }
        )

    benchmark_curve = np.asarray(benchmark["benchmark_curve"], dtype=float)

    for cycle_no, ((s, e), cycle_result) in enumerate(
        zip(cycles_idx, cycle_results),
        start=1,
    ):
        cycle_len = e - s + 1

        if cycle_len < 2:
            continue

        x_bench = np.linspace(0, 1, len(benchmark_curve))
        x_cycle = np.linspace(0, 1, cycle_len)
        benchmark_on_cycle = np.interp(x_cycle, x_bench, benchmark_curve)

        for j in range(cycle_len):
            row_idx = s + j
            full_chart[row_idx]["benchmark_overlay"] = float(benchmark_on_cycle[j])
            full_chart[row_idx]["cycle_no"] = cycle_no
            full_chart[row_idx]["cycle_score"] = float(cycle_result["score"])

    return full_chart
