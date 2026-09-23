from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.services.influx_service import fetch_multi_field_data, get_pressure_source
from app.services.signal_service import prepare_signal
from app.services.cycle_service import detect_full_cycles, refine_cycle_boundaries
from app.services.sterilizer_mapping import (
    get_auxiliary_channels_for_tag,
    get_sterilizers_for_tag,
)


# -----------------------------------------------------------------------------
# Best-evidence RCA evidence collection
# -----------------------------------------------------------------------------
# This module goes beyond checking whether multiple sterilizers are operating.
# It queries the actual pressure-time values for the selected sterilizer and
# peer sterilizers, then derives evidence from those values:
#
# 1) Peer sterilizer evidence
#    - Which peers are active during the selected cycle/stage window?
#    - What pressure values do they have at that time?
#    - Are multiple peers involved in the same time window?
#
# 2) Competition evidence
#    - Did a peer sterilizer start a pressure ramp during the selected stage
#      window?
#    - This is derived from pressure-time curve transitions/slope, not only
#      static overlap.
#
# 3) Pressure-time pattern evidence
#    - selected stage pressure statistics
#    - peer stage-window pressure statistics
#    - peer ramp starts and max positive slope
#    - summary lines suitable for human-friendly RCA feedback
#
# It does not require parsing Excel again or re-indexing Qdrant.
# -----------------------------------------------------------------------------


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        number = float(value)
        if pd.isna(number):
            return default
        return number
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None

    text = str(value).strip()

    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        pass

    try:
        parsed = pd.to_datetime(text)
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()
    except Exception:
        return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _overlap_seconds(
    a_start: datetime,
    a_end: datetime,
    b_start: datetime,
    b_end: datetime,
) -> float:
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    overlap = (earliest_end - latest_start).total_seconds()
    return max(0.0, overlap)


def _get_peer_fields(
    tag_id: str, selected_field: str, source_unit: str
) -> List[Dict[str, str]]:
    peers: List[Dict[str, str]] = []

    for item in get_sterilizers_for_tag(tag_id, source_unit=source_unit):
        field = str(item.get("field") or "").strip()
        if field and field != selected_field:
            peers.append(
                {
                    "field": field,
                    "sterilizer_name": str(item.get("sterilizer_name") or field),
                }
            )

    return peers


def _get_context_value(
    scoring_result: Dict[str, Any],
    metrics: Dict[str, Any],
    key: str,
) -> Any:
    if key in scoring_result:
        return scoring_result.get(key)

    data_context = scoring_result.get("data_context") or {}
    if key in data_context:
        return data_context.get(key)

    return metrics.get(key)


def _infer_selected_stage_window(
    cycle_start: datetime,
    cycle_end: datetime,
    selected_stage: Optional[str],
    metrics: Dict[str, Any],
) -> Tuple[datetime, datetime, str]:
    """
    Prefer exact stage window from scoring metrics.
    Fall back to a conservative estimated window from cycle fraction.
    """
    stage = str(selected_stage or metrics.get("affected_stage") or "").upper()

    possible_start_keys = [
        f"{stage.lower()}_start_time",
        f"{stage}_start_time",
        "affected_stage_start_time",
        "selected_stage_start_time",
    ]
    possible_end_keys = [
        f"{stage.lower()}_end_time",
        f"{stage}_end_time",
        "affected_stage_end_time",
        "selected_stage_end_time",
    ]

    stage_start = None
    stage_end = None

    for key in possible_start_keys:
        if key in metrics:
            stage_start = _parse_dt(metrics.get(key))
            if stage_start:
                break

    for key in possible_end_keys:
        if key in metrics:
            stage_end = _parse_dt(metrics.get(key))
            if stage_end:
                break

    if stage_start and stage_end and stage_start < stage_end:
        return stage_start, stage_end, "exact_stage_window_from_metrics"

    duration_seconds = max(1.0, (cycle_end - cycle_start).total_seconds())

    # Approximation only when exact stage boundaries are unavailable.
    if stage == "S1":
        start_frac, end_frac = 0.00, 0.35
    elif stage == "S2":
        start_frac, end_frac = 0.20, 0.62
    elif stage == "S3":
        start_frac, end_frac = 0.55, 0.92
    else:
        start_frac, end_frac = 0.00, 1.00

    start = cycle_start + timedelta(seconds=duration_seconds * start_frac)
    end = cycle_start + timedelta(seconds=duration_seconds * end_frac)
    return start, end, "estimated_stage_window_from_cycle_fraction"


