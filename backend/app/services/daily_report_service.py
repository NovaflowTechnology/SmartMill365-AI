"""Generate plant-specific Daily Insight Reports from verified cycle scores."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from app.services.benchmark_service import (
    load_benchmark,
    validate_benchmark_compatibility,
)
from app.services.comparison_service import compare_detected_cycles_to_benchmark
from app.services.cycle_service import detect_full_cycles, refine_cycle_boundaries
from app.services.daily_report_settings_service import (
    derive_site_code,
    get_plant_display_name,
    get_site_display_name,
    get_site_shifts,
)
from app.services.influx_service import fetch_multi_field_data, get_pressure_source
from app.services.signal_service import prepare_signal
from app.services.sterilizer_mapping import get_sterilizers_for_tag
from app.config import (
    CYCLE_QUERY_PADDING_MINUTES,
    DAILY_REPORT_CYCLE_LOOKBACK_MINUTES,
    PLANT_TIMEZONE,
)
from app.services.timezone_service import get_plant_timezone


REPORT_TIMEZONE = get_plant_timezone(PLANT_TIMEZONE)
SCORE_BANDS = (
    ("excellent", "Excellent", 90.0, float("inf")),
    ("good", "Good", 75.0, 90.0),
    ("fair", "Fair", 60.0, 75.0),
    ("poor", "Poor", 50.0, 60.0),
    ("critical", "Critical", float("-inf"), 50.0),
)
STAGE_KEYS = ("s1", "s2", "s3")
CYCLE_DETECTION_LOOKBACK = timedelta(
    minutes=DAILY_REPORT_CYCLE_LOOKBACK_MINUTES
)
DATA_AVAILABILITY_GAP = timedelta(minutes=30)
DATA_QUALITY_ISSUE_LABELS = {
    "too_few_points": "Too few pressure readings",
    "missing_or_nan_values": "Missing or invalid pressure values",
    "flatline_signal": "Possible flatline sensor signal",
    "irregular_or_missing_time_gaps": "Irregular or missing timestamp gaps",
    "possible_spikes_or_signal_noise": "Possible pressure spikes or signal noise",
    "timestamp_quality_check_failed": "Timestamp quality check could not be completed",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _parse_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _next_clock_after(moment: datetime, clock_value: str) -> datetime:
    candidate = datetime.combine(
        moment.date(),
        _parse_clock(clock_value),
        moment.tzinfo or REPORT_TIMEZONE,
    )
    if candidate <= moment:
        candidate += timedelta(days=1)
    return candidate


def build_operational_window(
    report_date: date,
    shifts: Dict[str, Any],
    report_timezone=REPORT_TIMEZONE,
) -> Dict[str, Any]:
    """Build the two continuous shift intervals for one operational date."""

    morning = shifts["morning"]
    night = shifts["night"]
    morning_start = datetime.combine(
        report_date, _parse_clock(morning["start"]), report_timezone
    )
    morning_end = _next_clock_after(morning_start, morning["end"])
    night_start = morning_end
    night_end = _next_clock_after(night_start, night["end"])

    return {
        "report_date": report_date.isoformat(),
        "start": morning_start,
        "end": night_end,
        "morning": {
            "key": "morning",
            "label": morning.get("label") or "Morning Shift",
            "configured_start": morning["start"],
            "configured_end": morning["end"],
            "start": morning_start,
            "end": morning_end,
        },
        "night": {
            "key": "night",
            "label": night.get("label") or "Night Shift",
            "configured_start": night["start"],
            "configured_end": night["end"],
            "start": night_start,
            "end": night_end,
        },
    }


def assign_shift_by_cycle_end(
    cycle_end: datetime, operational_window: Dict[str, Any]
) -> Optional[str]:
    """Assign a cycle using its end timestamp and half-open shift boundaries."""

    report_timezone = operational_window["start"].tzinfo or REPORT_TIMEZONE
    if cycle_end.tzinfo is None:
        cycle_end = cycle_end.replace(tzinfo=report_timezone)
    else:
        cycle_end = cycle_end.astimezone(report_timezone)

    for shift_key in ("morning", "night"):
        shift = operational_window[shift_key]
        if shift["start"] <= cycle_end < shift["end"]:
            return shift_key
    return None


def score_band(score: float) -> str:
    value = float(score)
    for key, _label, minimum, maximum in SCORE_BANDS:
        if minimum <= value < maximum:
            return key
    return "critical"


def _average(values: Iterable[Optional[float]]) -> Optional[float]:
    clean_values = [float(value) for value in values if value is not None]
    return mean(clean_values) if clean_values else None


def _band_summary(cycles: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    total = len(cycles)
    result = {}
    for key, label, minimum, maximum in SCORE_BANDS:
        count = sum(
            1 for cycle in cycles if minimum <= float(cycle["score"]) < maximum
        )
        result[key] = {
            "label": label,
            "count": count,
            "percentage": _round((count / total * 100.0) if total else 0.0),
        }
    return result


def _performance_status(average_score: Optional[float]) -> str:
    if average_score is None:
        return "NO DATA"
    if average_score >= 75:
        return "GOOD"
    if average_score >= 60:
        return "ATTENTION"
    return "CRITICAL"


def _reliability_summary(cycles: List[Dict[str, Any]]) -> Dict[str, Any]:
    scores = [float(cycle["score"]) for cycle in cycles]
    if not scores:
        return {
            "average_score": None,
            "standard_deviation": None,
            "best_score": None,
            "worst_score": None,
            "score_range": None,
            "cv_percent": None,
            "rating": "NO DATA",
        }

    average_score = mean(scores)
    standard_deviation = pstdev(scores) if len(scores) > 1 else 0.0
    cv_percent = (
        standard_deviation / average_score * 100.0 if average_score > 0 else None
    )
    if len(scores) < 2:
        rating = "INSUFFICIENT"
    elif cv_percent is not None and cv_percent <= 10:
        rating = "STABLE"
    elif cv_percent is not None and cv_percent <= 20:
        rating = "VARIABLE"
    else:
        rating = "UNSTABLE"

    best_score = max(scores)
    worst_score = min(scores)
    return {
        "average_score": _round(average_score),
        "standard_deviation": _round(standard_deviation),
        "best_score": _round(best_score),
        "worst_score": _round(worst_score),
        "score_range": f"{best_score:.1f} – {worst_score:.1f}",
        "cv_percent": _round(cv_percent),
        "rating": rating,
    }


def _stage_pressure_deviation(cycle: Dict[str, Any], stage_key: str) -> Optional[float]:
    """Return signed mean actual-minus-benchmark pressure in canonical bar."""

    metrics = cycle.get("metrics") or {}
    boundaries = metrics.get("stage_boundaries") or {}
    start = boundaries.get(f"{stage_key}_start_progress")
    end = boundaries.get(f"{stage_key}_end_progress")
    overlay = cycle.get("overlay_chart") or []
    if start is None or end is None or not overlay:
        return None

    start_percent = float(start) * 100.0
    end_percent = float(end) * 100.0
    differences = []
    for row in overlay:
        progress = row.get("progress")
        actual = row.get("realtime")
        benchmark = row.get("benchmark")
        if progress is None or actual is None or benchmark is None:
            continue
        if start_percent <= float(progress) <= end_percent:
            differences.append(float(actual) - float(benchmark))
    return _average(differences)


def _compact_cycle(
    cycle: Dict[str, Any], field: str, sterilizer_name: str, shift_key: str
) -> Dict[str, Any]:
    score_breakdown = cycle.get("score_breakdown") or {}
    metrics = cycle.get("metrics") or {}
    quality_issues = [
        str(item) for item in (metrics.get("data_quality_issues") or []) if str(item)
    ]
    return {
        "cycle_no": int(cycle.get("cycle_no") or 0),
        "field": field,
        "sterilizer_name": sterilizer_name,
        "cycle_start": cycle.get("cycle_start"),
        "cycle_end": cycle.get("cycle_end"),
        "duration_seconds": _round(cycle.get("duration_seconds"), 0),
        "shift": shift_key,
        "score": _round(cycle.get("score"), 2),
        "score_band": cycle.get("score_band") or score_band(cycle.get("score") or 0),
        "stage_scores": {
            stage_key: _round(score_breakdown.get(f"{stage_key}_score"), 2)
            for stage_key in STAGE_KEYS
        },
        "stage_deviation_bar": {
            stage_key: _round(_stage_pressure_deviation(cycle, stage_key), 3)
            for stage_key in STAGE_KEYS
        },
        "affected_stage": metrics.get("affected_stage"),
        "data_quality": {
            "status": "warning" if quality_issues else "valid",
            "score": _round(metrics.get("data_quality_score"), 1),
            "issues": quality_issues,
            "issue_labels": [
                DATA_QUALITY_ISSUE_LABELS.get(item, item.replace("_", " ").title())
                for item in quality_issues
            ],
            "data_point_count": metrics.get("data_point_count"),
            "gap_ratio": _round(metrics.get("data_quality_gap_ratio"), 3),
            "spike_ratio": _round(metrics.get("data_quality_spike_ratio"), 3),
        },
    }


def _summarise_cycles(cycles: List[Dict[str, Any]]) -> Dict[str, Any]:
    scores = [float(cycle["score"]) for cycle in cycles]
    average_score = _average(scores)
    return {
        "total_cycles": len(cycles),
        "average_score": _round(average_score),
        "best_score": _round(max(scores)) if scores else None,
        "worst_score": _round(min(scores)) if scores else None,
        "bands": _band_summary(cycles),
        "status": _performance_status(average_score),
        "data_quality_warning_cycles": sum(
            1
            for cycle in cycles
            if (cycle.get("data_quality") or {}).get("status") == "warning"
        ),
    }


def _summarise_sterilizer(
    field: str,
    sterilizer_name: str,
    cycles: List[Dict[str, Any]],
    benchmark_file_name: str,
    benchmark_name: str,
) -> Dict[str, Any]:
    summary = _summarise_cycles(cycles)
    stage_performance = {}
    for stage_key in STAGE_KEYS:
        stage_performance[stage_key] = {
            "average_score": _round(
                _average(cycle["stage_scores"].get(stage_key) for cycle in cycles)
            ),
            "average_deviation_bar": _round(
                _average(
                    cycle["stage_deviation_bar"].get(stage_key) for cycle in cycles
                ),
                3,
            ),
        }

    return {
        "field": field,
        "sterilizer_name": sterilizer_name,
        "benchmark_file_name": benchmark_file_name,
        "benchmark_name": benchmark_name,
        **summary,
        "stage_performance": stage_performance,
        "reliability": _reliability_summary(cycles),
    }


def _difference(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    return _round(float(current) - float(previous))


def _build_insights(
    site_name: str,
    current_summary: Dict[str, Any],
    sterilizers: List[Dict[str, Any]],
    shift_summary: Dict[str, Dict[str, Any]],
) -> List[Dict[str, str]]:
    insights: List[Dict[str, str]] = []
    average_score = current_summary.get("average_score")
    total_cycles = current_summary.get("total_cycles") or 0
    if average_score is None:
        return [
            {
                "key": "no_data",
                "title": "No Completed Cycles",
                "text": f"No complete sterilizer cycle ended within the selected {site_name} report period.",
            }
        ]

    insights.append(
        {
            "key": "overall",
            "title": "Overall Performance",
            "text": (
                f"{site_name} completed {total_cycles} cycles with an average "
                f"score of {average_score:.1f} during the selected operational day."
            ),
        }
    )

    active_sterilizers = [item for item in sterilizers if item["average_score"] is not None]
    if active_sterilizers:
        worst = min(active_sterilizers, key=lambda item: item["average_score"])
        critical_count = worst["bands"]["critical"]["count"]
        insights.append(
            {
                "key": "underperformance",
                "title": f"{worst['sterilizer_name']} Performance",
                "text": (
                    f"{worst['sterilizer_name']} recorded the lowest average score "
                    f"({worst['average_score']:.1f}) and {critical_count} critical cycles."
                ),
            }
        )

        least_reliable = max(
            active_sterilizers,
            key=lambda item: item["reliability"].get("cv_percent") or 0,
        )
        reliability = least_reliable["reliability"]
        if reliability.get("cv_percent") is not None:
            insights.append(
                {
                    "key": "reliability",
                    "title": "Reliability / Stability",
                    "text": (
                        f"{least_reliable['sterilizer_name']} has the highest score variability "
                        f"(CV {reliability['cv_percent']:.1f}%) and is rated {reliability['rating'].lower()}."
                    ),
                }
            )

    morning_average = shift_summary["morning"].get("average_score")
    night_average = shift_summary["night"].get("average_score")
    if morning_average is not None and night_average is not None:
        difference = night_average - morning_average
        lower_shift = "Night Shift" if difference < 0 else "Morning Shift"
        insights.append(
            {
                "key": "shift",
                "title": "Shift Performance Gap",
                "text": f"{lower_shift} scored {abs(difference):.1f} points lower than the other shift.",
            }
        )

    stage_rows = []
    for sterilizer in active_sterilizers:
        for stage_key, stage_data in sterilizer["stage_performance"].items():
            if stage_data.get("average_score") is not None:
                stage_rows.append(
                    (
                        stage_data["average_score"],
                        sterilizer["sterilizer_name"],
                        stage_key.upper(),
                    )
                )
    if stage_rows:
        stage_score, sterilizer_name, stage_name = min(stage_rows)
        insights.append(
            {
                "key": "focus",
                "title": "Recommended Focus",
                "text": (
                    f"Review {sterilizer_name} {stage_name}, which has the lowest average stage score "
                    f"({stage_score:.1f}). Open Analysis Feedback for affected Fair, Poor, or Critical cycles before taking corrective action."
                ),
            }
        )

    return insights


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _cycle_incident(cycle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Create one row even when a cycle has both performance and data warnings."""

    band = _clean(cycle.get("score_band")).lower()
    quality = cycle.get("data_quality") or {}
    incident_types: List[str] = []
    details: List[str] = []

    if band in {"fair", "poor", "critical"}:
        incident_types.append("performance")
        stage = _clean(cycle.get("affected_stage")).upper()
        stage_text = f"; affected stage {stage}" if stage and stage != "NONE" else ""
        details.append(f"{band.title()} cycle performance{stage_text}")
    if quality.get("status") == "warning":
        incident_types.append("data_quality")
        details.extend(quality.get("issue_labels") or ["Data-quality warning"])
    if not incident_types:
        return None

    severity_order = {"critical": 4, "poor": 3, "fair": 2}
    severity = band if band in severity_order else "warning"
    return {
        "incident_id": (
            f"cycle:{cycle.get('field')}:{cycle.get('cycle_no')}:{cycle.get('cycle_end')}"
        ),
        "incident_kind": "cycle",
        "incident_types": incident_types,
        "severity": severity,
        "field": cycle.get("field"),
        "sterilizer_name": cycle.get("sterilizer_name"),
        "cycle_no": cycle.get("cycle_no"),
        "start_time": cycle.get("cycle_start"),
        "end_time": cycle.get("cycle_end"),
        "shift": cycle.get("shift"),
        "score": cycle.get("score"),
        "score_band": band,
        "data_quality_status": quality.get("status") or "valid",
        "details": details,
    }


