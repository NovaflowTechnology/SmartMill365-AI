import numpy as np
import pandas as pd

from app.config import (
    CYCLE_BOUNDARY_LOOKAROUND_MINUTES,
    CYCLE_IDLE_TOLERANCE_BAR,
    CYCLE_MAX_DATA_GAP_SECONDS,
    CYCLE_MAX_INACTIVE_GAP_MINUTES,
    CYCLE_MIN_ACTIVE_MINUTES,
    CYCLE_STABLE_BASELINE_MINUTES,
)
from app.services.unit_service import convert_values


# A boundary is accepted only after the signal has remained at a stable idle
# pressure for this amount of real time.  The confirmation interval is not
# added to the cycle curve; only the last baseline point before ramp-up and the
# first baseline point after depressurisation are retained.
DEFAULT_STABLE_BASELINE_MINUTES = CYCLE_STABLE_BASELINE_MINUTES
DEFAULT_BOUNDARY_LOOKAROUND_MINUTES = CYCLE_BOUNDARY_LOOKAROUND_MINUTES


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
    df = df.copy().sort_values("time").reset_index(drop=True)

    threshold = baseline_value + baseline_margin
    df["active_raw"] = df["smooth"] > threshold

    dt_sec = df["time"].diff().dt.total_seconds().median()
    if pd.isna(dt_sec) or dt_sec <= 0:
        dt_sec = 1.0

    gap_fill_points = max(
        1,
        int((CYCLE_MAX_INACTIVE_GAP_MINUTES * 60) / dt_sec),
    )
    min_cycle_points = max(1, int((CYCLE_MIN_ACTIVE_MINUTES * 60) / dt_sec))

    # A missing-data interval is not an idle-pressure interval.  Process each
    # continuous timestamp group independently so gap filling can never join
    # two pressure fragments across missing telemetry.
    allowed_data_gap = max(float(CYCLE_MAX_DATA_GAP_SECONDS), dt_sec * 5.0)
    discontinuity = (
        df["time"].diff().dt.total_seconds().fillna(0.0) > allowed_data_gap
    )
    df["_continuity_group"] = discontinuity.cumsum().astype(int)
    active_clean = np.zeros(len(df), dtype=bool)
    cycles_idx = []

    for _group_id, group in df.groupby("_continuity_group", sort=False):
        positions = group.index.to_numpy(dtype=int)
        if len(positions) == 0:
            continue
        local_raw = df.loc[positions, "active_raw"].to_numpy(dtype=bool)
        local_filled = fill_short_false_gaps(local_raw, max_gap=gap_fill_points)
        local_clean = remove_short_true_runs(local_filled, min_len=min_cycle_points)
        active_clean[positions] = local_clean

        # Anchor a composite cycle to its longest sustained active section.
        # Boundary refinement can then search backwards to include Stage 1 and
        # Stage 2, while ignoring an isolated sensor spike followed by a real
        # stable-idle interval.
        for local_start, local_end, is_active in get_run_segments(local_clean):
            if not is_active:
                continue
            raw_section = local_raw[local_start : local_end + 1]
            raw_runs = [
                (s, e)
                for s, e, value in get_run_segments(raw_section)
                if value and (e - s + 1) >= min_cycle_points
            ]
            if not raw_runs:
                continue
            main_start, _main_end = max(
                raw_runs,
                key=lambda item: item[1] - item[0] + 1,
            )
            cycles_idx.append(
                (
                    int(positions[local_start + main_start]),
                    int(positions[local_end]),
                )
            )

    df["active_clean"] = active_clean

    return df, cycles_idx, threshold


def _median_sample_seconds(df):
    if len(df) < 2:
        return 1.0

    intervals = df["time"].diff().dt.total_seconds()
    intervals = intervals[np.isfinite(intervals) & (intervals > 0)]
    if intervals.empty:
        return 1.0
    return float(intervals.median())


def _points_covering_duration(duration_minutes, sample_seconds):
    duration_seconds = max(0.0, float(duration_minutes)) * 60.0
    return max(2, int(np.ceil(duration_seconds / sample_seconds)) + 1)