def _prepare_field_signal(
    field_df: pd.DataFrame,
    smooth_window: int,
) -> Tuple[pd.DataFrame, float, float, float]:
    """
    Prepare one field/channel and return:
    prepared dataframe, baseline, margin, dynamic range.
    """
    prepared_df, baseline_value, baseline_margin = prepare_signal(
        field_df.copy().sort_values("time").reset_index(drop=True),
        smooth_window=smooth_window,
        value_col="value_std",
    )

    q95 = _safe_float(prepared_df["smooth"].quantile(0.95), 0.0) or 0.0
    q05 = _safe_float(prepared_df["smooth"].quantile(0.05), 0.0) or 0.0
    dynamic_range = max(q95 - q05, 0.000001)

    return prepared_df, float(baseline_value), float(baseline_margin), float(dynamic_range)


def _nearest_value(
    df: pd.DataFrame,
    target_time: datetime,
    value_col: str = "smooth",
) -> Optional[float]:
    if df.empty:
        return None

    times = pd.to_datetime(df["time"])
    idx = (times - pd.Timestamp(target_time)).abs().idxmin()
    if value_col not in df.columns:
        return None
    return _safe_float(df.loc[idx, value_col])


def _window_stats(
    prepared_df: pd.DataFrame,
    window_start: datetime,
    window_end: datetime,
    baseline_value: float,
    baseline_margin: float,
    dynamic_range: float,
) -> Dict[str, Any]:
    """
    Calculate pressure-time statistics within a window.
    These values are the core of the best-evidence layer.
    """
    if prepared_df.empty:
        return {
            "available": False,
            "reason": "No data available for this field.",
        }

    df = prepared_df.copy().sort_values("time").reset_index(drop=True)
    window_df = df[(df["time"] >= window_start) & (df["time"] <= window_end)].copy()

    if window_df.empty:
        return {
            "available": False,
            "reason": "No pressure-time points in the selected window.",
        }

    active_threshold = baseline_value + baseline_margin

    values = window_df["smooth"].astype(float)
    active_mask = values > active_threshold

    # Slope calculation in pressure unit per second.
    window_df["dt_seconds"] = window_df["time"].diff().dt.total_seconds().fillna(0.0)
    window_df["dv"] = window_df["smooth"].diff().fillna(0.0)
    window_df["slope"] = window_df.apply(
        lambda row: row["dv"] / row["dt_seconds"] if row["dt_seconds"] > 0 else 0.0,
        axis=1,
    )

    max_positive_slope = _safe_float(window_df["slope"].max(), 0.0) or 0.0
    mean_positive_slope = _safe_float(window_df.loc[window_df["slope"] > 0, "slope"].mean(), 0.0) or 0.0

    start_value = _nearest_value(df, window_start)
    mid_value = _nearest_value(df, window_start + (window_end - window_start) / 2)
    end_value = _nearest_value(df, window_end)

    source_unit = None
    benchmark_unit = None
    raw_stats: Dict[str, Any] = {}
    if "source_unit" in window_df.columns:
        units = window_df["source_unit"].dropna().astype(str).unique().tolist()
        source_unit = units[0] if units else None
    if "benchmark_unit" in window_df.columns:
        units = window_df["benchmark_unit"].dropna().astype(str).unique().tolist()
        benchmark_unit = units[0] if units else None
    if "value" in window_df.columns:
        raw_values = window_df["value"].astype(float)
        raw_stats = {
            "raw_min_pressure": round(float(raw_values.min()), 4),
            "raw_mean_pressure": round(float(raw_values.mean()), 4),
            "raw_max_pressure": round(float(raw_values.max()), 4),
            "raw_start_pressure": (
                round(_nearest_value(df, window_start, "value"), 4)
                if _nearest_value(df, window_start, "value") is not None
                else None
            ),
            "raw_mid_pressure": (
                round(_nearest_value(df, window_start + (window_end - window_start) / 2, "value"), 4)
                if _nearest_value(df, window_start + (window_end - window_start) / 2, "value") is not None
                else None
            ),
            "raw_end_pressure": (
                round(_nearest_value(df, window_end, "value"), 4)
                if _nearest_value(df, window_end, "value") is not None
                else None
            ),
        }

    return {
        "available": True,
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "point_count": int(len(window_df)),
        "baseline_value": round(float(baseline_value), 4),
        "baseline_margin": round(float(baseline_margin), 4),
        "active_threshold": round(float(active_threshold), 4),
        "dynamic_range": round(float(dynamic_range), 4),
        "min_pressure": round(float(values.min()), 4),
        "mean_pressure": round(float(values.mean()), 4),
        "max_pressure": round(float(values.max()), 4),
        "start_pressure": round(start_value, 4) if start_value is not None else None,
        "mid_pressure": round(mid_value, 4) if mid_value is not None else None,
        "end_pressure": round(end_value, 4) if end_value is not None else None,
        "active_ratio": round(float(active_mask.mean()), 4),
        "active_seconds_estimated": round(float(active_mask.mean()) * max(0.0, (window_end - window_start).total_seconds()), 2),
        "max_positive_slope": round(float(max_positive_slope), 6),
        "mean_positive_slope": round(float(mean_positive_slope), 6),
        "source_unit": source_unit,
        "benchmark_unit": benchmark_unit,
        **raw_stats,
    }