def _availability_incidents(
    field_df: pd.DataFrame,
    operational_window: Dict[str, Any],
    *,
    field: str,
    sterilizer_name: str,
) -> List[Dict[str, Any]]:
    """Find conservative 30-minute periods with no stored pressure readings."""

    window_start = pd.Timestamp(operational_window["start"])
    window_end = pd.Timestamp(operational_window["end"])
    scoped = field_df[
        (field_df["time"] >= window_start) & (field_df["time"] < window_end)
    ].copy()
    scoped = scoped.sort_values("time").drop_duplicates(subset=["time"])

    if scoped.empty:
        gaps = [(window_start, window_end)]
    else:
        times = list(scoped["time"])
        gaps: List[Tuple[pd.Timestamp, pd.Timestamp]] = []
        if times[0] - window_start >= DATA_AVAILABILITY_GAP:
            gaps.append((window_start, times[0]))
        for previous, current in zip(times, times[1:]):
            if current - previous >= DATA_AVAILABILITY_GAP:
                gaps.append((previous, current))
        if window_end - times[-1] >= DATA_AVAILABILITY_GAP:
            gaps.append((times[-1], window_end))

    incidents = []
    for index, (gap_start, gap_end) in enumerate(gaps, start=1):
        start_value = gap_start.to_pydatetime()
        end_value = gap_end.to_pydatetime()
        shift_key = assign_shift_by_cycle_end(end_value, operational_window) or "multiple"
        duration_minutes = round((gap_end - gap_start).total_seconds() / 60.0, 1)
        incidents.append(
            {
                "incident_id": f"availability:{field}:{index}:{gap_start.isoformat()}",
                "incident_kind": "data_availability",
                "incident_types": ["data_availability"],
                "severity": "warning",
                "field": field,
                "sterilizer_name": sterilizer_name,
                "cycle_no": None,
                "start_time": _iso(start_value),
                "end_time": _iso(end_value),
                "shift": shift_key,
                "score": None,
                "score_band": None,
                "data_quality_status": "warning",
                "details": [
                    f"No stored pressure readings for approximately {duration_minutes:.1f} minutes"
                ],
            }
        )
    return incidents