def _raw_value_column(df):
    if "value_std" in df.columns and df["value_std"].notna().any():
        return "value_std"
    if "value" in df.columns and df["value"].notna().any():
        return "value"
    return "smooth"


def _estimate_sensor_resolution(values):
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if len(finite) < 2:
        return 1e-6
    unique = np.unique(np.round(finite, 9))
    positive_steps = np.diff(unique)
    positive_steps = positive_steps[positive_steps > 1e-9]
    if len(positive_steps) == 0:
        return 1e-6
    # The lower portion avoids treating large process-stage jumps as sensor
    # resolution while remaining robust to a few floating-point artefacts.
    limit = max(1, min(len(positive_steps), 100))
    return max(1e-6, float(np.median(positive_steps[:limit])))


def _idle_profile(df, baseline_value, baseline_margin):
    raw_col = _raw_value_column(df)
    raw_values = pd.to_numeric(df[raw_col], errors="coerce").to_numpy(dtype=float)
    finite = raw_values[np.isfinite(raw_values)]
    if len(finite) == 0:
        raise ValueError("No usable pressure values are available for cycle detection.")

    resolution = _estimate_sensor_resolution(finite)
    idle_center = float(np.quantile(finite, 0.05))
    derived_idle_allowance = max(
        resolution * 2.0,
        min(
            float(baseline_margin) * 0.05,
            float(CYCLE_IDLE_TOLERANCE_BAR),
        ),
    )
    idle_ceiling_allowance = max(
        derived_idle_allowance,
        float(CYCLE_IDLE_TOLERANCE_BAR),
    )
    threshold = float(baseline_value) + float(baseline_margin)
    idle_ceiling = min(
        threshold - max(resolution, 1e-6),
        idle_center + idle_ceiling_allowance,
    )
    return {
        "raw_col": raw_col,
        "idle_center": idle_center,
        "idle_ceiling": idle_ceiling,
        # Keep the existing spread/slope checks independent from the new idle
        # ceiling. This accepts a realistic non-zero idle level without making
        # a noisy or moving pressure region qualify as stable baseline.
        "allowed_spread": max(
            resolution * 4.0,
            derived_idle_allowance * 1.50,
        ),
        "allowed_slope_per_minute": max(
            resolution * 2.0,
            float(baseline_margin) * 0.02,
        ),
    }


def _stable_baseline_window(
    df,
    start,
    end,
    baseline_value,
    baseline_margin,
    required_minutes,
    sample_seconds,
    idle_profile,
):
    """Return True when a window is continuously stable near idle pressure."""

    if start < 0 or end >= len(df) or end <= start:
        return False

    window = df.iloc[start : end + 1]
    values = pd.to_numeric(
        window[idle_profile["raw_col"]], errors="coerce"
    ).to_numpy(dtype=float)
    if len(values) < 2 or not np.isfinite(values).all():
        return False

    times = pd.to_datetime(window["time"], errors="coerce")
    if times.isna().any():
        return False
    elapsed_seconds = float((times.iloc[-1] - times.iloc[0]).total_seconds())
    required_seconds = max(0.0, float(required_minutes)) * 60.0
    if elapsed_seconds + sample_seconds * 0.25 < required_seconds:
        return False

    gaps = times.diff().dt.total_seconds().dropna()
    if not gaps.empty and float(gaps.max()) > max(
        sample_seconds * 5.0,
        float(CYCLE_MAX_DATA_GAP_SECONDS),
    ):
        return False

    q05, q95 = np.quantile(values, [0.05, 0.95])
    if (
        float(q95) > idle_profile["idle_ceiling"]
        or float(q95 - q05) > idle_profile["allowed_spread"]
    ):
        return False

    elapsed_minutes = (times - times.iloc[0]).dt.total_seconds().to_numpy() / 60.0
    if elapsed_minutes[-1] <= 0:
        return False
    slope = float(np.polyfit(elapsed_minutes, values, 1)[0])
    if abs(slope) > idle_profile["allowed_slope_per_minute"]:
        return False

    # A two-minute average can hide the first few samples of a newly rising
    # signal.  Recheck the most recent 30 seconds so the chosen start remains
    # at the end of the genuinely flat baseline, not several points into a
    # gradual ramp.
    tail_seconds = min(required_seconds, 30.0)
    tail_points = max(2, int(np.ceil(tail_seconds / sample_seconds)) + 1)
    tail_values = values[-tail_points:]
    tail_minutes = elapsed_minutes[-tail_points:] - elapsed_minutes[-tail_points]
    if tail_minutes[-1] <= 0:
        return False
    tail_slope = float(np.polyfit(tail_minutes, tail_values, 1)[0])
    return abs(tail_slope) <= idle_profile["allowed_slope_per_minute"]