def _pressure_condition_from_stats(stats: Dict[str, Any]) -> str:
    """Describe whether a peer is active and whether its pressure is rising."""
    if not stats.get("available"):
        return "pressure data unavailable"

    active_ratio = _safe_float(stats.get("active_ratio"), 0.0) or 0.0
    if active_ratio >= 0.65:
        activity = "active through most of the affected stage"
    elif active_ratio >= 0.10:
        activity = "partly active during the affected stage"
    else:
        activity = "mostly inactive during the affected stage"

    # Analytical classification stays in canonical bar, independent of display unit.
    start = _safe_float(stats.get("start_pressure"))
    end = _safe_float(stats.get("end_pressure"))
    minimum = _safe_float(stats.get("min_pressure"))
    maximum = _safe_float(stats.get("max_pressure"))

    if start is None or end is None:
        trend = "with an undetermined pressure trend"
    else:
        span = max((maximum or start) - (minimum or start), 0.0)
        tolerance = max(span * 0.08, 0.05)
        change = end - start
        if change > tolerance:
            trend = "with pressure rising"
        elif change < -tolerance:
            trend = "with pressure falling"
        else:
            trend = "with pressure relatively stable"

    return f"{activity}, {trend}"


def _build_peer_pressure_condition(
    stats: Dict[str, Any],
    *,
    active_during_cycle: bool,
    ramp_detected: bool,
    shared_event_detected: bool,
) -> Dict[str, Any]:
    """Build a compact, display-ready peer pressure record."""
    return {
        "field": stats.get("field"),
        "sterilizer_name": stats.get("sterilizer_name"),
        "available": bool(stats.get("available")),
        "window_start": stats.get("window_start"),
        "window_end": stats.get("window_end"),
        "source_unit": stats.get("source_unit"),
        "benchmark_unit": stats.get("benchmark_unit"),
        "min_pressure": stats.get("raw_min_pressure", stats.get("min_pressure")),
        "mean_pressure": stats.get("raw_mean_pressure", stats.get("mean_pressure")),
        "max_pressure": stats.get("raw_max_pressure", stats.get("max_pressure")),
        "start_pressure": stats.get("raw_start_pressure", stats.get("start_pressure")),
        "end_pressure": stats.get("raw_end_pressure", stats.get("end_pressure")),
        "active_ratio": stats.get("active_ratio"),
        "pressure_condition": _pressure_condition_from_stats(stats),
        "active_during_cycle": bool(active_during_cycle),
        "active_during_affected_stage": bool(
            (_safe_float(stats.get("active_ratio"), 0.0) or 0.0) >= 0.10
        ),
        "ramp_detected_during_affected_stage": bool(ramp_detected),
        "shared_pressure_event_detected": bool(shared_event_detected),
    }


def _detect_ramp_events_from_pressure(
    prepared_df: pd.DataFrame,
    window_start: datetime,
    window_end: datetime,
    baseline_value: float,
    baseline_margin: float,
    dynamic_range: float,
    field: str,
    sterilizer_name: str,
) -> List[Dict[str, Any]]:
    """
    Detect ramp-start events using pressure-time values.

    A ramp event is identified when pressure transitions from baseline/low level
    into active rise, or when the positive slope is large enough during the
    checked window. This is stronger than simply checking if a peer is active.
    """
    if prepared_df.empty:
        return []

    df = prepared_df.copy().sort_values("time").reset_index(drop=True)
    df = df[(df["time"] >= window_start) & (df["time"] <= window_end)].copy()

    if len(df) < 4:
        return []

    active_threshold = baseline_value + baseline_margin
    low_threshold = baseline_value + baseline_margin * 0.50

    df["dt_seconds"] = df["time"].diff().dt.total_seconds().fillna(0.0)
    df["dv"] = df["smooth"].diff().fillna(0.0)
    df["slope"] = df.apply(
        lambda row: row["dv"] / row["dt_seconds"] if row["dt_seconds"] > 0 else 0.0,
        axis=1,
    )

    # Dynamic slope threshold. A typical full ramp may rise across much of the
    # range over 10-30 minutes, so this captures meaningful pressure rise while
    # avoiding small noise spikes.
    slope_threshold = max(dynamic_range / 1800.0, 0.00035)

    events: List[Dict[str, Any]] = []
    was_low = True

    for idx, row in df.iterrows():
        pressure = float(row["smooth"])
        display_pressure = (
            float(row["value"])
            if "value" in row and pd.notna(row["value"])
            else pressure
        )
        slope = float(row["slope"])
        current_time = row["time"]

        # Confirm ramp start by actual pressure transition or strong slope.
        transitioned_to_active = was_low and pressure >= active_threshold
        strong_rise_from_low_area = pressure >= low_threshold and slope >= slope_threshold

        if transitioned_to_active or strong_rise_from_low_area:
            # Check local pressure increase over nearby points to avoid a single
            # noisy point being treated as ramp.
            local = df.iloc[max(0, idx - 2) : min(len(df), idx + 5)]
            local_increase = float(local["smooth"].max() - local["smooth"].min())
            display_local_increase = (
                float(local["value"].max() - local["value"].min())
                if "value" in local.columns and not local["value"].dropna().empty
                else local_increase
            )

            if local_increase >= max(dynamic_range * 0.08, baseline_margin * 0.35):
                events.append(
                    {
                        "field": field,
                        "sterilizer_name": sterilizer_name,
                        "ramp_start_time": current_time.isoformat(),
                        "ramp_start_pressure": round(display_pressure, 4),
                        "slope_at_start": round(slope, 6),
                        "local_pressure_increase": round(display_local_increase, 4),
                        "pressure_unit": (
                            str(df["source_unit"].dropna().iloc[0])
                            if "source_unit" in df.columns and not df["source_unit"].dropna().empty
                            else str(df["benchmark_unit"].dropna().iloc[0])
                            if "benchmark_unit" in df.columns and not df["benchmark_unit"].dropna().empty
                            else None
                        ),
                        "detection_method": "pressure_transition_or_positive_slope",
                    }
                )
                # Avoid reporting many consecutive points as separate starts.
                was_low = False
                continue

        if pressure <= low_threshold:
            was_low = True

    # Dedupe events that occur very close together for the same field.
    deduped: List[Dict[str, Any]] = []
    last_time: Optional[datetime] = None
    for event in events:
        event_time = _parse_dt(event.get("ramp_start_time"))
        if last_time and event_time and abs((event_time - last_time).total_seconds()) < 180:
            continue
        deduped.append(event)
        if event_time:
            last_time = event_time

    return deduped