def _incident_summary(incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "total_incidents": len(incidents),
        "performance_cycles": sum(
            1 for item in incidents if "performance" in item.get("incident_types", [])
        ),
        "data_quality_cycles": sum(
            1 for item in incidents if "data_quality" in item.get("incident_types", [])
        ),
        "data_availability_incidents": sum(
            1
            for item in incidents
            if "data_availability" in item.get("incident_types", [])
        ),
        "critical_cycles": sum(
            1 for item in incidents if item.get("score_band") == "critical"
        ),
    }


def _prepare_field_for_analysis(field_df: pd.DataFrame, smooth_window: int):
    if field_df.empty:
        raise ValueError("No pressure data was returned for the selected field.")
    value_col = "value_std" if "value_std" in field_df.columns else "value"
    return prepare_signal(field_df, smooth_window=smooth_window, value_col=value_col)


def _analyse_field_day(
    field_df: pd.DataFrame,
    operational_window: Dict[str, Any],
    benchmark: Dict[str, Any],
    field: str,
    sterilizer_name: str,
    smooth_window: int,
) -> List[Dict[str, Any]]:
    analysis_start = operational_window["start"] - CYCLE_DETECTION_LOOKBACK
    analysis_end = operational_window["end"] + timedelta(
        minutes=CYCLE_QUERY_PADDING_MINUTES
    )
    day_df = field_df[
        (field_df["time"] >= pd.Timestamp(analysis_start))
        & (field_df["time"] < pd.Timestamp(analysis_end))
    ].copy()
    if day_df.empty:
        return []

    day_df = day_df.sort_values("time").reset_index(drop=True)
    day_df, baseline_value, baseline_margin = _prepare_field_for_analysis(
        day_df, smooth_window
    )
    day_df, cycles_idx, _threshold = detect_full_cycles(
        day_df, baseline_value, baseline_margin
    )
    cycles_idx = refine_cycle_boundaries(
        day_df,
        cycles_idx,
        baseline_value,
        baseline_margin,
    )
    report_start = pd.Timestamp(operational_window["start"])
    report_end = pd.Timestamp(operational_window["end"])
    cycles_idx = [
        (start_index, end_index)
        for start_index, end_index in cycles_idx
        if report_start
        <= pd.Timestamp(day_df.iloc[end_index]["time"])
        < report_end
    ]
    if not cycles_idx:
        return []
    comparisons = compare_detected_cycles_to_benchmark(
        df=day_df,
        cycles_idx=cycles_idx,
        benchmark=benchmark,
        value_col="smooth",
    )

    compact_cycles = []
    for cycle in comparisons:
        cycle_end = pd.Timestamp(cycle["cycle_end"]).to_pydatetime()
        shift_key = assign_shift_by_cycle_end(cycle_end, operational_window)
        if shift_key:
            compact_cycles.append(
                _compact_cycle(cycle, field, sterilizer_name, shift_key)
            )
    for cycle_no, cycle in enumerate(compact_cycles, start=1):
        cycle["cycle_no"] = cycle_no
    return compact_cycles