def refine_cycle_boundaries(
    df,
    cycles_idx,
    baseline_value,
    baseline_margin,
    search_points=None,
    stable_points=None,
    complete_only=True,
    stable_baseline_minutes=DEFAULT_STABLE_BASELINE_MINUTES,
    start_lookback_minutes=DEFAULT_BOUNDARY_LOOKAROUND_MINUTES,
    end_lookahead_minutes=DEFAULT_BOUNDARY_LOOKAROUND_MINUTES,
):
    """Find the full ramp-up and a confirmed return to stable idle pressure.

    The main threshold is still used to identify a sustained active run.  From
    its first crossing, the detector searches backwards for the most recent
    stable baseline window and starts the cycle at that window's final point.
    This retains the entire gradual pressure rise without attaching a long idle
    period.  After depressurisation, two minutes of stable baseline confirms
    completeness, while the cycle itself ends at the first point of that
    confirmation window so idle time does not distort duration or scoring.

    ``search_points`` and ``stable_points`` remain accepted for compatibility
    with older callers.  Current application paths use time-based settings so
    behaviour is consistent at different InfluxDB sampling intervals.

    ``complete_only=False`` is reserved for Live Monitoring.  Other workflows
    receive only cycles with both confirmed boundaries.
    """
    if df is None or df.empty or not cycles_idx:
        return []

    refined = []
    sample_seconds = _median_sample_seconds(df)
    idle_profile = _idle_profile(df, baseline_value, baseline_margin)
    if stable_points is None:
        confirmation_points = _points_covering_duration(
            stable_baseline_minutes,
            sample_seconds,
        )
        required_minutes = float(stable_baseline_minutes)
    else:
        confirmation_points = max(2, int(stable_points))
        required_minutes = (
            (confirmation_points - 1) * sample_seconds / 60.0
        )

    if search_points is None:
        start_search_points = max(
            confirmation_points,
            int(np.ceil(float(start_lookback_minutes) * 60.0 / sample_seconds)),
        )
        end_search_points = max(
            confirmation_points,
            int(np.ceil(float(end_lookahead_minutes) * 60.0 / sample_seconds)),
        )
    else:
        start_search_points = end_search_points = max(0, int(search_points))

    for cycle_position, (s, e) in enumerate(cycles_idx):
        if "_continuity_group" in df.columns:
            group_id = df.iloc[s]["_continuity_group"]
            if df.iloc[e]["_continuity_group"] != group_id:
                continue
            group_positions = np.flatnonzero(
                df["_continuity_group"].to_numpy() == group_id
            )
            group_start = int(group_positions[0])
            group_end = int(group_positions[-1])
        else:
            group_start = 0
            group_end = len(df) - 1

        found_start = None
        # ``s`` is deliberately anchored to the longest sustained active
        # section (normally the high-pressure plateau). For cycles with a long
        # gradual Stage 1/Stage 2, that plateau can begin more than the
        # configured lookback after the real cycle start. In that case the old
        # boundary search never reached the idle baseline and incorrectly
        # discarded an otherwise complete cycle.
        #
        # The cleaned active mask already identifies the full validated
        # composite candidate, including short below-threshold stage gaps. Use
        # the start of that candidate as the lookback origin. We still scan
        # backwards from the plateau and choose the *nearest* stable baseline,
        # so an isolated spike followed by genuine idle pressure remains
        # excluded from the cycle.
        composite_start = s
        if "active_clean" in df.columns:
            clean_mask = df["active_clean"].to_numpy(dtype=bool)
            while composite_start > group_start and clean_mask[composite_start - 1]:
                composite_start -= 1

        earliest_window_start = max(
            group_start,
            composite_start - start_search_points,
        )
        latest_start_window_end = s - 1
        earliest_start_window_end = earliest_window_start + confirmation_points - 1

        for window_end in range(
            latest_start_window_end,
            earliest_start_window_end - 1,
            -1,
        ):
            window_start = window_end - confirmation_points + 1
            if _stable_baseline_window(
                df,
                window_start,
                window_end,
                baseline_value,
                baseline_margin,
                required_minutes,
                sample_seconds,
                idle_profile,
            ):
                found_start = window_end
                break

        found_end = None
        latest_end_window_start = min(
            group_end - confirmation_points + 1,
            e + end_search_points,
        )
        for window_start in range(e + 1, latest_end_window_start + 1):
            window_end = window_start + confirmation_points - 1
            if _stable_baseline_window(
                df,
                window_start,
                window_end,
                baseline_value,
                baseline_margin,
                required_minutes,
                sample_seconds,
                idle_profile,
            ):
                found_end = window_start
                break

        has_complete_start = found_start is not None
        has_complete_end = found_end is not None
        if complete_only and (not has_complete_start or not has_complete_end):
            continue

        start = found_start if has_complete_start else max(0, s - 1)
        if has_complete_end:
            end = found_end
        elif cycle_position == len(cycles_idx) - 1:
            # Preserve the unfinished tail for Live Monitoring only.
            end = len(df) - 1
        else:
            end = e

        if end <= start:
            continue
        refined.append((start, end))

    return refined