def _build_cycle_records_for_field(
    prepared_df: pd.DataFrame,
    baseline_value: float,
    baseline_margin: float,
    dynamic_range: float,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    try:
        detected_df, cycles_idx, _threshold = detect_full_cycles(
            prepared_df.copy(),
            baseline_value,
            baseline_margin,
        )
        cycles_idx = refine_cycle_boundaries(
            detected_df,
            cycles_idx,
            baseline_value,
            baseline_margin,
        )
    except Exception:
        return records

    for idx, (s, e) in enumerate(cycles_idx, start=1):
        cycle_df = detected_df.iloc[s : e + 1].copy()
        if cycle_df.empty:
            continue

        start_time = cycle_df["time"].iloc[0]
        end_time = cycle_df["time"].iloc[-1]
        stats = _window_stats(
            cycle_df,
            start_time,
            end_time,
            baseline_value,
            baseline_margin,
            dynamic_range,
        )

        records.append(
            {
                "peer_cycle_no": idx,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "duration_seconds": float((end_time - start_time).total_seconds()),
                "peak_value": _safe_float(cycle_df["smooth"].max(), 0.0),
                "min_value": _safe_float(cycle_df["smooth"].min(), 0.0),
                "cycle_pressure_stats": stats,
            }
        )

    return records


def _dedupe(items: List[Dict[str, Any]], key_names: Tuple[str, ...]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []

    for item in items:
        key = tuple(item.get(k) for k in key_names)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)

    return out


def collect_peer_competition_evidence(scoring_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect best evidence for RCA confirmation.

    Required context, usually passed from frontend through scoring_result:
    - bucket, measurement, tag_id, field
    - cycle_start, cycle_end
    - source_unit, smooth_window optional

    Output is safe: if data/context is missing, available=False is returned and
    the RCA pipeline continues without crashing.
    """

    metrics = scoring_result.get("metrics") or {}

    requested_bucket = _get_context_value(scoring_result, metrics, "bucket")
    tag_id = _get_context_value(scoring_result, metrics, "tag_id")
    selected_field = _get_context_value(scoring_result, metrics, "field")
    source_unit = _get_context_value(scoring_result, metrics, "source_unit") or "bar"
    smooth_window = int(_safe_float(_get_context_value(scoring_result, metrics, "smooth_window"), 9) or 9)

    cycle_start = _parse_dt(_get_context_value(scoring_result, metrics, "cycle_start"))
    cycle_end = _parse_dt(_get_context_value(scoring_result, metrics, "cycle_end"))
    selected_stage = metrics.get("user_selected_stage") or metrics.get("affected_stage")

    try:
        source = get_pressure_source(source_unit, requested_bucket=requested_bucket)
        bucket = source["bucket"]
        measurement = source["measurement"]
        source_unit = source["source_unit"]
    except Exception as exc:
        return {
            "available": False,
            "evidence_level": "not_available",
            "reason": f"Evidence source configuration is unavailable: {exc}",
            "summary_lines": [f"Evidence source configuration is unavailable: {exc}"],
            "selected_context": {
                "tag_id": tag_id,
                "field": selected_field,
                "source_unit": source_unit,
            },
            "selected_pressure_time_evidence": {},
            "auxiliary_pressure_evidence": {},
            "peer_sterilizer_evidence": {},
            "competition_evidence": {},
            "pressure_time_evidence": {},
            "confirmation_flags": {},
        }

    evidence: Dict[str, Any] = {
        "available": False,
        "evidence_level": "not_available",
        "reason": None,
        "selected_context": {
            "bucket": bucket,
            "measurement": measurement,
            "source_unit": source_unit,
            "tag_id": tag_id,
            "field": selected_field,
            "cycle_start": _iso(cycle_start),
            "cycle_end": _iso(cycle_end),
            "selected_stage": selected_stage,
        },
        "selected_pressure_time_evidence": {},
        "auxiliary_pressure_evidence": {},
        "peer_sterilizer_evidence": {
            "active_peer_count": 0,
            "active_peers": [],
            "overlapping_peer_count": 0,
            "overlapping_peers": [],
            "affected_stage_pressure_conditions": [],
        },
        "competition_evidence": {
            "confirmed": False,
            "concurrent_ramp_count": 0,
            "concurrent_ramp_peers": [],
            "window_minutes": 5,
            "pressure_value_based": True,
        },
        "pressure_time_evidence": {
            "peer_window_stats": [],
            "peer_ramp_events": [],
            "shared_pressure_event_count": 0,
            "shared_pressure_event_peers": [],
        },
        "confirmation_flags": {
            "pressure_value_evidence_available": False,
            "peer_confirmation_available": False,
            "competition_confirmation_available": False,
            "local_confirmation_available": False,
            "boiler_or_system_confirmation_available": False,
            "boiler_pressure_evidence_available": False,
            "bpv_pressure_evidence_available": False,
        },
        "summary_lines": [],
        "debug": {},
    }

    missing = [
        name
        for name, value in {
            "tag_id": tag_id,
            "field": selected_field,
            "cycle_start": cycle_start,
            "cycle_end": cycle_end,
        }.items()
        if not value
    ]

    if missing:
        evidence["reason"] = f"Evidence collection skipped because context is missing: {', '.join(missing)}."
        evidence["summary_lines"].append(evidence["reason"])
        return evidence

    peer_items = _get_peer_fields(str(tag_id), str(selected_field), source_unit)
    peer_fields = [item["field"] for item in peer_items]
    peer_name_by_field = {item["field"]: item["sterilizer_name"] for item in peer_items}
    auxiliary_channels = get_auxiliary_channels_for_tag(
        str(tag_id), source_unit=source_unit
    )
    auxiliary_fields = [
        str(item.get("field"))
        for item in auxiliary_channels.values()
        if item.get("field")
    ]

    selected_stage_start, selected_stage_end, window_source = _infer_selected_stage_window(
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        selected_stage=selected_stage,
        metrics=metrics,
    )

    query_start = cycle_start - timedelta(minutes=30)
    query_end = cycle_end + timedelta(minutes=30)

    all_fields = list(dict.fromkeys([str(selected_field)] + peer_fields + auxiliary_fields))

    evidence["selected_context"].update(
        {
            "selected_stage_window_start": _iso(selected_stage_start),
            "selected_stage_window_end": _iso(selected_stage_end),
            "selected_stage_window_source": window_source,
            "peer_fields_checked": peer_fields,
            "auxiliary_fields_checked": auxiliary_fields,
            "query_start": _iso(query_start),
            "query_end": _iso(query_end),
        }
    )

    try:
        all_df = fetch_multi_field_data(
            bucket=bucket,
            measurement=measurement,
            fields=all_fields,
            tag_id=tag_id,
            start_time=query_start.isoformat(),
            stop_time=query_end.isoformat(),
            source_unit_fallback=source_unit,
        )
    except Exception as exc:
        evidence["available"] = False
        evidence["reason"] = f"Failed to fetch selected/peer sterilizer data: {exc}"
        evidence["summary_lines"].append(evidence["reason"])
        return evidence

    if all_df.empty:
        evidence["available"] = False
        evidence["reason"] = "No pressure-time data returned for selected or peer sterilizers."
        evidence["summary_lines"].append(evidence["reason"])
        return evidence

    evidence["available"] = True
    evidence["evidence_level"] = "pressure_time_value_analysis"

    prepared_by_field: Dict[str, Dict[str, Any]] = {}

    for field, field_df in all_df.groupby("field", sort=False):
        try:
            prepared_df, baseline, margin, dynamic_range = _prepare_field_signal(
                field_df,
                smooth_window=smooth_window,
            )
            prepared_by_field[str(field)] = {
                "prepared_df": prepared_df,
                "baseline": baseline,
                "margin": margin,
                "dynamic_range": dynamic_range,
            }
        except Exception as exc:
            prepared_by_field[str(field)] = {
                "error": str(exc),
            }

    selected_prepared = prepared_by_field.get(str(selected_field)) or {}
    if selected_prepared.get("prepared_df") is not None:
        selected_stats = _window_stats(
            selected_prepared["prepared_df"],
            selected_stage_start,
            selected_stage_end,
            selected_prepared["baseline"],
            selected_prepared["margin"],
            selected_prepared["dynamic_range"],
        )
        evidence["selected_pressure_time_evidence"] = {
            "field": selected_field,
            "stage_window_stats": selected_stats,
        }

    auxiliary_pressure_evidence: Dict[str, Dict[str, Any]] = {}
    for cause_key, channel in auxiliary_channels.items():
        auxiliary_field = str(channel.get("field") or "")
        display_name = str(channel.get("display_name") or cause_key.upper())
        auxiliary_data = prepared_by_field.get(auxiliary_field) or {}
        auxiliary_df = auxiliary_data.get("prepared_df")

        if auxiliary_df is None:
            auxiliary_pressure_evidence[cause_key] = {
                "field": auxiliary_field,
                "display_name": display_name,
                "available": False,
                "reason": auxiliary_data.get("error") or "No pressure data was available for this equipment channel.",
            }
            continue

        auxiliary_stats = _window_stats(
            auxiliary_df,
            selected_stage_start,
            selected_stage_end,
            auxiliary_data["baseline"],
            auxiliary_data["margin"],
            auxiliary_data["dynamic_range"],
        )
        auxiliary_pressure_evidence[cause_key] = {
            "field": auxiliary_field,
            "display_name": display_name,
            "available": bool(auxiliary_stats.get("available")),
            "stage_window_stats": auxiliary_stats,
            "pressure_condition": _pressure_condition_from_stats(auxiliary_stats),
            "interpretation": "raw_observation_only_no_reference_threshold",
        }

    evidence["auxiliary_pressure_evidence"] = auxiliary_pressure_evidence

    active_peers: List[Dict[str, Any]] = []
    overlapping_peers: List[Dict[str, Any]] = []
    concurrent_ramp_peers: List[Dict[str, Any]] = []
    peer_window_stats: List[Dict[str, Any]] = []
    peer_ramp_events: List[Dict[str, Any]] = []
    shared_pressure_event_peers: List[Dict[str, Any]] = []

    competition_margin = timedelta(minutes=5)
    competition_start = selected_stage_start - competition_margin
    competition_end = selected_stage_end + competition_margin

    for peer_field in peer_fields:
        peer_name = peer_name_by_field.get(peer_field, peer_field)
        peer_data = prepared_by_field.get(peer_field) or {}
        prepared_df = peer_data.get("prepared_df")

        if prepared_df is None:
            peer_window_stats.append(
                {
                    "field": peer_field,
                    "sterilizer_name": peer_name,
                    "available": False,
                    "reason": peer_data.get("error") or "No prepared data for peer field.",
                }
            )
            continue

        baseline = peer_data["baseline"]
        margin = peer_data["margin"]
        dynamic_range = peer_data["dynamic_range"]

        stats = _window_stats(
            prepared_df,
            selected_stage_start,
            selected_stage_end,
            baseline,
            margin,
            dynamic_range,
        )
        stats_with_peer = {
            "field": peer_field,
            "sterilizer_name": peer_name,
            **stats,
        }
        peer_window_stats.append(stats_with_peer)

        if stats.get("available") and _safe_float(stats.get("active_ratio"), 0.0) >= 0.10:
            overlapping_peers.append(
                {
                    "field": peer_field,
                    "sterilizer_name": peer_name,
                    "stage_overlap_seconds_estimated": stats.get("active_seconds_estimated"),
                    "mean_pressure": stats.get("raw_mean_pressure", stats.get("mean_pressure")),
                    "max_pressure": stats.get("raw_max_pressure", stats.get("max_pressure")),
                    "source_unit": stats.get("source_unit"),
                    "benchmark_unit": stats.get("benchmark_unit"),
                    "active_ratio": stats.get("active_ratio"),
                    "evidence_source": "pressure_time_values_in_selected_stage_window",
                }
            )

        # Broader active evidence over the whole selected cycle.
        cycle_stats = _window_stats(
            prepared_df,
            cycle_start,
            cycle_end,
            baseline,
            margin,
            dynamic_range,
        )
        if cycle_stats.get("available") and _safe_float(cycle_stats.get("active_ratio"), 0.0) >= 0.10:
            active_peers.append(
                {
                    "field": peer_field,
                    "sterilizer_name": peer_name,
                    "active_seconds_estimated": cycle_stats.get("active_seconds_estimated"),
                    "mean_pressure": cycle_stats.get("raw_mean_pressure", cycle_stats.get("mean_pressure")),
                    "max_pressure": cycle_stats.get("raw_max_pressure", cycle_stats.get("max_pressure")),
                    "source_unit": cycle_stats.get("source_unit"),
                    "benchmark_unit": cycle_stats.get("benchmark_unit"),
                    "active_ratio": cycle_stats.get("active_ratio"),
                    "evidence_source": "pressure_time_values_in_selected_cycle_window",
                }
            )

        # Competition evidence using pressure-time ramp events.
        ramp_events = _detect_ramp_events_from_pressure(
            prepared_df=prepared_df,
            window_start=competition_start,
            window_end=competition_end,
            baseline_value=baseline,
            baseline_margin=margin,
            dynamic_range=dynamic_range,
            field=peer_field,
            sterilizer_name=peer_name,
        )
        for event in ramp_events:
            event_time = _parse_dt(event.get("ramp_start_time"))
            if event_time and selected_stage_start <= event_time <= selected_stage_end:
                event["selected_stage_window_start"] = _iso(selected_stage_start)
                event["selected_stage_window_end"] = _iso(selected_stage_end)
                event["minutes_from_stage_start"] = round(
                    (event_time - selected_stage_start).total_seconds() / 60.0,
                    2,
                )
                concurrent_ramp_peers.append(event)

            peer_ramp_events.append(event)

        # Shared pressure event: peer is active in selected stage and either has a
        # strong positive slope or large pressure variation. This supports shared
        # demand/system evidence, not just static overlap.
        if stats.get("available"):
            active_ratio = _safe_float(stats.get("active_ratio"), 0.0) or 0.0
            max_slope = _safe_float(stats.get("max_positive_slope"), 0.0) or 0.0
            pressure_span = (_safe_float(stats.get("max_pressure"), 0.0) or 0.0) - (_safe_float(stats.get("min_pressure"), 0.0) or 0.0)
            slope_threshold = max(dynamic_range / 1800.0, 0.00035)

            if active_ratio >= 0.15 and (max_slope >= slope_threshold or pressure_span >= max(dynamic_range * 0.20, margin)):
                shared_pressure_event_peers.append(
                    {
                        "field": peer_field,
                        "sterilizer_name": peer_name,
                        "active_ratio": round(active_ratio, 4),
                        "max_positive_slope": round(max_slope, 6),
                        "pressure_span": round(pressure_span, 4),
                        "evidence_source": "peer_pressure_time_pattern_in_selected_stage_window",
                    }
                )

    active_peers = _dedupe(active_peers, ("field",))
    overlapping_peers = _dedupe(overlapping_peers, ("field",))
    concurrent_ramp_peers = _dedupe(concurrent_ramp_peers, ("field", "ramp_start_time"))
    shared_pressure_event_peers = _dedupe(shared_pressure_event_peers, ("field",))

    active_peer_count = len(active_peers)
    overlapping_peer_count = len(overlapping_peers)
    concurrent_ramp_count = len({item.get("field") for item in concurrent_ramp_peers})
    shared_pressure_event_count = len(shared_pressure_event_peers)

    active_fields = {str(item.get("field")) for item in active_peers}
    ramp_fields = {str(item.get("field")) for item in concurrent_ramp_peers}
    shared_fields = {str(item.get("field")) for item in shared_pressure_event_peers}

    peer_pressure_conditions: List[Dict[str, Any]] = []
    for peer_stats in peer_window_stats:
        if not peer_stats.get("available"):
            continue
        field = str(peer_stats.get("field") or "")
        condition = _build_peer_pressure_condition(
            peer_stats,
            active_during_cycle=field in active_fields,
            ramp_detected=field in ramp_fields,
            shared_event_detected=field in shared_fields,
        )
        # Keep peers that were active in the selected cycle or affected stage,
        # or that produced a shared/ramp event. These are the peers relevant to
        # the operator's explanation.
        if (
            condition["active_during_cycle"]
            or condition["active_during_affected_stage"]
            or condition["ramp_detected_during_affected_stage"]
            or condition["shared_pressure_event_detected"]
        ):
            peer_pressure_conditions.append(condition)

    evidence["peer_sterilizer_evidence"] = {
        "active_peer_count": active_peer_count,
        "active_peers": active_peers,
        "overlapping_peer_count": overlapping_peer_count,
        "overlapping_peers": overlapping_peers,
        "affected_stage_pressure_conditions": peer_pressure_conditions,
    }

    evidence["competition_evidence"] = {
        "confirmed": concurrent_ramp_count >= 1,
        "concurrent_ramp_count": concurrent_ramp_count,
        "concurrent_ramp_peers": concurrent_ramp_peers,
        "competitor_pressure_conditions": [
            item
            for item in peer_pressure_conditions
            if item.get("active_during_affected_stage")
            or item.get("ramp_detected_during_affected_stage")
        ],
        "window_minutes": 5,
        "pressure_value_based": True,
    }

    evidence["pressure_time_evidence"] = {
        "peer_window_stats": peer_window_stats,
        "peer_ramp_events": peer_ramp_events,
        "shared_pressure_event_count": shared_pressure_event_count,
        "shared_pressure_event_peers": shared_pressure_event_peers,
    }

    pressure_value_evidence_available = bool(
        evidence.get("selected_pressure_time_evidence", {}).get("stage_window_stats", {}).get("available")
        or any(item.get("available") for item in peer_window_stats)
        or any(item.get("available") for item in auxiliary_pressure_evidence.values())
    )

    boiler_pressure_available = bool(
        auxiliary_pressure_evidence.get("boiler", {}).get("available")
    )
    bpv_pressure_available = bool(
        auxiliary_pressure_evidence.get("bpv", {}).get("available")
    )

    evidence["confirmation_flags"] = {
        "pressure_value_evidence_available": pressure_value_evidence_available,
        "peer_confirmation_available": overlapping_peer_count >= 1 or active_peer_count >= 2,
        "competition_confirmation_available": concurrent_ramp_count >= 1,
        "local_confirmation_available": active_peer_count == 0 and concurrent_ramp_count == 0 and shared_pressure_event_count == 0,
        "boiler_or_system_confirmation_available": shared_pressure_event_count >= 2 or overlapping_peer_count >= 2,
        "boiler_pressure_evidence_available": boiler_pressure_available,
        "bpv_pressure_evidence_available": bpv_pressure_available,
    }

    selected_stats = evidence.get("selected_pressure_time_evidence", {}).get("stage_window_stats") or {}
    if selected_stats.get("available"):
        selected_unit = selected_stats.get("source_unit") or selected_stats.get("benchmark_unit") or "pressure units"
        selected_min = selected_stats.get("raw_min_pressure", selected_stats.get("min_pressure"))
        selected_max = selected_stats.get("raw_max_pressure", selected_stats.get("max_pressure"))
        selected_mean = selected_stats.get("raw_mean_pressure", selected_stats.get("mean_pressure"))
        evidence["summary_lines"].append(
            "Selected pressure-time evidence: "
            f"during the selected stage window, pressure ranged from {selected_min} {selected_unit} "
            f"to {selected_max} {selected_unit} with mean {selected_mean} {selected_unit}."
        )

    if active_peer_count > 0:
        names = ", ".join(sorted({str(item.get("sterilizer_name")) for item in active_peers}))
        evidence["summary_lines"].append(
            f"Peer evidence: {active_peer_count} peer sterilizer(s) had pressure-time activity during the selected cycle: {names}."
        )
    else:
        evidence["summary_lines"].append(
            "Peer evidence: no configured peer sterilizer showed pressure-time activity during the selected cycle window."
        )

    if overlapping_peer_count > 0:
        names = ", ".join(sorted({str(item.get("sterilizer_name")) for item in overlapping_peers}))
        evidence["summary_lines"].append(
            f"Stage-window peer evidence: {overlapping_peer_count} peer sterilizer(s) had pressure values above baseline during the selected stage window: {names}."
        )

    for condition in peer_pressure_conditions[:4]:
        unit = condition.get("source_unit") or condition.get("benchmark_unit") or "pressure units"
        evidence["summary_lines"].append(
            f"Peer pressure condition: {condition.get('sterilizer_name')} was "
            f"{condition.get('pressure_condition')} from {condition.get('window_start')} "
            f"to {condition.get('window_end')}; pressure minimum {condition.get('min_pressure')} {unit}, "
            f"mean {condition.get('mean_pressure')} {unit}, maximum {condition.get('max_pressure')} {unit}, "
            f"start {condition.get('start_pressure')} {unit}, and end {condition.get('end_pressure')} {unit}."
        )

    if concurrent_ramp_count > 0:
        names = ", ".join(sorted({str(item.get("sterilizer_name")) for item in concurrent_ramp_peers}))
        evidence["summary_lines"].append(
            f"Competition evidence: {concurrent_ramp_count} peer sterilizer(s) showed a pressure-time ramp start during the selected stage window: {names}."
        )
    else:
        evidence["summary_lines"].append(
            "Competition evidence: no peer pressure-time ramp start was detected during the selected stage window."
        )

    if shared_pressure_event_count > 0:
        names = ", ".join(sorted({str(item.get("sterilizer_name")) for item in shared_pressure_event_peers}))
        evidence["summary_lines"].append(
            f"Shared pressure-time evidence: {shared_pressure_event_count} peer sterilizer(s) had notable pressure movement during the selected stage window: {names}."
        )

    evidence["debug"]["fields_checked"] = all_fields
    evidence["debug"]["pressure_time_basis"] = "value_std/smooth pressure values from InfluxDB"

    return evidence