def generate_daily_report(
    site_code: str,
    data_id: str,
    report_date_value: str,
    settings: Dict[str, Any],
    smooth_window: int = 9,
) -> Dict[str, Any]:
    """Generate one strict operational-day report for one plant under one site.

    A configurable lookback and completion padding are queried only to recover
    cycles that cross the report boundaries. A cycle is included only when its
    end time belongs to the selected operational day.
    """

    report_timezone_name = _clean(settings.get("timezone")) or PLANT_TIMEZONE
    report_timezone = get_plant_timezone(report_timezone_name)
    clean_site_code = _clean(site_code)
    clean_data_id = _clean(data_id)
    if not clean_site_code:
        raise ValueError("Please select a site.")
    if not clean_data_id:
        raise ValueError("Please select a plant.")
    derived_site_code = derive_site_code(clean_data_id)
    if derived_site_code != clean_site_code:
        raise ValueError(
            f"Plant {clean_data_id} belongs to site {derived_site_code}, not {clean_site_code}."
        )
    try:
        selected_date = date.fromisoformat(_clean(report_date_value))
    except ValueError as exc:
        raise ValueError("Report date must use YYYY-MM-DD.") from exc
    if smooth_window < 1:
        raise ValueError("Smooth Window must be greater than 0.")

    sterilizers = get_sterilizers_for_tag(clean_data_id, source_unit="bar")
    if not sterilizers:
        raise ValueError(f"No sterilizers were discovered for {clean_data_id}.")

    active_for_site = (settings.get("active_benchmarks") or {}).get(clean_data_id) or {}
    missing_fields = [
        item["field"] for item in sterilizers if not _clean(active_for_site.get(item["field"]))
    ]
    if missing_fields:
        missing_text = ", ".join(missing_fields)
        raise ValueError(
            f"Active benchmark is not configured for: {missing_text}. Open Settings and assign one active benchmark to every sterilizer."
        )

    benchmark_by_field = {}
    for item in sterilizers:
        field = item["field"]
        file_name = active_for_site[field]
        benchmark = load_benchmark(file_name)
        validate_benchmark_compatibility(
            benchmark,
            tag_id=clean_data_id,
            field=field,
            file_name=file_name,
        )
        benchmark_by_field[field] = benchmark

    site_shifts = get_site_shifts(settings, clean_site_code)
    current_window = build_operational_window(
        selected_date,
        site_shifts,
        report_timezone,
    )

    source = get_pressure_source("bar")
    fields = [item["field"] for item in sterilizers]
    query_start = current_window["start"] - CYCLE_DETECTION_LOOKBACK
    query_end = current_window["end"] + timedelta(
        minutes=CYCLE_QUERY_PADDING_MINUTES
    )
    all_data = fetch_multi_field_data(
        bucket=source["bucket"],
        measurement=source["measurement"],
        fields=fields,
        tag_id=clean_data_id,
        start_time=_iso(query_start),
        stop_time=_iso(query_end),
        source_unit_fallback="bar",
    )
    no_pressure_data_returned = all_data.empty
    if no_pressure_data_returned:
        # A no-data day is itself operationally important. Generate the report
        # with zero cycles and availability incidents instead of hiding the day
        # behind an error response.
        all_data = pd.DataFrame(columns=["time", "field"])

    current_cycles: List[Dict[str, Any]] = []
    current_cycles_by_field: Dict[str, List[Dict[str, Any]]] = {
        field: [] for field in fields
    }
    warnings = (
        ["No pressure readings were returned for this plant during the report period."]
        if no_pressure_data_returned
        else []
    )
    availability_incidents: List[Dict[str, Any]] = []

    for sterilizer in sterilizers:
        field = sterilizer["field"]
        sterilizer_name = sterilizer["sterilizer_name"]
        field_df = all_data[all_data["field"] == field].copy().reset_index(drop=True)
        availability_incidents.extend(
            _availability_incidents(
                field_df,
                current_window,
                field=field,
                sterilizer_name=sterilizer_name,
            )
        )
        if field_df.empty:
            warnings.append(f"No pressure data found for {sterilizer_name} ({field}).")
            continue
        day_cycles = _analyse_field_day(
            field_df=field_df,
            operational_window=current_window,
            benchmark=benchmark_by_field[field],
            field=field,
            sterilizer_name=sterilizer_name,
            smooth_window=smooth_window,
        )
        current_cycles.extend(day_cycles)
        current_cycles_by_field[field] = day_cycles

    current_key = selected_date.isoformat()
    current_summary = _summarise_cycles(current_cycles)

    sterilizer_summaries = []
    for sterilizer in sterilizers:
        field = sterilizer["field"]
        benchmark = benchmark_by_field[field]
        sterilizer_summaries.append(
            _summarise_sterilizer(
                field=field,
                sterilizer_name=sterilizer["sterilizer_name"],
                cycles=current_cycles_by_field[field],
                benchmark_file_name=active_for_site[field],
                benchmark_name=benchmark.get("benchmark_name") or active_for_site[field],
            )
        )

    shift_summary = {}
    for shift_key in ("morning", "night"):
        shift_cycles = [
            cycle for cycle in current_cycles if cycle.get("shift") == shift_key
        ]
        shift_summary[shift_key] = {
            "label": current_window[shift_key]["label"],
            "start": current_window[shift_key]["configured_start"],
            "end": current_window[shift_key]["configured_end"],
            **_summarise_cycles(shift_cycles),
        }

    site_name = get_site_display_name(settings, clean_site_code)
    plant_name = get_plant_display_name(settings, clean_data_id)
    insights = _build_insights(
        site_name=plant_name,
        current_summary=current_summary,
        sterilizers=sterilizer_summaries,
        shift_summary=shift_summary,
    )
    cycle_incidents = [
        incident for incident in (_cycle_incident(cycle) for cycle in current_cycles) if incident
    ]
    incident_history = sorted(
        cycle_incidents + availability_incidents,
        key=lambda item: (item.get("end_time") or item.get("start_time") or "", item.get("field") or ""),
    )
    incident_summary = _incident_summary(incident_history)
    if incident_summary["data_quality_cycles"]:
        warnings.append(
            f"{incident_summary['data_quality_cycles']} completed cycle(s) contain data-quality warnings. "
            "They remain included in every score and KPI as required."
        )
    if incident_summary["data_availability_incidents"]:
        warnings.append(
            f"{incident_summary['data_availability_incidents']} pressure data-availability gap(s) were detected."
        )

    return {
        "report_title": "Sterilizer Line Daily Insight Report",
        "report_date": current_key,
        "generated_at": _iso(datetime.now(report_timezone)),
        "timezone": report_timezone_name,
        "site": {
            "site_code": clean_site_code,
            "display_name": site_name,
        },
        "plant": {
            "display_name": plant_name,
            "data_id": clean_data_id,
        },
        "report_period": {
            "start": _iso(current_window["start"]),
            "end": _iso(current_window["end"]),
            "duration_hours": 24,
            "cycle_assignment_rule": "cycle_end_time",
            "detection_context": {
                "query_start": _iso(query_start),
                "query_end": _iso(query_end),
                "lookback_minutes": DAILY_REPORT_CYCLE_LOOKBACK_MINUTES,
                "completion_padding_minutes": CYCLE_QUERY_PADDING_MINUTES,
                "counting_rule": "Only cycles ending inside the report period are included.",
            },
        },
        "pressure_source": {
            "measurement": source["measurement"],
            "source_unit": source["source_unit"],
            "calculation_unit": "bar",
        },
        "summary": current_summary,
        "sterilizers": sterilizer_summaries,
        "shift_comparison": {
            **shift_summary,
            "night_minus_morning": {
                "total_cycles": shift_summary["night"]["total_cycles"]
                - shift_summary["morning"]["total_cycles"],
                "average_score": _difference(
                    shift_summary["night"]["average_score"],
                    shift_summary["morning"]["average_score"],
                ),
            },
        },
        "cycles": sorted(current_cycles, key=lambda cycle: cycle["cycle_end"]),
        "incident_history": incident_history,
        "incident_summary": incident_summary,
        "data_quality_policy": {
            "include_warned_cycles_in_all_calculations": True,
            "score_formula_changed": False,
            "warning_rule": "Warned cycles remain in totals, averages, bands, shifts, and reliability calculations.",
            "data_availability_gap_minutes": int(DATA_AVAILABILITY_GAP.total_seconds() / 60),
        },
        "insights": insights,
        "warnings": warnings,
    }
