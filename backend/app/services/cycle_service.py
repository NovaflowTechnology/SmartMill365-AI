import numpy as np
import pandas as pd


def get_run_segments(mask):
    mask = np.asarray(mask, dtype=bool)

    segments = []
    if len(mask) == 0:
        return segments

    start = 0
    current = bool(mask[0])

    for i in range(1, len(mask)):
        if bool(mask[i]) != current:
            segments.append((start, i - 1, current))
            start = i
            current = bool(mask[i])

    segments.append((start, len(mask) - 1, current))
    return segments


def fill_short_false_gaps(mask, max_gap):
    mask = np.asarray(mask, dtype=bool).copy()
    segments = get_run_segments(mask)

    for idx, (s, e, val) in enumerate(segments):
        if not val:
            gap_len = e - s + 1
            has_left_true = idx > 0 and bool(segments[idx - 1][2])
            has_right_true = idx < len(segments) - 1 and bool(segments[idx + 1][2])

            if has_left_true and has_right_true and gap_len <= max_gap:
                mask[s:e+1] = True

    return mask


def remove_short_true_runs(mask, min_len):
    mask = np.asarray(mask, dtype=bool).copy()
    segments = get_run_segments(mask)

    for s, e, val in segments:
        if val:
            run_len = e - s + 1
            if run_len < min_len:
                mask[s:e+1] = False

    return mask


def detect_full_cycles(df, baseline_value, baseline_margin):
    df = df.copy()

    threshold = baseline_value + baseline_margin
    df["active_raw"] = df["smooth"] > threshold

    dt_sec = df["time"].diff().dt.total_seconds().median()
    if pd.isna(dt_sec) or dt_sec <= 0:
        dt_sec = 1.0

    gap_fill_minutes = 25
    min_cycle_minutes = 5

    gap_fill_points = max(1, int((gap_fill_minutes * 60) / dt_sec))
    min_cycle_points = max(1, int((min_cycle_minutes * 60) / dt_sec))

    active_filled = fill_short_false_gaps(df["active_raw"].to_numpy(), max_gap=gap_fill_points)
    active_clean = remove_short_true_runs(active_filled, min_len=min_cycle_points)

    df["active_clean"] = active_clean

    segments = get_run_segments(active_clean)
    cycles_idx = [(s, e) for s, e, val in segments if val]

    return df, cycles_idx, threshold


def refine_cycle_boundaries(df, cycles_idx, baseline_value, baseline_margin, search_points=40, stable_points=5):
    refined = []
    threshold = baseline_value + baseline_margin

    for s, e in cycles_idx:
        start = s
        end = e

        for i in range(s - 1, max(-1, s - search_points - 1), -1):
            if df["smooth"].iloc[i] <= threshold:
                start = i
            else:
                break

        last_possible = min(len(df) - stable_points, e + search_points)
        found_end = e

        for i in range(e + 1, last_possible + 1):
            window = df["smooth"].iloc[i:i + stable_points]
            if (window <= threshold).all():
                found_end = i + stable_points - 1
                break

        end = found_end
        refined.append((start, end))

    return refined


def build_cycle_summary(df, cycles_idx, value_col="smooth"):
    output = []

    for i, (s, e) in enumerate(cycles_idx, start=1):
        cycle_df = df.iloc[s:e+1].copy().reset_index(drop=True)

        output.append({
            "cycle_no": i,
            "start": cycle_df["time"].iloc[0].isoformat(),
            "end": cycle_df["time"].iloc[-1].isoformat(),
            "num_points_original": int(len(cycle_df)),
            "duration_seconds": float(
                (cycle_df["time"].iloc[-1] - cycle_df["time"].iloc[0]).total_seconds()
            ),
            "max_value": float(cycle_df[value_col].max()),
            "min_value": float(cycle_df[value_col].min()),
            "source_unit": cycle_df["source_unit"].iloc[0] if "source_unit" in cycle_df.columns else None,
            "benchmark_unit": cycle_df["benchmark_unit"].iloc[0] if "benchmark_unit" in cycle_df.columns else None,
        })

    return output