def restrict_cycles_to_time_range(
    df,
    cycles_idx,
    start_time,
    stop_time,
):
    """Keep complete cycles inside the requested range and hide query padding."""

    if df is None or df.empty:
        return df, []
    start = pd.Timestamp(start_time)
    stop = pd.Timestamp(stop_time)
    kept = []
    for s, e in cycles_idx or []:
        cycle_start = pd.Timestamp(df.iloc[s]["time"])
        cycle_end = pd.Timestamp(df.iloc[e]["time"])
        if cycle_start >= start and cycle_end < stop:
            kept.append((s, e))

    visible_positions = np.flatnonzero(
        ((df["time"] >= start) & (df["time"] < stop)).to_numpy()
    )
    if len(visible_positions) == 0:
        return df.iloc[0:0].copy(), []
    position_map = {
        int(old_position): new_position
        for new_position, old_position in enumerate(visible_positions)
    }
    visible_df = df.iloc[visible_positions].copy().reset_index(drop=True)
    visible_cycles = [
        (position_map[int(s)], position_map[int(e)])
        for s, e in kept
        if int(s) in position_map and int(e) in position_map
    ]
    return visible_df, visible_cycles


def build_cycle_summary(df, cycles_idx, value_col="smooth"):
    output = []

    for i, (s, e) in enumerate(cycles_idx, start=1):
        cycle_df = df.iloc[s:e+1].copy().reset_index(drop=True)

        source_unit = cycle_df["source_unit"].iloc[0] if "source_unit" in cycle_df.columns else None
        benchmark_unit = cycle_df["benchmark_unit"].iloc[0] if "benchmark_unit" in cycle_df.columns else None
        max_value = float(cycle_df[value_col].max())
        min_value = float(cycle_df[value_col].min())

        # Detection uses canonical bar; summaries are shown in the selected unit.
        if source_unit and benchmark_unit and value_col != "value":
            max_value, min_value = convert_values(
                [max_value, min_value], benchmark_unit, source_unit
            ).tolist()

        output.append({
            "cycle_no": i,
            "start": cycle_df["time"].iloc[0].isoformat(),
            "end": cycle_df["time"].iloc[-1].isoformat(),
            "num_points_original": int(len(cycle_df)),
            "duration_seconds": float(
                (cycle_df["time"].iloc[-1] - cycle_df["time"].iloc[0]).total_seconds()
            ),
            "max_value": float(max_value),
            "min_value": float(min_value),
            "source_unit": source_unit,
            "benchmark_unit": benchmark_unit,
        })

    return output
